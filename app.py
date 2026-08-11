import os
import re
import sys
import uuid
import subprocess
import threading
import json
import shutil
import glob
import time
import zipfile
import itertools
import asyncio
import httpx
from dotenv import load_dotenv
from typing import Dict, Optional, List
from contextlib import asynccontextmanager
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from starlette.background import BackgroundTask
from pydantic import BaseModel
from local_library import (
    MANIFEST_NAME,
    bootstrap_projects,
    ensure_project,
    history as local_history,
    list_projects as local_projects,
    load_project,
    project_clip,
    recover_job,
    remove_project,
    save_project_state,
    sync_clip_edit,
)

load_dotenv()

# The desktop build keeps mutable projects outside the application bundle.  The
# regular Python entry point still defaults to this repository, so local API
# development remains straightforward.
APP_ROOT = os.path.dirname(os.path.abspath(__file__))
RESOURCE_DIR = os.path.abspath(os.environ.get("OPENSHORTS_RESOURCE_DIR", APP_ROOT))
DATA_DIR = os.path.abspath(os.environ.get("OPENSHORTS_DATA_DIR", APP_ROOT))
UPLOAD_DIR = os.path.join(DATA_DIR, "uploads")
OUTPUT_DIR = os.path.join(DATA_DIR, "output")
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)

# Configuration
# Default to 1 if not set, but user can set higher for powerful servers
MAX_CONCURRENT_JOBS = int(os.environ.get("MAX_CONCURRENT_JOBS", "5"))
MAX_FILE_SIZE_MB = 2048  # 2GB limit

JOB_RETENTION_SECONDS = int(os.environ.get("JOB_RETENTION_SECONDS", "3600"))  # job/file retention (issue #46)
# Ceiling for the working directory once it lives on a persistent volume: the
# age-based sweep alone can't stop a burst of long videos from filling the disk.
# 0 disables the cap.
OUTPUT_MAX_GB = int(os.environ.get("OUTPUT_MAX_GB", "25"))
# Same idea for source uploads, which are the biggest single files on disk.
UPLOADS_MAX_GB = int(os.environ.get("UPLOADS_MAX_GB", "15"))
# Pre-flight quality gate: warn before processing a YouTube source below this
# height (0 disables). Only applies to URLs; uploads are whatever the user gave.
QUALITY_GATE_MIN_HEIGHT = int(os.environ.get("QUALITY_GATE_MIN_HEIGHT", "720"))
QUALITY_PROBE_SCRIPT = os.path.join(RESOURCE_DIR, "quality_probe.py")
DISABLE_YOUTUBE_URL = os.environ.get("DISABLE_YOUTUBE_URL", "false").lower() in ("1", "true", "yes")


def _pipeline_command() -> List[str]:
    """Return a command that works both from source and from a PyInstaller sidecar."""
    if os.environ.get("OPENSHORTS_FROZEN") == "1":
        return [sys.executable, "--worker"]
    return [sys.executable, "-u", os.path.join(RESOURCE_DIR, "main.py")]


def _quality_probe_command(url: str) -> List[str]:
    if os.environ.get("OPENSHORTS_FROZEN") == "1":
        return [sys.executable, "--quality-probe", "--url", url]
    return [sys.executable, QUALITY_PROBE_SCRIPT, "--url", url]

async def resolve_gemini(request: Request) -> Optional[str]:
    """Resolve a request's Gemini key, falling back to the server env key."""
    header = request.headers.get("X-Gemini-Key")
    return header or os.environ.get("GEMINI_API_KEY")


def gemini_missing_error():
    return HTTPException(status_code=400, detail="Missing X-Gemini-Key header")


# Application state is single-tenant and FIFO.
job_queue = asyncio.Queue()
_job_seq = itertools.count()
jobs: Dict[str, Dict] = {}
thumbnail_sessions: Dict[str, Dict] = {}
concurrency_semaphore = asyncio.Semaphore(MAX_CONCURRENT_JOBS)


def _enqueue_job(job_id: str):
    job_queue.put_nowait((next(_job_seq), job_id))

def _relocate_root_job_artifacts(job_id: str, job_output_dir: str) -> bool:
    """
    Backward-compat rescue:
    If main.py accidentally wrote metadata/clips into OUTPUT_DIR root (e.g. output/<jobid>_...),
    move them into output/<job_id>/ so the API can find and serve them.
    """
    try:
        os.makedirs(job_output_dir, exist_ok=True)
        root = OUTPUT_DIR
        pattern = os.path.join(root, f"{job_id}_*_metadata.json")
        meta_candidates = sorted(glob.glob(pattern), key=lambda p: os.path.getmtime(p), reverse=True)
        if not meta_candidates:
            return False

        # Move the newest metadata and its associated clips.
        metadata_path = meta_candidates[0]
        base_name = os.path.basename(metadata_path).replace("_metadata.json", "")

        # Move metadata
        dest_metadata = os.path.join(job_output_dir, os.path.basename(metadata_path))
        if os.path.abspath(metadata_path) != os.path.abspath(dest_metadata):
            shutil.move(metadata_path, dest_metadata)

        # Move any clips that match the same base_name into the job folder
        clip_pattern = os.path.join(root, f"{base_name}_clip_*.mp4")
        for clip_path in glob.glob(clip_pattern):
            dest_clip = os.path.join(job_output_dir, os.path.basename(clip_path))
            if os.path.abspath(clip_path) != os.path.abspath(dest_clip):
                shutil.move(clip_path, dest_clip)

        # Also move any temp_ clips that might remain
        temp_clip_pattern = os.path.join(root, f"temp_{base_name}_clip_*.mp4")
        for clip_path in glob.glob(temp_clip_pattern):
            dest_clip = os.path.join(job_output_dir, os.path.basename(clip_path))
            if os.path.abspath(clip_path) != os.path.abspath(dest_clip):
                shutil.move(clip_path, dest_clip)

        return True
    except Exception:
        return False

def _canonical_clip_file(output_dir, base_name, index):
    """The file to serve for clip ``index``, preferring a derived version.

    The pipeline writes the clean reframe as ``<base>_clip_<n>.mp4`` and any
    post-processing (optional auto-captions, and /api/subtitle re-styles) as
    ``subtitled_<ts>_<clean>.mp4``, keeping the original for re-styling. Every
    place that rebuilds the canonical name from disk — restore after a restart,
    the local library and the download bundle — must therefore resolve to the
    newest derived file, or clips silently lose their captions on a redeploy.
    """
    clean = f"{base_name}_clip_{index + 1}.mp4"
    try:
        derived = glob.glob(os.path.join(output_dir, f"subtitled_*_{clean}"))
    except Exception:
        derived = []
    if not derived:
        return clean
    # Highest timestamp wins — that's the most recent styling.
    return os.path.basename(max(derived, key=os.path.getmtime))


def _strip_burned_captions(output_dir, filename):
    """Walk ``subtitled_<ts>_`` prefixes back to the file without burned captions.

    Returns the name unchanged when there is nothing to strip (or when the
    underlying file is gone, e.g. a library restore that only kept the current
    version).
    """
    while True:
        m = re.match(r'^subtitled_\d+_(.+)$', filename)
        if not m or not os.path.exists(os.path.join(output_dir, m.group(1))):
            return filename
        filename = m.group(1)


def _reapply_captions(job_id, clip_index, video_path):
    """Re-burn an existing caption layer onto a freshly derived file.

    Captions must always be the LAST layer. Editing or hooking a clip that
    already had them burned in produced `edited_subtitled_<...>`, and the next
    subtitle pass then stacked a second caption layer on top of the first —
    visibly doubled and unreadable in real user clips (26-jul-2026). So the
    derivation runs on the clean file and captions go back on afterwards.

    Returns the captioned path, or None if there was nothing to caption.
    """
    try:
        meta_files = glob.glob(os.path.join(OUTPUT_DIR, job_id, "*_metadata.json"))
        if not meta_files:
            return None
        with open(meta_files[0], 'r') as f:
            data = json.load(f)
        transcript = data.get('transcript')
        clips = data.get('shorts', [])
        if not transcript or clip_index >= len(clips):
            return None
        clip = clips[clip_index]
        import main as _main
        return _main.auto_caption_clip(video_path, transcript,
                                       clip['start'], clip['end'], force=True)
    except Exception as e:
        print(f"⚠️  Could not re-apply captions to {video_path}: {e}")
        return None


def _recover_jobs_from_disk():
    """Rebuild the in-memory index from durable local project manifests."""
    recovered = 0
    bootstrap_projects(OUTPUT_DIR)
    for project in local_projects(OUTPUT_DIR):
        job_id = project["job_id"]
        if job_id in jobs:
            continue
        job_path = os.path.join(OUTPUT_DIR, job_id)
        try:
            recovered_job = recover_job(OUTPUT_DIR, job_id)
            if recovered_job:
                jobs[job_id] = {
                    "status": "completed",
                    "logs": ["♻️ Job recovered from local storage."],
                    "output_dir": job_path,
                    "result": {
                        "clips": recovered_job["clips"],
                        "cost_analysis": recovered_job["metadata"].get("cost_analysis"),
                    },
                    "project": recovered_job["manifest"],
                }
                recovered += 1
        except Exception as e:
            print(f"⚠️ Could not recover job {job_id}: {e}")
    if recovered:
        print(f"♻️  Recovered {recovered} completed job(s) from disk.")


# --- Mid-flight job resume (survive an app restart without losing work) -----
# A job lives only in memory, so stopping the backend mid-processing used to
# lose it: the user's clip just stops. We persist a tiny manifest per job and,
# on startup, re-enqueue any that were interrupted — the user sees it resume
# instead of vanish. Bounded by MAX_RESUME_ATTEMPTS so a video that reliably
# crashes the worker can't crashloop the service.
_RESUME_FILE = ".resume.json"
MAX_RESUME_ATTEMPTS = 2


def _write_resume_manifest(job_id, cmd, webhook_url=None, webhook_secret=None, base_url=None):
    try:
        path = os.path.join(OUTPUT_DIR, job_id, _RESUME_FILE)
        with open(path, "w") as f:
            json.dump({
                "cmd": cmd, "attempts": 0,
                # The caller's webhook must survive a redeploy: a pipeline that
                # relies on the callback would otherwise hang forever on a job
                # that resumed fine. The secret is the caller's own HMAC value,
                # stored next to their video on the same disk — not a server
                # credential (those are rebuilt from os.environ on resume).
                "webhook_url": webhook_url,
                "webhook_secret": webhook_secret,
                "base_url": base_url,
            }, f)
    except Exception as e:
        print(f"⚠️ Could not write resume manifest for {job_id}: {e}")


def _clear_resume_manifest(job_id):
    """Drop the manifest once a job reaches a terminal state, so it is never
    re-run on a later restart. Only an interrupted (still-running) job keeps it."""
    try:
        os.remove(os.path.join(OUTPUT_DIR, job_id, _RESUME_FILE))
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"⚠️ Could not clear resume manifest for {job_id}: {e}")


def _resume_interrupted_jobs() -> set:
    """Re-enqueue jobs that were mid-processing when the server last stopped.

    Runs after _recover_jobs_from_disk: a job whose clips already finished has a
    metadata JSON and is recovered as 'completed', so we only resume manifests
    with no metadata yet (analysis never finished).

    Resume manifests contain only local process state and caller webhook data.
    """
    try:
        entries = os.listdir(OUTPUT_DIR)
    except FileNotFoundError:
        return None
    resumed = 0
    for job_id in entries:
        job_path = os.path.join(OUTPUT_DIR, job_id)
        manifest_path = os.path.join(job_path, _RESUME_FILE)
        if not os.path.isfile(manifest_path):
            continue
        # Already finished generating clips → recovered as completed elsewhere.
        if glob.glob(os.path.join(job_path, "*_metadata.json")):
            _clear_resume_manifest(job_id)
            continue
        try:
            with open(manifest_path) as f:
                m = json.load(f)
        except Exception as e:
            print(f"⚠️ Bad resume manifest for {job_id}: {e}")
            continue

        attempts = int(m.get("attempts", 0)) + 1
        if attempts > MAX_RESUME_ATTEMPTS:
            print(f"🛑 Job {job_id} exceeded {MAX_RESUME_ATTEMPTS} resume attempts — giving up.")
            _clear_resume_manifest(job_id)
            continue

        # Rebuild env from scratch; API keys are supplied by the local process
        # configuration and are never persisted in the manifest.
        env = os.environ.copy()

        m["attempts"] = attempts
        try:
            with open(manifest_path, "w") as f:
                json.dump(m, f)
        except Exception:
            pass

        jobs[job_id] = {
            'status': 'queued',
            'logs': [f"♻️ Resuming your video after a server update (attempt {attempts})."],
            'cmd': m.get("cmd"),
            'env': env,
            'output_dir': job_path,
            'webhook_url': m.get("webhook_url"),
            'webhook_secret': m.get("webhook_secret"),
            'base_url': m.get("base_url"),
        }
        _enqueue_job(job_id)
        resumed += 1
    if resumed:
        print(f"♻️  Re-enqueued {resumed} interrupted job(s) after restart.")
    return None


def _dir_size(path: str) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def _enforce_uploads_size_cap():
    """Delete the oldest source uploads while UPLOAD_DIR is over UPLOADS_MAX_GB.

    Sources are only needed while a job runs (and for the preview afterwards),
    but they're the biggest files on disk — up to MAX_FILE_SIZE_MB each.
    """
    cap = UPLOADS_MAX_GB * 1024 ** 3
    if cap <= 0:
        return
    used = _dir_size(UPLOAD_DIR)
    if used <= cap:
        return
    files = []
    for name in os.listdir(UPLOAD_DIR):
        p = os.path.join(UPLOAD_DIR, name)
        if os.path.isfile(p):
            try:
                files.append((os.path.getmtime(p), p, os.path.getsize(p)))
            except OSError:
                pass
    files.sort()
    print(f"🧹 Uploads at {used / 1024**3:.1f} GB (cap {UPLOADS_MAX_GB} GB) — trimming.")
    for _mtime, path, size in files:
        if used <= cap:
            break
        try:
            os.remove(path)
            used -= size
            print(f"🧹 Size cap: removed upload {os.path.basename(path)}")
        except OSError:
            pass


def _enforce_output_size_cap():
    """Trim transient output while preserving durable projects."""
    cap = OUTPUT_MAX_GB * 1024 ** 3
    if cap <= 0:
        return
    used = sum(_dir_size(os.path.join(OUTPUT_DIR, name))
               for name in os.listdir(OUTPUT_DIR)
               if name != "thumbnails"
               and not os.path.exists(os.path.join(OUTPUT_DIR, name, MANIFEST_NAME)))
    if used <= cap:
        return
    candidates = []
    for job_id in os.listdir(OUTPUT_DIR):
        if job_id == "thumbnails":
            continue
        p = os.path.join(OUTPUT_DIR, job_id)
        if os.path.isdir(p) and not os.path.exists(os.path.join(p, MANIFEST_NAME)):
            try:
                candidates.append((os.path.getmtime(p), p, job_id))
            except OSError:
                pass
    candidates.sort()  # oldest first
    print(f"🧹 Output dir at {used / 1024**3:.1f} GB (cap {OUTPUT_MAX_GB} GB) — trimming.")
    for _mtime, path, job_id in candidates:
        if used <= cap:
            break
        size = _dir_size(path)
        shutil.rmtree(path, ignore_errors=True)
        jobs.pop(job_id, None)
        used -= size
        print(f"🧹 Size cap: purged {job_id} ({size / 1024**2:.0f} MB)")


async def cleanup_jobs():
    """Background task to remove old jobs and files."""
    import time
    print("🧹 Cleanup task started.")
    while True:
        try:
            await asyncio.sleep(300) # Check every 5 minutes
            now = time.time()
            
            # Durable projects are never expired. Only transient work
            # directories are subject to this sweep.
            for job_id in os.listdir(OUTPUT_DIR):
                if job_id == "thumbnails":
                    continue
                job_path = os.path.join(OUTPUT_DIR, job_id)
                if os.path.isdir(job_path) and not os.path.exists(os.path.join(job_path, MANIFEST_NAME)):
                    if now - os.path.getmtime(job_path) > JOB_RETENTION_SECONDS:
                        print(f"🧹 Purging old job: {job_id}")
                        shutil.rmtree(job_path, ignore_errors=True)
                        if job_id in jobs:
                            del jobs[job_id]

            # Hard disk cap for transient work only. Durable projects are
            # explicitly deleted by the operator through the API/UI.
            _enforce_output_size_cap()
            _enforce_uploads_size_cap()

            # Cleanup Uploads
            for filename in os.listdir(UPLOAD_DIR):
                file_path = os.path.join(UPLOAD_DIR, filename)
                try:
                    if now - os.path.getmtime(file_path) > JOB_RETENTION_SECONDS:
                         os.remove(file_path)
                except Exception: pass

        except Exception as e:
            print(f"⚠️ Cleanup error: {e}")

async def process_queue():
    """Background worker to process jobs from the queue with concurrency limit."""
    print(f"🚀 Job Queue Worker started with {MAX_CONCURRENT_JOBS} concurrent slots.")
    while True:
        try:
            # Wait for the next FIFO job.
            _seq, job_id = await job_queue.get()

            # Acquire semaphore slot (waits if max jobs are running)
            await concurrency_semaphore.acquire()
            print(f"🔄 Acquired slot for job: {job_id}")

            # Process in background task to not block the loop (allowing other slots to fill)
            asyncio.create_task(run_job_wrapper(job_id))
            
        except Exception as e:
            print(f"❌ Queue dispatch error: {e}")
            await asyncio.sleep(1)

async def run_job_wrapper(job_id):
    """Wrapper to run job and release semaphore"""
    try:
        job = jobs.get(job_id)
        if job:
            await run_job(job_id, job)
    except Exception as e:
         print(f"❌ Job wrapper error {job_id}: {e}")
    finally:
        # The subprocess returned (success or genuine failure) — a terminal
        # state, so drop the resume manifest. It only survives if the backend
        # was stopped mid-run, which is exactly when we want to resume.
        _clear_resume_manifest(job_id)
        job = jobs.get(job_id) or {}
        if job.get("status") == "completed":
            ensure_project(OUTPUT_DIR, job_id, (job.get("result") or {}).get("clips") or [])
        # Fire the caller's webhook after the local manifest is durable.
        await _notify_job_webhook(job_id)
        # Always release semaphore and mark queue task done
        concurrency_semaphore.release()
        job_queue.task_done()
        print(f"✅ Released slot for job: {job_id}")


def _archive_clip_edit_bg(job_id: str, clip_index: int, filename: str):
    sync_clip_edit(OUTPUT_DIR, job_id, clip_index, filename)


# Markers that identify a line as an actual error rather than progress noise.
_ERROR_MARKERS = ("❌", "ERROR:", "Traceback", "FATAL", "Exception",
                  "Process failed with exit code", "No metadata file generated",
                  "Execution error:")


def _job_error_text(logs) -> str:
    """The lines that explain WHY a job failed, for the alert's classifier.

    The tail of the log is usually progress noise (scene detection, ffmpeg
    banners), which made alerts blame whatever word happened to be nearby —
    a silent upload got reported as a broken download path, and a Gemini blip
    as an ffmpeg problem. Pick the error-bearing lines instead, newest last.
    """
    hits = [ln for ln in logs if any(m in ln for m in _ERROR_MARKERS)]
    if not hits:
        return " ".join(logs[-10:])  # nothing recognisable — fall back to the tail
    return " ".join(hits[-6:])

# --- Job completion webhooks --------------------------------------------------
# Agents and pipelines (n8n, cron, MCP clients) need push, not poll: a caller
# passes webhook_url on /api/process and gets one POST when the job reaches a
# terminal state. The URL goes through assert_public_url both at submit and at
# delivery time — the second check is what defeats DNS rebinding between them.
WEBHOOK_TIMEOUT = 10.0
WEBHOOK_RETRY_DELAYS = (0, 10, 60)  # seconds before each attempt


def _sign_webhook(body: bytes, secret: str) -> str:
    import hmac as _hmac
    import hashlib as _hashlib
    return "sha256=" + _hmac.new(secret.encode(), body, _hashlib.sha256).hexdigest()


async def _webhook_clip_entries(job_id, job):
    """Return local absolute URLs and download endpoints for each clip."""
    base = (job.get('base_url') or os.environ.get("PUBLIC_API_URL", "")).rstrip("/")
    clips = (job.get('result') or {}).get('clips') or []
    entries = []
    for i, clip in enumerate(clips):
        rel = clip.get('video_url') or ""
        entries.append({
            "index": i,
            "title": clip.get('title') or clip.get('video_title_for_youtube_short'),
            "video_url": f"{base}{rel}" if rel.startswith("/") and base else rel,
            "download_url": f"{base}/api/projects/{job_id}/clips/{i}/download" if base else f"/api/projects/{job_id}/clips/{i}/download",
        })
    return entries


async def _deliver_webhook(url, body: bytes, secret):
    headers = {"Content-Type": "application/json", "User-Agent": "OpenShorts-Webhook/1.0"}
    if secret:
        headers["X-OpenShorts-Signature"] = _sign_webhook(body, secret)
    from security_utils import assert_public_url, UnsafeURLError
    loop = asyncio.get_event_loop()
    for attempt, delay in enumerate(WEBHOOK_RETRY_DELAYS, 1):
        if delay:
            await asyncio.sleep(delay)
        try:
            # Re-resolve on every attempt: the submit-time check is stale by now.
            await loop.run_in_executor(None, assert_public_url, url)
            async with httpx.AsyncClient(timeout=WEBHOOK_TIMEOUT,
                                         follow_redirects=False) as client:
                resp = await client.post(url, content=body, headers=headers)
            if resp.status_code < 300:
                print(f"🪝 Webhook delivered to {url} (attempt {attempt})")
                return
            print(f"⚠️ Webhook attempt {attempt} to {url}: HTTP {resp.status_code}")
        except UnsafeURLError as e:
            print(f"🛑 Webhook URL no longer safe, dropping: {e}")
            return
        except Exception as e:
            print(f"⚠️ Webhook attempt {attempt} to {url} failed: {e}")
    print(f"❌ Webhook to {url} gave up after {len(WEBHOOK_RETRY_DELAYS)} attempts.")


async def _notify_job_webhook(job_id):
    """Fire the caller's webhook for a terminal job."""
    job = jobs.get(job_id) or {}
    url = job.get('webhook_url')
    if not url or job.get('webhook_sent'):
        return
    job['webhook_sent'] = True
    completed = job.get('status') == 'completed'
    payload = {
        "event": "job.completed" if completed else "job.failed",
        "job_id": job_id,
        "status": job.get('status'),
        "clips": (await _webhook_clip_entries(job_id, job)) if completed else [],
    }
    if not completed:
        payload["error"] = _job_error_text(job.get('logs', []))[-500:]
    body = json.dumps(payload).encode()
    asyncio.create_task(_deliver_webhook(url, body, job.get('webhook_secret')))


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Rehydrate finished jobs from disk before serving (survives restarts).
    _recover_jobs_from_disk()
    # Re-enqueue jobs that were mid-processing when we stopped.
    _resume_interrupted_jobs()
    # Start worker and cleanup
    worker_task = asyncio.create_task(process_queue())
    cleanup_task = asyncio.create_task(cleanup_jobs())
    yield
    # Cleanup (optional: cancel worker)

app = FastAPI(lifespan=lifespan)

# MCP server (/mcp): the pipeline as agent-callable tools.
import mcp_server as _mcp_server
app.include_router(_mcp_server.router)

# The desktop webview and Vite development server are the only browser clients
# allowed to call the loopback API. Additional local origins can be opted into
# explicitly for integrations that need them.
_configured_origins = [
    origin.strip()
    for origin in os.environ.get("OPENSHORTS_ALLOWED_ORIGINS", "").split(",")
    if origin.strip()
]
LOCAL_APP_ORIGINS = _configured_origins or [
    "tauri://localhost",
    "http://tauri.localhost",
    "https://tauri.localhost",
    "http://127.0.0.1:1420",
    "http://localhost:1420",
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=LOCAL_APP_ORIGINS,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount static files for serving videos
app.mount("/videos", StaticFiles(directory=OUTPUT_DIR), name="videos")

# Mount static files for serving thumbnails
THUMBNAILS_DIR = os.path.join(OUTPUT_DIR, "thumbnails")
os.makedirs(THUMBNAILS_DIR, exist_ok=True)
app.mount("/thumbnails", StaticFiles(directory=THUMBNAILS_DIR), name="thumbnails")


def _safe_under(base_dir: str, user_rel_path: str) -> Optional[str]:
    """Resolve ``user_rel_path`` under ``base_dir`` and reject path traversal.

    Returns the absolute path only if it stays inside ``base_dir`` (after
    following ``..``); otherwise None. Used to sanitize client-supplied file
    references so ``../../.env`` can't escape the output directories.
    """
    base = os.path.realpath(base_dir)
    target = os.path.realpath(os.path.join(base, user_rel_path))
    if target == base or target.startswith(base + os.sep):
        return target
    return None

class ProcessRequest(BaseModel):
    url: str

# Masks user:password credentials embedded in any URL (e.g. the residential
# proxy URL that yt-dlp echoes in its verbose debug output) before the line is
# ever printed to the server console or stored in the job log.
_CREDENTIAL_URL_RE = re.compile(r'(\w+://)[^:/@\s]+:[^@/\s]+@')


def _scrub_secrets(line: str) -> str:
    return _CREDENTIAL_URL_RE.sub(r'\1***:***@', line)


def _visible_logs(logs):
    """Return complete logs for local troubleshooting."""
    return logs


def enqueue_output(out, job_id):
    """Reads output from a subprocess and appends it to jobs logs."""
    try:
        for line in iter(out.readline, b''):
            decoded_line = _scrub_secrets(line.decode('utf-8').strip())
            if decoded_line:
                print(f"📝 [Job Output] {decoded_line}")
                if job_id in jobs:
                    jobs[job_id]['logs'].append(decoded_line)
    except Exception as e:
        print(f"Error reading output for job {job_id}: {e}")
    finally:
        out.close()

async def run_job(job_id, job_data):
    """Executes the subprocess for a specific job."""
    
    cmd = job_data['cmd']
    env = job_data['env']
    output_dir = job_data['output_dir']
    
    jobs[job_id]['status'] = 'processing'
    jobs[job_id]['logs'].append("Job started by worker.")
    print(f"🎬 [run_job] Executing command for {job_id}: {' '.join(cmd)}")
    
    try:
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, # Merge stderr to stdout
            env=env,
            cwd=RESOURCE_DIR,
        )
        
        # We need to capture logs in a thread because Popen isn't async
        t_log = threading.Thread(target=enqueue_output, args=(process.stdout, job_id))
        t_log.daemon = True
        t_log.start()
        
        # Async wait for process with incremental updates
        start_wait = time.time()
        while process.poll() is None:
            await asyncio.sleep(2)
            
            # Check for partial results every 2 seconds
            # Look for metadata file
            try:
                json_files = glob.glob(os.path.join(output_dir, "*_metadata.json"))
                if json_files:
                    target_json = json_files[0]
                    # Read metadata (it might be being written to, so simple try/except or just read)
                    # Use a lock or just robust read? json.load might fail if file is partial.
                    # Usually main.py writes it once at start (based on my review).
                    if os.path.getsize(target_json) > 0:
                        with open(target_json, 'r') as f:
                            data = json.load(f)
                            
                        base_name = os.path.basename(target_json).replace('_metadata.json', '')
                        clips = data.get('shorts', [])
                        cost_analysis = data.get('cost_analysis')
                        
                        # Check which clips actually exist on disk
                        ready_clips = []
                        for i, clip in enumerate(clips):
                             clip_filename = f"{base_name}_clip_{i+1}.mp4"
                             clip_path = os.path.join(output_dir, clip_filename)
                             if os.path.exists(clip_path) and os.path.getsize(clip_path) > 0:
                                 # Checking if file is growing? For now assume if it exists and main.py moves it there, it's done.
                                 # main.py writes to temp_... then moves to final name. So presence means ready!
                                 clip['video_url'] = f"/videos/{job_id}/{clip_filename}"
                                 ready_clips.append(clip)
                        
                        if ready_clips:
                             jobs[job_id]['result'] = {'clips': ready_clips, 'cost_analysis': cost_analysis}
            except Exception as e:
                # Ignore read errors during processing
                pass

        returncode = process.returncode
        
        if returncode == 0:
            jobs[job_id]['status'] = 'completed'
            jobs[job_id]['logs'].append("Process finished successfully.")
            
            # Find result JSON
            json_files = glob.glob(os.path.join(output_dir, "*_metadata.json"))
            if not json_files:
                # Backward-compat rescue if outputs were written to OUTPUT_DIR root
                if _relocate_root_job_artifacts(job_id, output_dir):
                    json_files = glob.glob(os.path.join(output_dir, "*_metadata.json"))
            if json_files:
                target_json = json_files[0] 
                with open(target_json, 'r') as f:
                    data = json.load(f)
                
                # Enhance result with video URLs
                base_name = os.path.basename(target_json).replace('_metadata.json', '')
                clips = data.get('shorts', [])
                cost_analysis = data.get('cost_analysis')

                for i, clip in enumerate(clips):
                     clip_filename = _canonical_clip_file(output_dir, base_name, i)
                     clip['video_url'] = f"/videos/{job_id}/{clip_filename}"
                
                jobs[job_id]['result'] = {'clips': clips, 'cost_analysis': cost_analysis}
            else:
                 jobs[job_id]['status'] = 'failed'
                 jobs[job_id]['logs'].append("No metadata file generated.")
        else:
            jobs[job_id]['status'] = 'failed'
            jobs[job_id]['logs'].append(_scrub_secrets(f"Process failed with exit code {returncode}"))
            
    except Exception as e:
        jobs[job_id]['status'] = 'failed'
        # Exception text can embed URLs with credentials (e.g. the proxy URL
        # inside a yt-dlp/httpx error) — scrub before it reaches client logs.
        jobs[job_id]['logs'].append(_scrub_secrets(f"Execution error: {str(e)}"))

@app.get("/health")
async def health():
    """Lightweight liveness probe for local startup checks."""
    return {"status": "ok"}

@app.get("/api/config")
async def get_config():
    return {
        "youtubeUrlEnabled": not DISABLE_YOUTUBE_URL,
    }


@app.get("/api/projects")
async def get_projects(limit: int = 200):
    """List durable projects stored on this computer."""
    return {"projects": local_projects(OUTPUT_DIR, limit=max(1, min(limit, 500)))}


@app.get("/api/history")
async def get_history(limit: int = 500):
    """List current clip versions from the local project library."""
    return {"videos": local_history(OUTPUT_DIR, limit=max(1, min(limit, 1000)))}


@app.get("/api/projects/{job_id}")
async def get_project(job_id: str):
    loaded = load_project(OUTPUT_DIR, job_id)
    if not loaded:
        raise HTTPException(status_code=404, detail="Project not found")
    _job_dir, manifest = loaded
    return manifest


@app.put("/api/projects/{job_id}/state")
async def update_project_state(job_id: str, request: Request):
    loaded = load_project(OUTPUT_DIR, job_id)
    if not loaded:
        raise HTTPException(status_code=404, detail="Project not found")
    body = await request.json()
    clips = body.get("clips") if isinstance(body, dict) else None
    if not isinstance(clips, list):
        raise HTTPException(status_code=400, detail="clips must be a list")
    if not save_project_state(OUTPUT_DIR, job_id, clips):
        raise HTTPException(status_code=404, detail="Project not found")
    return load_project(OUTPUT_DIR, job_id)[1]


@app.get("/api/projects/{job_id}/clips/{clip_index}/download")
async def download_project_clip(job_id: str, clip_index: int):
    found = project_clip(OUTPUT_DIR, job_id, clip_index)
    if not found:
        raise HTTPException(status_code=404, detail="Clip not found")
    _job_dir, clip, path = found
    return FileResponse(path, media_type="video/mp4", filename=os.path.basename(path))


@app.delete("/api/projects/{job_id}")
async def delete_project(job_id: str, confirm: bool = False):
    """Delete a durable project only after an explicit client confirmation."""
    if not confirm:
        raise HTTPException(status_code=400, detail="Pass confirm=true to delete this project")
    if not remove_project(OUTPUT_DIR, job_id):
        raise HTTPException(status_code=404, detail="Project not found")
    jobs.pop(job_id, None)
    return {"deleted": True, "job_id": job_id}


async def _probe_youtube_quality(url: str) -> dict:
    """Run quality_probe.py in a worker thread; {} on any failure (fail-open)."""
    def _run():
        try:
            proc = subprocess.run(
                _quality_probe_command(url),
                capture_output=True,
                timeout=75,
                cwd=RESOURCE_DIR,
            )
            return json.loads(proc.stdout.decode(errors="replace").strip() or "{}")
        except Exception as e:
            print(f"⚠️ Quality probe failed ({e}); starting job without gate.")
            return {}

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _run)


# Layouts the caller can let the renderer choose from, mapped to the env var
# each one is gated on. The renderer only ever picks between layouts that are
# switched on here.
#
# This is opt-in per job, not a detector running on every video, because the
# detection is not good enough to be trusted unprompted: measured over the
# 48-clip corpus, routing every video through the on-screen-content check fixed
# 13 clips and spoiled 13 others (talking heads and corner tickers demoted to a
# layout they do not need). Asking the person who knows what they uploaded costs
# them one click and removes that whole class of error. It is also what OpusClip
# does — its "applicable auto layout" panel lets the user pick which layouts the
# AI may apply.
LAYOUT_ENV = {
    "split": "SPLIT_LAYOUT",          # two speakers stacked
    "screencast": "SCREENCAST_LAYOUT",  # slides/screen share over the speaker
    "speaker_cut": "SPEAKER_CUT",     # hard cuts to whoever is talking
    "punch_in": "PUNCH_IN",           # small push on the clip's beats
}

# Stacking and cutting both need to know who is speaking.
LAYOUT_IMPLIES = {
    "split": ["SPEAKER_SIGNAL"],
    "speaker_cut": ["SPEAKER_SIGNAL"],
}


def layout_env(requested):
    """Env overrides for the layouts this job allows. Unknown names are ignored
    rather than rejected: a newer dashboard must not break an older API.

    The special value "auto" hands the choice to Gemini (one call per video).
    It composes with explicit picks: layout_picker only ever adds, so asking for
    "auto,punch_in" means "decide the layout yourself, and punch in regardless".
    """
    env = {}
    for name in requested or []:
        key = str(name).strip().lower()
        if key == "auto":
            env["AUTO_LAYOUT"] = "1"
            continue
        var = LAYOUT_ENV.get(key)
        if not var:
            continue
        env[var] = "1"
        for extra in LAYOUT_IMPLIES.get(key, []):
            env[extra] = "1"
    return env


@app.post("/api/process")
async def process_endpoint(
    request: Request,
    file: Optional[UploadFile] = File(None),
    url: Optional[str] = Form(None),
    acknowledged: Optional[str] = Form(None),
    output_format: Optional[str] = Form(None),
    layouts: Optional[str] = Form(None),
    force_low_quality: Optional[str] = Form(None),
    webhook_url: Optional[str] = Form(None),
    webhook_secret: Optional[str] = Form(None)
):
    api_key = await resolve_gemini(request)
    if not api_key:
        raise gemini_missing_error()

    ack_flag = str(acknowledged).lower() in ("1", "true", "yes")
    force_low = str(force_low_quality).lower() in ("1", "true", "yes")

    # Handle JSON body manually for URL payload
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        body = await request.json()
        url = body.get("url")
        ack_flag = bool(body.get("acknowledged"))
        force_low = bool(body.get("force_low_quality"))
        output_format = body.get("output_format")
        layouts = body.get("layouts")
        webhook_url = body.get("webhook_url")
        webhook_secret = body.get("webhook_secret")

    # Normalize output format (auto = keep pipeline default).
    if output_format not in ("vertical", "horizontal", "square"):
        output_format = "auto"

    # Accepts a JSON list or a comma-separated form field.
    if isinstance(layouts, str):
        layouts = [p for p in layouts.split(",") if p.strip()]
    elif not isinstance(layouts, list):
        layouts = []

    if not url and not file:
        raise HTTPException(status_code=400, detail="Must provide URL or File")

    # Completion callback: reject unsafe targets NOW (clear 400) — delivery
    # re-validates anyway, but failing at submit is the debuggable behavior.
    if webhook_url:
        from security_utils import assert_public_url, UnsafeURLError
        try:
            await asyncio.get_event_loop().run_in_executor(
                None, assert_public_url, webhook_url)
        except UnsafeURLError as e:
            raise HTTPException(status_code=400, detail=f"Invalid webhook_url: {e}")

    if not ack_flag:
        raise HTTPException(status_code=400, detail="You must confirm you own the content or have rights to process it.")

    if url and DISABLE_YOUTUBE_URL:
        raise HTTPException(status_code=403, detail="YouTube URL ingest is disabled locally. Please upload a file you own.")

    # Pre-flight quality gate: probe the offered resolution before starting so
    # the user can refresh cookies or update yt-dlp instead of processing a
    # low-resolution source. Fail-open: any probe error starts normally.
    if url and not force_low and QUALITY_GATE_MIN_HEIGHT > 0:
        probe = await _probe_youtube_quality(url)
        max_height = int(probe.get("max_height") or 0)
        if 0 < max_height < QUALITY_GATE_MIN_HEIGHT:
            print(f"⚠️ Quality gate: only {max_height}p available for {url} — asking user first.")
            return JSONResponse({
                "needs_confirmation": True,
                "quality_check": {
                    "max_height": max_height,
                    "min_height": QUALITY_GATE_MIN_HEIGHT,
                    "cookies_invalid": bool(probe.get("cookies_invalid")),
                },
            })

    # Capture attestation context for legal record (IP + timestamp + UA)
    client_ip = request.client.host if request.client else "unknown"
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        client_ip = fwd.split(",")[0].strip()
    user_agent = request.headers.get("user-agent", "")
    attestation = {
        "acknowledged": True,
        "ip": client_ip,
        "user_agent": user_agent,
        "timestamp": time.time(),
        "source": "url" if url else "file",
    }

    job_id = str(uuid.uuid4())
    job_output_dir = os.path.join(OUTPUT_DIR, job_id)
    os.makedirs(job_output_dir, exist_ok=True)

    # Prepare Command. The packaged desktop backend re-enters its sidecar in
    # worker mode; source development launches main.py with the active Python.
    cmd = _pipeline_command()
    env = os.environ.copy()
    env["GEMINI_API_KEY"] = api_key # Override with key from request

    # Optional layouts are per job. The renderer reads these at import time in
    # the subprocess, so they must be set before Popen.
    chosen = layout_env(layouts)
    env.update(chosen)
    if chosen:
        print(f"[layouts] job={job_id} enabled={sorted(chosen)}")

    input_path = None
    if url:
        cmd.extend(["-u", url])
    else:
        # Save uploaded file with size limit check.
        # basename() strips any path components from the client-supplied
        # filename so a name like "../../main.py" can't escape UPLOAD_DIR.
        safe_name = os.path.basename(file.filename or "upload") or "upload"
        input_path = os.path.join(UPLOAD_DIR, f"{job_id}_{safe_name}")

        # Read file in chunks to check size
        size = 0
        limit_bytes = MAX_FILE_SIZE_MB * 1024 * 1024

        with open(input_path, "wb") as buffer:
            while content := await file.read(1024 * 1024): # Read 1MB chunks
                size += len(content)
                if size > limit_bytes:
                    os.remove(input_path)
                    shutil.rmtree(job_output_dir)
                    raise HTTPException(status_code=413, detail=f"File too large. Max size {MAX_FILE_SIZE_MB}MB")
                buffer.write(content)

        cmd.extend(["-i", input_path])

    cmd.extend(["-o", job_output_dir])
    if output_format and output_format != "auto":
        cmd.extend(["--format", output_format])

    print(f"[attestation] job={job_id} ip={attestation['ip']} source={attestation['source']} ack=true")

    # Absolute-URL base for the webhook payload: explicit env wins (the API may
    # sit behind a proxy whose forwarded headers we can't trust), else what the
    # caller connected to.
    api_base = os.environ.get("PUBLIC_API_URL", "").rstrip("/") or str(request.base_url).rstrip("/")

    # Enqueue Job
    jobs[job_id] = {
        'status': 'queued',
        'logs': [f"Job {job_id} queued."],
        'cmd': cmd,
        'env': env,
        'output_dir': job_output_dir,
        'attestation': attestation,
        'webhook_url': webhook_url,
        'webhook_secret': webhook_secret,
        'base_url': api_base,
    }

    # Resume manifest: enough to re-run this job after the app stops mid-flight.
    # No secrets — the env is rebuilt from os.environ on resume.
    _write_resume_manifest(job_id, cmd, webhook_url=webhook_url, webhook_secret=webhook_secret,
                           base_url=api_base)

    _enqueue_job(job_id)

    return {"job_id": job_id, "status": "queued"}

@app.get("/api/status/{job_id}")
async def get_status(job_id: str, request: Request):
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")

    job = jobs[job_id]
    return {
        "status": job['status'],
        "logs": _visible_logs(job['logs']),
        "result": job.get('result')
    }


@app.get("/api/source/{job_id}")
async def get_source_video(job_id: str):
    """Stream a job's original source video for the live-analysis preview.

    Uploaded sources are blob URLs in the browser and don't survive a reload,
    so the recovered session points the preview here instead. Unauthenticated
    like the /videos mount — the UUID job_id is the capability.
    """
    matches = [
        f for f in glob.glob(os.path.join(UPLOAD_DIR, f"{job_id}_*"))
        if not os.path.basename(f).startswith("thumb_")
    ]
    if not matches:
        raise HTTPException(status_code=404, detail="Source not found")
    return FileResponse(matches[0], media_type="video/mp4")


@app.get("/api/jobs/{job_id}/download-all")
async def download_all_clips(job_id: str, request: Request):
    """Bundle the current version of every clip of a job into one ZIP."""
    await _ensure_job_files(job_id, request)
    output_dir = os.path.join(OUTPUT_DIR, job_id)
    json_files = glob.glob(os.path.join(output_dir, "*_metadata.json"))
    if not json_files:
        raise HTTPException(status_code=404, detail="Job not found")

    with open(json_files[0], 'r', encoding='utf-8') as f:
        data = json.load(f)

    # The metadata file on disk never carries video_url — the pipeline doesn't
    # write it, it's injected into the in-memory job record. So prefer the live
    # record (it also tracks edits like subtitled_/hook_ renames) and fall back
    # to the canonical name a job/restore rebuilds, instead of finding nothing.
    base_name = os.path.basename(json_files[0]).replace('_metadata.json', '')
    mem_clips = ((jobs.get(job_id) or {}).get('result') or {}).get('clips') or []

    files = []
    for i, clip in enumerate(data.get('shorts', [])):
        url = None
        if i < len(mem_clips):
            url = (mem_clips[i] or {}).get('video_url')
        url = url or clip.get('video_url')
        filename = (os.path.basename(url.split('/')[-1]) if url
                    else _canonical_clip_file(output_dir, base_name, i))
        path = os.path.join(output_dir, filename)
        if filename and os.path.exists(path):
            files.append((i, path))

    if not files:
        raise HTTPException(status_code=404, detail="No clip files found for this job")

    zip_path = os.path.join(output_dir, f"clips_{int(time.time())}.zip")

    def build_zip():
        # Videos are already compressed; store instead of deflate for speed.
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_STORED) as zf:
            for i, path in files:
                zf.write(path, arcname=f"clip_{i + 1:02d}_{os.path.basename(path)}")

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, build_zip)

    return FileResponse(
        zip_path,
        media_type="application/zip",
        filename=f"openshorts_clips_{job_id[:8]}.zip",
        background=BackgroundTask(os.remove, zip_path),
    )


@app.post("/api/projects/{job_id}/restore")
async def restore_project(job_id: str, request: Request):
    recovered = recover_job(OUTPUT_DIR, job_id)
    if not recovered:
        raise HTTPException(status_code=404, detail="Project not found")
    jobs[job_id] = {
        "status": "completed",
        "logs": ["♻️ Project loaded from local storage."],
        "output_dir": recovered["job_dir"],
        "result": {
            "clips": recovered["clips"],
            "cost_analysis": recovered["metadata"].get("cost_analysis"),
        },
        "project": recovered["manifest"],
    }
    return {
        "job_id": job_id,
        "status": "completed",
        "result": jobs[job_id]["result"],
        "project_state": recovered["manifest"].get("state", {}),
        "title": recovered["manifest"].get("title"),
    }


async def _ensure_job_files(job_id: str, request: Request) -> bool:
    """Check that a durable local project is available in the working volume."""
    job_dir = os.path.join(OUTPUT_DIR, job_id)
    return bool(job_id in jobs and glob.glob(os.path.join(job_dir, "*_metadata.json")))


from editor import VideoEditor
from subtitles import generate_srt, generate_ass, burn_subtitles, generate_srt_from_video
from hooks import add_hook_to_video
from translate import translate_video, get_supported_languages
from thumbnail import analyze_video_for_titles, refine_titles, generate_thumbnail, generate_youtube_description

class EditRequest(BaseModel):
    job_id: str
    clip_index: int
    api_key: Optional[str] = None
    input_filename: Optional[str] = None

@app.post("/api/edit")
async def edit_clip(
    req: EditRequest,
    request: Request,
):
    final_api_key = req.api_key or await resolve_gemini(request)

    if not final_api_key:
        raise gemini_missing_error()

    await _ensure_job_files(req.job_id, request)
    if req.job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")

    job = jobs[req.job_id]
    if 'result' not in job or 'clips' not in job['result']:
        raise HTTPException(status_code=400, detail="Job result not available")

    try:
        # Resolve Input Path: Prefer explict input_filename from frontend (chaining edits)
        if req.input_filename:
            # Security: Ensure just a filename, no paths
            safe_name = os.path.basename(req.input_filename)
            input_path = os.path.join(OUTPUT_DIR, req.job_id, safe_name)
            filename = safe_name
        else:
            # Fallback to original clip
            clip = job['result']['clips'][req.clip_index]
            filename = clip['video_url'].split('/')[-1]
            input_path = os.path.join(OUTPUT_DIR, req.job_id, filename)
        
        if not os.path.exists(input_path):
             raise HTTPException(status_code=404, detail=f"Video file not found: {input_path}")

        # Edit the clip WITHOUT its burned captions, then put them back on top —
        # otherwise the captions are baked into the edit and the next subtitle
        # pass stacks a second layer over them (see _reapply_captions).
        clean_name = _strip_burned_captions(os.path.join(OUTPUT_DIR, req.job_id), filename)
        had_captions = clean_name != filename
        if had_captions:
            filename = clean_name
            input_path = os.path.join(OUTPUT_DIR, req.job_id, clean_name)

        # Define output path for edited video
        edited_filename = f"edited_{filename}"
        output_path = os.path.join(OUTPUT_DIR, req.job_id, edited_filename)
        
        # Run editing in a thread to avoid blocking main loop
        # Since VideoEditor uses blocking calls (subprocess, API wait)
        def run_edit():
            editor = VideoEditor(api_key=final_api_key)
            
            # SAFE FILE RENAMING STRATEGY (Avoid UnicodeEncodeError in subprocesses)
            # Create a safe ASCII filename in the same directory
            safe_filename = f"temp_input_{req.job_id}.mp4"
            safe_input_path = os.path.join(OUTPUT_DIR, req.job_id, safe_filename)
            
            # Copy original file to safe path
            # (Copy is safer than rename if something crashes, we keep original)
            shutil.copy(input_path, safe_input_path)
            
            try:
                # 1. Upload (using safe path)
                vid_file = editor.upload_video(safe_input_path)
                
                # 2. Get duration
                import cv2
                cap = cv2.VideoCapture(safe_input_path)
                fps = cap.get(cv2.CAP_PROP_FPS)
                frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
                width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                duration = frame_count / fps if fps else 0
                cap.release()
                
                # Load transcript from metadata
                transcript = None
                try:
                    meta_files = glob.glob(os.path.join(OUTPUT_DIR, req.job_id, "*_metadata.json"))
                    if meta_files:
                        with open(meta_files[0], 'r') as f:
                            data = json.load(f)
                            transcript = data.get('transcript')
                except Exception as e:
                    print(f"⚠️ Could not load transcript for editing context: {e}")

                # 3. Get Plan (Filter String)
                # Zooms would crop burned-in captions/hooks off screen, so tell
                # the editor when the source already carries them. `filename` is
                # the original clip name (safe_input_path is an ASCII temp copy).
                has_captions = ("subtitled_" in filename) or ("hook_" in filename)
                filter_data = editor.get_ffmpeg_filter(vid_file, duration, fps=fps, width=width, height=height, transcript=transcript, has_captions=has_captions)
                
                # 4. Apply
                # Use safe output name first
                safe_output_path = os.path.join(OUTPUT_DIR, req.job_id, f"temp_output_{req.job_id}.mp4")
                editor.apply_edits(safe_input_path, safe_output_path, filter_data)
                
                # Move result to final destination (rename works even if dest name has unicode if filesystem supports it, 
                # but python might still struggle if locale is broken? No, os.rename usually handles it better than subprocess args)
                # Actually, output_path is defined above: f"edited_{filename}"
                # If filename has unicode, output_path has unicode.
                # Let's hope shutil.move / os.rename works.
                if os.path.exists(safe_output_path):
                    shutil.move(safe_output_path, output_path)
                
                return filter_data
            finally:
                # Cleanup temp safe input
                if os.path.exists(safe_input_path):
                    os.remove(safe_input_path)

        # Run in thread pool
        loop = asyncio.get_event_loop()
        plan = await loop.run_in_executor(None, run_edit)

        # Captions back on top, so the clip the user sees keeps them and the
        # clean edited file stays available for a later restyle.
        if had_captions:
            recap = await loop.run_in_executor(
                None, _reapply_captions, req.job_id, req.clip_index, output_path)
            if recap:
                edited_filename = os.path.basename(recap)

        new_video_url = f"/videos/{req.job_id}/{edited_filename}"

        # Persist the new current file like /api/subtitle does: in-memory job
        # result + metadata.json, so reload/recovery see this version.
        if req.clip_index < len(job['result']['clips']):
            job['result']['clips'][req.clip_index]['video_url'] = new_video_url
        try:
            meta_files = glob.glob(os.path.join(OUTPUT_DIR, req.job_id, "*_metadata.json"))
            if meta_files:
                with open(meta_files[0], 'r') as f:
                    meta = json.load(f)
                shorts = meta.get('shorts', [])
                if req.clip_index < len(shorts):
                    shorts[req.clip_index]['video_url'] = new_video_url
                    meta['shorts'] = shorts
                    with open(meta_files[0], 'w') as f:
                        json.dump(meta, f, indent=4)
        except Exception as e:
            print(f"⚠️ Failed to update metadata.json: {e}")

        _archive_clip_edit_bg(req.job_id, req.clip_index, edited_filename)

        return {
            "success": True,
            "new_video_url": new_video_url,
            "edit_plan": plan
        }

    except Exception as e:
        print(f"❌ Edit Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

class SubtitleRequest(BaseModel):
    job_id: str
    clip_index: int
    position: str = "bottom" # top, middle, bottom
    font_size: int = 16
    font_name: str = "Verdana"
    font_color: str = "#FFFFFF"
    border_color: str = "#000000"
    border_width: int = 2
    bg_color: str = "#000000"
    bg_opacity: float = 0.0
    style: str = "classic"  # classic (uniform color) or karaoke (word highlight)
    highlight_color: str = "#FFD700"
    effect: str = "none"  # none | glow | pop | box (karaoke only)
    base_opacity: float = 1.0  # opacity of non-active words (dimmed modern look)
    uppercase: bool = False
    input_filename: Optional[str] = None


@app.get("/api/clip/{job_id}/{clip_index}/transcript")
async def get_clip_transcript(job_id: str, clip_index: int, request: Request):
    """Return word-level captions for a specific clip, formatted for Remotion."""
    await _ensure_job_files(job_id, request)
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")

    output_dir = os.path.join(OUTPUT_DIR, job_id)
    json_files = glob.glob(os.path.join(output_dir, "*_metadata.json"))

    if not json_files:
        raise HTTPException(status_code=404, detail="Metadata not found")

    with open(json_files[0], 'r') as f:
        data = json.load(f)

    transcript = data.get('transcript')
    if not transcript:
        raise HTTPException(status_code=400, detail="Transcript not found in metadata")

    clips = data.get('shorts', [])
    if clip_index >= len(clips):
        raise HTTPException(status_code=404, detail="Clip not found")

    clip_data = clips[clip_index]
    clip_start = clip_data.get('start', 0)
    clip_end = clip_data.get('end', 0)

    # Extract words within clip range and convert to CaptionWord format
    captions = []
    for segment in transcript.get('segments', []):
        for word_info in segment.get('words', []):
            if word_info['end'] > clip_start and word_info['start'] < clip_end:
                captions.append({
                    "text": word_info.get('word', '').strip(),
                    "startMs": int((max(0, word_info['start'] - clip_start)) * 1000),
                    "endMs": int((max(0, word_info['end'] - clip_start)) * 1000),
                })

    duration_sec = clip_end - clip_start

    return {
        "captions": captions,
        "durationSec": duration_sec,
        "language": transcript.get('language', 'en'),
    }


class EffectsGenerateRequest(BaseModel):
    job_id: str
    clip_index: int
    input_filename: Optional[str] = None

@app.post("/api/effects/generate")
async def generate_effects_config(
    req: EffectsGenerateRequest,
    request: Request,
):
    """Generate structured EffectsConfig JSON for Remotion rendering via Gemini AI."""
    final_api_key = await resolve_gemini(request)

    if not final_api_key:
        raise gemini_missing_error()

    await _ensure_job_files(req.job_id, request)
    if req.job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")

    job = jobs[req.job_id]
    if 'result' not in job or 'clips' not in job['result']:
        raise HTTPException(status_code=400, detail="Job result not available")

    try:
        # Resolve input path
        if req.input_filename:
            safe_name = os.path.basename(req.input_filename)
            input_path = os.path.join(OUTPUT_DIR, req.job_id, safe_name)
        else:
            clip = job['result']['clips'][req.clip_index]
            filename = clip['video_url'].split('/')[-1]
            input_path = os.path.join(OUTPUT_DIR, req.job_id, filename)

        if not os.path.exists(input_path):
            raise HTTPException(status_code=404, detail=f"Video file not found: {input_path}")

        def run_effects_generation():
            editor = VideoEditor(api_key=final_api_key)

            # Create safe ASCII filename to avoid encoding issues
            safe_filename = f"temp_effects_{req.job_id}.mp4"
            safe_input_path = os.path.join(OUTPUT_DIR, req.job_id, safe_filename)
            shutil.copy(input_path, safe_input_path)

            try:
                # Upload video to Gemini
                vid_file = editor.upload_video(safe_input_path)

                # Get video metadata via ffprobe
                probe_cmd = [
                    'ffprobe', '-v', 'error',
                    '-select_streams', 'v:0',
                    '-show_entries', 'stream=width,height,r_frame_rate,duration',
                    '-show_entries', 'format=duration',
                    '-of', 'json',
                    safe_input_path
                ]
                probe_result = subprocess.check_output(probe_cmd).decode().strip()
                probe_data = json.loads(probe_result)

                stream = probe_data.get('streams', [{}])[0]
                width = int(stream.get('width', 1080))
                height = int(stream.get('height', 1920))

                # Parse fps from r_frame_rate (e.g. "30/1")
                r_frame_rate = stream.get('r_frame_rate', '30/1')
                num, den = r_frame_rate.split('/')
                fps = round(int(num) / int(den), 2)

                # Get duration from stream or format
                duration = float(stream.get('duration', 0))
                if duration == 0:
                    duration = float(probe_data.get('format', {}).get('duration', 0))

                # Load transcript from metadata
                transcript = None
                try:
                    meta_files = glob.glob(os.path.join(OUTPUT_DIR, req.job_id, "*_metadata.json"))
                    if meta_files:
                        with open(meta_files[0], 'r') as f:
                            data = json.load(f)
                            transcript = data.get('transcript')
                except Exception as e:
                    print(f"⚠️ Could not load transcript for effects config: {e}")

                # Generate effects config
                effects_config = editor.get_effects_config(
                    vid_file, duration, fps=fps, width=width, height=height, transcript=transcript
                )

                return effects_config
            finally:
                if os.path.exists(safe_input_path):
                    os.remove(safe_input_path)

        loop = asyncio.get_event_loop()
        effects_config = await loop.run_in_executor(None, run_effects_generation)

        if effects_config is None:
            raise HTTPException(status_code=500, detail="Failed to generate effects config from Gemini")

        return {"effects": effects_config}

    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Effects Generation Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/subtitle")
async def add_subtitles(req: SubtitleRequest, request: Request):
    await _ensure_job_files(req.job_id, request)
    if req.job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    
    # Reload job data from disk just in case metadata was updated
    job = jobs[req.job_id]

    # We need to access metadata.json to get the transcript
    output_dir = os.path.join(OUTPUT_DIR, req.job_id)
    json_files = glob.glob(os.path.join(output_dir, "*_metadata.json"))
    
    if not json_files:
        raise HTTPException(status_code=404, detail="Metadata not found")
        
    with open(json_files[0], 'r') as f:
        data = json.load(f)
        
    transcript = data.get('transcript')
    if not transcript:
        raise HTTPException(status_code=400, detail="Transcript not found in metadata. Please process a new video.")
        
    clips = data.get('shorts', [])
    if req.clip_index >= len(clips):
        raise HTTPException(status_code=404, detail="Clip not found")
        
    clip_data = clips[req.clip_index]
    
    # Video Path
    if req.input_filename:
        # Use chained file
        filename = os.path.basename(req.input_filename)
    else:
        # Fallback to standard naming
        filename = clip_data.get('video_url', '').split('/')[-1]
        if not filename:
             base_name = os.path.basename(json_files[0]).replace('_metadata.json', '')
             filename = f"{base_name}_clip_{req.clip_index+1}.mp4"

    # Re-subtitling must replace previous subtitles instead of burning over them.
    filename = _strip_burned_captions(output_dir, filename)

    input_path = os.path.join(output_dir, filename)
    if not os.path.exists(input_path):
        # Try looking for edited version if url implied it?
        # Just fail if not found.
        raise HTTPException(status_code=404, detail=f"Video file not found: {input_path}")

    # Define outputs
    generation_id = int(time.time())
    is_karaoke = req.style == "karaoke"
    srt_filename = f"subs_{req.clip_index}_{generation_id}.{'ass' if is_karaoke else 'srt'}"
    srt_path = os.path.join(output_dir, srt_filename)

    # Style options shared by the karaoke ASS generator paths.
    karaoke_opts = dict(
        alignment=req.position, fontsize=req.font_size, font_name=req.font_name,
        font_color=req.font_color, border_color=req.border_color,
        border_width=req.border_width, highlight_color=req.highlight_color,
        bg_color=req.bg_color, bg_opacity=req.bg_opacity,
        effect=req.effect, base_opacity=req.base_opacity, uppercase=req.uppercase,
    )

    # Output video
    # We create a new file "subtitled_..."
    output_filename = f"subtitled_{generation_id}_{filename}"
    output_path = os.path.join(output_dir, output_filename)

    is_dubbed = filename.startswith("translated_")

    try:
        # 1. Generate SRT — from the existing transcript, or a fresh
        # transcription when the audio was dubbed.
        if is_dubbed:
            print(f"🎙️ Dubbed video detected, transcribing audio for subtitles...")
            def run_transcribe_srt():
                if is_karaoke:
                    return generate_srt_from_video(input_path, srt_path, style="karaoke", **karaoke_opts)
                return generate_srt_from_video(input_path, srt_path)

            loop = asyncio.get_event_loop()
            success = await loop.run_in_executor(None, run_transcribe_srt)
        elif is_karaoke:
            success = generate_ass(transcript, clip_data['start'], clip_data['end'], srt_path, **karaoke_opts)
        else:
            success = generate_srt(transcript, clip_data['start'], clip_data['end'], srt_path)

        if not success:
             raise HTTPException(status_code=400, detail="No words found for this clip range.")

        # 2. Burn Subtitles
        # Run in thread pool
        def run_burn():
             burn_subtitles(input_path, srt_path, output_path,
                           alignment=req.position, fontsize=req.font_size,
                           font_name=req.font_name, font_color=req.font_color,
                           border_color=req.border_color, border_width=req.border_width,
                           bg_color=req.bg_color, bg_opacity=req.bg_opacity)
        
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, run_burn)
        
    except Exception as e:
        print(f"❌ Subtitle Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

    # 3. Update Result and Metadata
    # Update InMemory Jobs
    if req.clip_index < len(job['result']['clips']):
         job['result']['clips'][req.clip_index]['video_url'] = f"/videos/{req.job_id}/{output_filename}"
    
    # Update Metadata on Disk (Persistence)
    try:
        if req.clip_index < len(clips):
            clips[req.clip_index]['video_url'] = f"/videos/{req.job_id}/{output_filename}"
            # Update the main data structure
            data['shorts'] = clips
            
            # Write back
            with open(json_files[0], 'w') as f:
                json.dump(data, f, indent=4)
                print(f"✅ Metadata updated with subtitled video for clip {req.clip_index}")
    except Exception as e:
        print(f"⚠️ Failed to update metadata.json: {e}")
        # Non-critical, but good for persistence

    _archive_clip_edit_bg(req.job_id, req.clip_index, output_filename)

    return {
        "success": True,
        "new_video_url": f"/videos/{req.job_id}/{output_filename}"
    }

class RemoveSubtitlesRequest(BaseModel):
    job_id: str
    clip_index: int
    input_filename: Optional[str] = None


@app.post("/api/subtitle/remove")
async def remove_subtitles(req: RemoveSubtitlesRequest, request: Request):
    """Point a clip back at its un-captioned original.

    New clips start without captions, but users can add them later. No
    re-encode is needed to undo that choice: the pipeline keeps the clean file
    next to the derived
    ``subtitled_<ts>_`` one, so removing is just choosing the other file.
    """
    await _ensure_job_files(req.job_id, request)
    if req.job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    job = jobs[req.job_id]

    output_dir = os.path.join(OUTPUT_DIR, req.job_id)
    json_files = glob.glob(os.path.join(output_dir, "*_metadata.json"))
    if not json_files:
        raise HTTPException(status_code=404, detail="Metadata not found")
    with open(json_files[0], 'r') as f:
        data = json.load(f)
    clips = data.get('shorts', [])
    if req.clip_index >= len(clips):
        raise HTTPException(status_code=404, detail="Clip not found")

    filename = os.path.basename(
        req.input_filename
        or (clips[req.clip_index].get('video_url') or '').split('/')[-1]
        or f"{os.path.basename(json_files[0]).replace('_metadata.json', '')}"
           f"_clip_{req.clip_index + 1}.mp4")

    # Same walk-back the burn path uses, so this undoes any number of restyles.
    while True:
        m = re.match(r'^subtitled_\d+_(.+)$', filename)
        if not m or not os.path.exists(os.path.join(output_dir, m.group(1))):
            break
        filename = m.group(1)

    if not os.path.exists(os.path.join(output_dir, filename)):
        raise HTTPException(status_code=404,
                            detail="The original clip is no longer available.")

    new_url = f"/videos/{req.job_id}/{filename}"
    if req.clip_index < len(job.get('result', {}).get('clips', [])):
        job['result']['clips'][req.clip_index]['video_url'] = new_url
    try:
        clips[req.clip_index]['video_url'] = new_url
        data['shorts'] = clips
        with open(json_files[0], 'w') as f:
            json.dump(data, f, indent=4)
    except Exception as e:
        print(f"⚠️ Failed to update metadata.json: {e}")

    _archive_clip_edit_bg(req.job_id, req.clip_index, filename)
    return {"success": True, "new_video_url": new_url}


class HookRequest(BaseModel):
    job_id: str
    clip_index: int
    text: str
    input_filename: Optional[str] = None
    position: Optional[str] = "top" # top, center, bottom
    size: Optional[str] = "M" # S, M, L
    duration_seconds: Optional[float] = None  # None = hook visible for the whole clip
    style: Optional[str] = "classic"  # classic/dark/yellow/red/outline/outline_yellow

@app.post("/api/hook")
async def add_hook(req: HookRequest, request: Request):
    await _ensure_job_files(req.job_id, request)
    if req.job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")

    job = jobs[req.job_id]
    output_dir = os.path.join(OUTPUT_DIR, req.job_id)
    json_files = glob.glob(os.path.join(output_dir, "*_metadata.json"))
    
    if not json_files:
        raise HTTPException(status_code=404, detail="Metadata not found")
        
    with open(json_files[0], 'r') as f:
        data = json.load(f)
        
    clips = data.get('shorts', [])
    if req.clip_index >= len(clips):
        raise HTTPException(status_code=404, detail="Clip not found")
        
    clip_data = clips[req.clip_index]
    
    # Video Path
    if req.input_filename:
        filename = os.path.basename(req.input_filename)
    else:
        filename = clip_data.get('video_url', '').split('/')[-1]
        if not filename:
             base_name = os.path.basename(json_files[0]).replace('_metadata.json', '')
             filename = f"{base_name}_clip_{req.clip_index+1}.mp4"
         
    input_path = os.path.join(output_dir, filename)
    if not os.path.exists(input_path):
        raise HTTPException(status_code=404, detail=f"Video file not found: {input_path}")

    # Same invariant as /api/edit: overlay onto the clip WITHOUT its burned
    # captions, then put them back on top, so a later restyle never stacks a
    # second caption layer (see _reapply_captions).
    clean_name = _strip_burned_captions(output_dir, filename)
    had_captions = clean_name != filename
    if had_captions:
        filename = clean_name
        input_path = os.path.join(output_dir, clean_name)

    # Output video
    output_filename = f"hook_{filename}"
    output_path = os.path.join(output_dir, output_filename)
    
    # Map Size to Scale
    size_map = {"S": 0.8, "M": 1.0, "L": 1.3}
    font_scale = size_map.get(req.size, 1.0)

    try:
        # Run in thread pool
        def run_hook():
             add_hook_to_video(input_path, req.text, output_path, position=req.position, font_scale=font_scale, duration=req.duration_seconds, style=req.style)

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, run_hook)

    except Exception as e:
        print(f"❌ Hook Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

    # Captions back on top (see /api/edit for the same invariant).
    if had_captions:
        recap = await asyncio.get_event_loop().run_in_executor(
            None, _reapply_captions, req.job_id, req.clip_index, output_path)
        if recap:
            output_filename = os.path.basename(recap)

    # Update Persistence (Same logic as subtitles)
    # Update InMemory Jobs
    if req.clip_index < len(job['result']['clips']):
         job['result']['clips'][req.clip_index]['video_url'] = f"/videos/{req.job_id}/{output_filename}"
    
    # Update Metadata on Disk
    try:
        if req.clip_index < len(clips):
            clips[req.clip_index]['video_url'] = f"/videos/{req.job_id}/{output_filename}"
            data['shorts'] = clips
            with open(json_files[0], 'w') as f:
                json.dump(data, f, indent=4)
                print(f"✅ Metadata updated with hook video for clip {req.clip_index}")
    except Exception as e:
        print(f"⚠️ Failed to update metadata.json: {e}")

    _archive_clip_edit_bg(req.job_id, req.clip_index, output_filename)

    return {
        "success": True,
        "new_video_url": f"/videos/{req.job_id}/{output_filename}"
    }

class TranslateRequest(BaseModel):
    job_id: str
    clip_index: int
    target_language: str
    source_language: Optional[str] = None
    input_filename: Optional[str] = None

@app.get("/api/translate/languages")
async def get_languages():
    """Return supported languages for translation."""
    return {"languages": get_supported_languages()}

@app.post("/api/translate")
async def translate_clip(
    req: TranslateRequest,
    request: Request,
    x_elevenlabs_key: Optional[str] = Header(None, alias="X-ElevenLabs-Key")
):
    """Translate a video clip to a different language using ElevenLabs dubbing."""
    if not x_elevenlabs_key:
        raise HTTPException(status_code=400, detail="Missing X-ElevenLabs-Key header")

    await _ensure_job_files(req.job_id, request)
    if req.job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")

    job = jobs[req.job_id]
    output_dir = os.path.join(OUTPUT_DIR, req.job_id)
    json_files = glob.glob(os.path.join(output_dir, "*_metadata.json"))

    if not json_files:
        raise HTTPException(status_code=404, detail="Metadata not found")

    with open(json_files[0], 'r') as f:
        data = json.load(f)

    clips = data.get('shorts', [])
    if req.clip_index >= len(clips):
        raise HTTPException(status_code=404, detail="Clip not found")

    clip_data = clips[req.clip_index]

    # Video Path
    if req.input_filename:
        filename = os.path.basename(req.input_filename)
    else:
        filename = clip_data.get('video_url', '').split('/')[-1]
        if not filename:
             base_name = os.path.basename(json_files[0]).replace('_metadata.json', '')
             filename = f"{base_name}_clip_{req.clip_index+1}.mp4"

    input_path = os.path.join(output_dir, filename)
    if not os.path.exists(input_path):
        raise HTTPException(status_code=404, detail=f"Video file not found: {input_path}")

    # Output video with language suffix
    base, ext = os.path.splitext(filename)
    output_filename = f"translated_{req.target_language}_{base}{ext}"
    output_path = os.path.join(output_dir, output_filename)

    try:
        # Run translation in thread pool (blocking API calls)
        def run_translate():
            return translate_video(
                video_path=input_path,
                output_path=output_path,
                target_language=req.target_language,
                api_key=x_elevenlabs_key,
                source_language=req.source_language,
            )

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, run_translate)

    except Exception as e:
        print(f"❌ Translation Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

    # Update InMemory Jobs
    if req.clip_index < len(job['result']['clips']):
         job['result']['clips'][req.clip_index]['video_url'] = f"/videos/{req.job_id}/{output_filename}"

    # Update Metadata on Disk
    try:
        if req.clip_index < len(clips):
            clips[req.clip_index]['video_url'] = f"/videos/{req.job_id}/{output_filename}"
            data['shorts'] = clips
            with open(json_files[0], 'w') as f:
                json.dump(data, f, indent=4)
                print(f"✅ Metadata updated with translated video for clip {req.clip_index}")
    except Exception as e:
        print(f"⚠️ Failed to update metadata.json: {e}")

    _archive_clip_edit_bg(req.job_id, req.clip_index, output_filename)

    return {
        "success": True,
        "new_video_url": f"/videos/{req.job_id}/{output_filename}"
    }

# --- Thumbnail Studio Endpoints ---

@app.post("/api/thumbnail/upload")
async def thumbnail_upload(
    request: Request,
    file: Optional[UploadFile] = File(None),
    url: Optional[str] = Form(None),
):
    """Upload video and start background Whisper transcription immediately."""
    if not url and not file:
        raise HTTPException(status_code=400, detail="Must provide URL or File")

    session_id = str(uuid.uuid4())
    transcript_event = asyncio.Event()

    # Save file if uploaded directly. basename() stops a "../../x" filename from
    # escaping UPLOAD_DIR; the chunked read caps memory so a huge body can't OOM.
    video_path = None
    if file:
        safe_name = os.path.basename(file.filename or "upload") or "upload"
        video_path = os.path.join(UPLOAD_DIR, f"thumb_{session_id}_{safe_name}")
        size = 0
        limit_bytes = MAX_FILE_SIZE_MB * 1024 * 1024
        with open(video_path, "wb") as buffer:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > limit_bytes:
                    os.remove(video_path)
                    raise HTTPException(status_code=413, detail=f"File too large. Max size {MAX_FILE_SIZE_MB}MB")
                buffer.write(chunk)

    # Initialize session
    thumbnail_sessions[session_id] = {
        "video_path": video_path,
        "transcript_event": transcript_event,
        "transcript_ready": False,
        "transcript": None,
        "transcript_segments": [],
        "video_duration": 0,
        "language": "en",
        "context": "",
        "titles": [],
        "conversation": [],
        "_url": url,  # Store URL for deferred download
    }

    async def run_background_whisper():
        try:
            vpath = video_path
            # Download YouTube video if URL was provided
            if not vpath and url:
                from main import download_youtube_video
                loop = asyncio.get_event_loop()
                vpath, _ = await loop.run_in_executor(None, download_youtube_video, url, UPLOAD_DIR)
                thumbnail_sessions[session_id]["video_path"] = vpath

            from main import transcribe_video
            loop = asyncio.get_event_loop()
            transcript = await loop.run_in_executor(None, transcribe_video, vpath)
            segments = transcript.get("segments", [])
            duration = segments[-1]["end"] if segments else 0

            thumbnail_sessions[session_id].update({
                "transcript_ready": True,
                "transcript": transcript,
                "transcript_segments": segments,
                "video_duration": duration,
                "language": transcript.get("language", "en"),
            })
            print(f"✅ [Thumbnail] Background Whisper complete for session {session_id}")
        except Exception as e:
            print(f"❌ [Thumbnail] Background Whisper failed: {e}")
            thumbnail_sessions[session_id]["transcript_error"] = str(e)
        finally:
            transcript_event.set()

    asyncio.create_task(run_background_whisper())

    return {"session_id": session_id}


@app.post("/api/thumbnail/analyze")
async def thumbnail_analyze(
    request: Request,
    file: Optional[UploadFile] = File(None),
    url: Optional[str] = Form(None),
    session_id: Optional[str] = Form(None),
    x_gemini_key: Optional[str] = Header(None, alias="X-Gemini-Key")
):
    """Analyze a video and suggest viral YouTube titles."""
    api_key = await resolve_gemini(request)
    if not api_key:
        raise gemini_missing_error()

    pre_transcript = None

    # Check for pre-existing session with background Whisper
    if session_id and session_id in thumbnail_sessions:
        session = thumbnail_sessions[session_id]

        # Wait for background Whisper to complete
        transcript_event = session.get("transcript_event")
        if transcript_event:
            print(f"⏳ [Thumbnail] Waiting for background Whisper to finish...")
            await transcript_event.wait()

        if session.get("transcript_error"):
            raise HTTPException(status_code=500, detail=f"Transcription failed: {session['transcript_error']}")

        video_path = session["video_path"]
        if not video_path or not os.path.exists(video_path):
            raise HTTPException(status_code=404, detail="Video file not found in session")

        if session.get("transcript_ready"):
            pre_transcript = session["transcript"]
    else:
        # No pre-existing session — need file or URL
        if not url and not file:
            raise HTTPException(status_code=400, detail="Must provide URL, File, or session_id")

        session_id = str(uuid.uuid4())

        if url:
            from main import download_youtube_video
            video_path, _ = download_youtube_video(url, UPLOAD_DIR)
        else:
            safe_name = os.path.basename(file.filename or "upload") or "upload"
            video_path = os.path.join(UPLOAD_DIR, f"thumb_{session_id}_{safe_name}")
            size = 0
            limit_bytes = MAX_FILE_SIZE_MB * 1024 * 1024
            with open(video_path, "wb") as buffer:
                while chunk := await file.read(1024 * 1024):
                    size += len(chunk)
                    if size > limit_bytes:
                        os.remove(video_path)
                        raise HTTPException(status_code=413, detail=f"File too large. Max size {MAX_FILE_SIZE_MB}MB")
                    buffer.write(chunk)

    try:
        # Run analysis in thread pool (skips Whisper if pre_transcript is available)
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, analyze_video_for_titles, api_key, video_path, pre_transcript)

        # Store/update session context
        if session_id not in thumbnail_sessions:
            thumbnail_sessions[session_id] = {}

        thumbnail_sessions[session_id].update({
            "context": result.get("transcript_summary", ""),
            "titles": result.get("titles", []),
            "language": result.get("language", "en"),
            "conversation": thumbnail_sessions[session_id].get("conversation", []),
            "video_path": video_path,
            "transcript_segments": result.get("segments", []),
            "video_duration": result.get("video_duration", 0)
        })

        return {
            "session_id": session_id,
            "titles": result.get("titles", []),
            "context": result.get("transcript_summary", ""),
            "language": result.get("language", "en"),
            "recommended": result.get("recommended", [])
        }

    except Exception as e:
        print(f"❌ Thumbnail Analyze Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


class ThumbnailTitlesRequest(BaseModel):
    session_id: Optional[str] = None
    message: Optional[str] = None
    title: Optional[str] = None

@app.post("/api/thumbnail/titles")
async def thumbnail_titles(
    req: ThumbnailTitlesRequest,
    request: Request,
):
    """Refine title suggestions or accept a manual title."""
    api_key = await resolve_gemini(request)
    if not api_key:
        raise gemini_missing_error()

    # Manual title mode - just create a session with the user's title
    if req.title:
        session_id = req.session_id or str(uuid.uuid4())
        if session_id not in thumbnail_sessions:
            thumbnail_sessions[session_id] = {
                "context": "",
                "titles": [req.title],
                "language": "en",
                "conversation": []
            }
        return {"session_id": session_id, "titles": [req.title]}

    # Refinement mode
    if not req.session_id or req.session_id not in thumbnail_sessions:
        raise HTTPException(status_code=404, detail="Session not found")

    if not req.message:
        raise HTTPException(status_code=400, detail="Must provide message or title")

    session = thumbnail_sessions[req.session_id]

    # Add user message to conversation history
    session["conversation"].append({"role": "user", "content": req.message})

    try:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            refine_titles,
            api_key,
            session["context"],
            req.message,
            session["conversation"]
        )

        new_titles = result.get("titles", [])
        session["titles"] = new_titles
        session["conversation"].append({"role": "assistant", "content": json.dumps(new_titles)})

        return {"titles": new_titles}

    except Exception as e:
        print(f"❌ Thumbnail Titles Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/thumbnail/generate")
async def thumbnail_generate(
    request: Request,
    session_id: str = Form(...),
    title: str = Form(...),
    extra_prompt: str = Form(""),
    count: int = Form(3),
    face: Optional[UploadFile] = File(None),
    background: Optional[UploadFile] = File(None),
):
    """Generate YouTube thumbnails with Gemini image generation."""
    api_key = await resolve_gemini(request)
    if not api_key:
        raise gemini_missing_error()

    # Clamp count
    count = min(max(1, count), 6)

    # Save optional uploaded images. basename() on the session id and filenames
    # keeps everything inside UPLOAD_DIR (no "../" escape from client input).
    face_path = None
    bg_path = None
    safe_session = os.path.basename(session_id) or "session"
    thumb_upload_dir = os.path.join(UPLOAD_DIR, f"thumb_{safe_session}")
    os.makedirs(thumb_upload_dir, exist_ok=True)

    try:
        if face and face.filename:
            face_name = os.path.basename(face.filename)
            face_path = os.path.join(thumb_upload_dir, f"face_{face_name}")
            with open(face_path, "wb") as f:
                f.write(await face.read())

        if background and background.filename:
            bg_name = os.path.basename(background.filename)
            bg_path = os.path.join(thumb_upload_dir, f"bg_{bg_name}")
            with open(bg_path, "wb") as f:
                f.write(await background.read())

        # Get video context from session (transcript summary from analysis step)
        video_context = ""
        if session_id in thumbnail_sessions:
            video_context = thumbnail_sessions[session_id].get("context", "")

        # Run generation in thread pool
        loop = asyncio.get_event_loop()
        thumbnails = await loop.run_in_executor(
            None,
            generate_thumbnail,
            api_key,
            title,
            session_id,
            face_path,
            bg_path,
            extra_prompt,
            count,
            video_context
        )

        if not thumbnails:
            raise HTTPException(status_code=500, detail="Thumbnail generation failed. Please check your Gemini API key has access to image generation (gemini-3.1-flash-image-preview model).")

        return {"thumbnails": thumbnails}

    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Thumbnail Generate Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


class ThumbnailDescribeRequest(BaseModel):
    session_id: str
    title: str

@app.post("/api/thumbnail/describe")
async def thumbnail_describe(
    req: ThumbnailDescribeRequest,
    request: Request,
):
    """Generate a YouTube description with chapters from the transcript."""
    api_key = await resolve_gemini(request)
    if not api_key:
        raise gemini_missing_error()

    if req.session_id not in thumbnail_sessions:
        raise HTTPException(status_code=404, detail="Session not found")

    session = thumbnail_sessions[req.session_id]
    segments = session.get("transcript_segments", [])
    if not segments:
        raise HTTPException(status_code=400, detail="No transcript segments available. Please analyze a video first.")

    try:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            generate_youtube_description,
            api_key,
            req.title,
            segments,
            session.get("language", "en"),
            session.get("video_duration", 0)
        )
        return {"description": result.get("description", "")}

    except Exception as e:
        print(f"❌ Thumbnail Describe Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
