"""Durable single-tenant project library stored on the local output volume.

Completed jobs remain in ``output/<job_id>`` and are marked with a small JSON
manifest.  Keeping the manifest next to the existing rendered files means a
restart only needs to re-read local state; there is no database, remote object
store or signed URL involved.
"""

from __future__ import annotations

import glob
import json
import os
import re
import shutil
import tempfile
import threading
from datetime import datetime, timezone
from urllib.parse import quote


MANIFEST_NAME = ".openshorts-project.json"
MAX_STATE_BYTES = 262_144
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_locks: dict[str, threading.RLock] = {}
_locks_guard = threading.Lock()


def _lock(job_id: str) -> threading.RLock:
    with _locks_guard:
        return _locks.setdefault(job_id, threading.RLock())


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _valid_id(value: str) -> bool:
    return bool(isinstance(value, str) and _ID_RE.fullmatch(value))


def safe_job_dir(output_dir: str, job_id: str) -> str | None:
    """Return a job directory only when the id stays below ``output_dir``."""
    if not _valid_id(job_id):
        return None
    root = os.path.realpath(output_dir)
    target = os.path.realpath(os.path.join(root, job_id))
    if target == root or not target.startswith(root + os.sep):
        return None
    return target


def _manifest_path(job_dir: str) -> str:
    return os.path.join(job_dir, MANIFEST_NAME)


def _atomic_json(path: str, data: dict) -> None:
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".openshorts-", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass


def _read(path: str) -> dict | None:
    try:
        with open(path, encoding="utf-8") as handle:
            value = json.load(handle)
        return value if isinstance(value, dict) else None
    except (OSError, ValueError, TypeError):
        return None


def _filename_from_url(url: str | None) -> str:
    return os.path.basename(str(url or "").split("?", 1)[0])


def _clip_title(clip: dict) -> str:
    return (clip.get("title")
            or clip.get("video_title_for_youtube_short")
            or "Short")


def _existing_state(manifest: dict | None) -> dict[int, dict]:
    state = (manifest or {}).get("state") or {}
    result = {}
    for raw in state.get("clips", []):
        if isinstance(raw, dict) and isinstance(raw.get("index"), int):
            result[raw["index"]] = dict(raw)
    return result


def _build_manifest(job_id: str, job_dir: str, clips: list[dict], old: dict | None) -> dict:
    old_clips = _existing_state(old)
    entries = []
    for index, clip in enumerate(clips or []):
        if not isinstance(clip, dict):
            continue
        current = _filename_from_url(clip.get("video_url"))
        previous = old_clips.get(index, {})
        original = os.path.basename(str(previous.get("original_file") or current))
        server = os.path.basename(str(previous.get("server_file") or current))
        if current and os.path.exists(os.path.join(job_dir, current)):
            server = current
        if not server or not os.path.exists(os.path.join(job_dir, server)):
            fallback = original if os.path.exists(os.path.join(job_dir, original)) else ""
            server = fallback
        entries.append({
            "index": index,
            "title": _clip_title(clip),
            "original_file": original,
            "server_file": server,
            "active_layers": previous.get("active_layers"),
        })

    created_at = (old or {}).get("created_at") or _now()
    return {
        "version": 1,
        "job_id": job_id,
        "title": ((old or {}).get("title")
                  or (entries[0].get("title") if entries else "Project")),
        "created_at": created_at,
        "updated_at": _now(),
        "state": {"v": 1, "clips": entries},
    }


def ensure_project(output_dir: str, job_id: str, clips: list[dict]) -> dict | None:
    """Create/update the durable manifest for a completed job."""
    job_dir = safe_job_dir(output_dir, job_id)
    if not job_dir or not os.path.isdir(job_dir):
        return None
    with _lock(job_id):
        path = _manifest_path(job_dir)
        manifest = _build_manifest(job_id, job_dir, clips, _read(path))
        _atomic_json(path, manifest)
        return manifest


def load_project(output_dir: str, job_id: str) -> tuple[str, dict] | None:
    job_dir = safe_job_dir(output_dir, job_id)
    if not job_dir:
        return None
    manifest = _read(_manifest_path(job_dir))
    if not manifest or manifest.get("job_id") not in (None, job_id):
        return None
    return job_dir, manifest


def bootstrap_projects(output_dir: str) -> int:
    """Adopt completed local jobs created before the manifest was introduced."""
    count = 0
    try:
        entries = os.listdir(output_dir)
    except OSError:
        return count
    for job_id in entries:
        job_dir = safe_job_dir(output_dir, job_id)
        if not job_dir or not os.path.isdir(job_dir):
            continue
        if job_id in {"gallery", "thumbnails"} or os.path.exists(_manifest_path(job_dir)):
            continue
        metadata = glob.glob(os.path.join(job_dir, "*_metadata.json"))
        if not metadata:
            continue
        data = _read(metadata[0]) or {}
        if ensure_project(output_dir, job_id, data.get("shorts") or []):
            count += 1
    return count


def recover_job(output_dir: str, job_id: str) -> dict | None:
    loaded = load_project(output_dir, job_id)
    if not loaded:
        return None
    job_dir, manifest = loaded
    metadata_files = glob.glob(os.path.join(job_dir, "*_metadata.json"))
    if not metadata_files:
        return None
    metadata = _read(metadata_files[0]) or {}
    clips = metadata.get("shorts") or []
    state = (manifest.get("state") or {}).get("clips") or []
    by_index = {c.get("index"): c for c in state if isinstance(c, dict)}
    for index, clip in enumerate(clips):
        if not isinstance(clip, dict):
            continue
        entry = by_index.get(index) or {}
        filename = os.path.basename(str(entry.get("server_file") or _filename_from_url(clip.get("video_url"))))
        if filename and os.path.exists(os.path.join(job_dir, filename)):
            clip["video_url"] = f"/videos/{job_id}/{quote(filename)}"
    return {
        "job_dir": job_dir,
        "manifest": manifest,
        "metadata": metadata,
        "clips": clips,
    }


def list_projects(output_dir: str, limit: int = 200) -> list[dict]:
    projects = []
    try:
        entries = os.listdir(output_dir)
    except OSError:
        return projects
    for job_id in entries:
        loaded = load_project(output_dir, job_id)
        if not loaded:
            continue
        _job_dir, manifest = loaded
        state = (manifest.get("state") or {}).get("clips") or []
        projects.append({
            "job_id": job_id,
            "title": manifest.get("title") or "Project",
            "clip_count": len(state),
            "size_bytes": directory_size(loaded[0]),
            "created_at": manifest.get("created_at"),
            "updated_at": manifest.get("updated_at"),
        })
    projects.sort(key=lambda item: item.get("created_at") or "", reverse=True)
    return projects[:limit]


def history(output_dir: str, limit: int = 500) -> list[dict]:
    items = []
    for project in list_projects(output_dir, limit=limit):
        loaded = load_project(output_dir, project["job_id"])
        if not loaded:
            continue
        _job_dir, manifest = loaded
        for clip in (manifest.get("state") or {}).get("clips") or []:
            if not isinstance(clip, dict):
                continue
            index = clip.get("index")
            filename = os.path.basename(str(clip.get("server_file") or ""))
            if not isinstance(index, int) or not filename:
                continue
            if not os.path.exists(os.path.join(_job_dir, filename)):
                continue
            items.append({
                "id": f"{project['job_id']}:{index}",
                "job_id": project["job_id"],
                "clip_index": index,
                "title": clip.get("title") or "Short",
                "created_at": project.get("created_at"),
                "size_bytes": os.path.getsize(os.path.join(_job_dir, filename)),
                "view_url": f"/videos/{project['job_id']}/{quote(filename)}",
                "download_url": f"/api/projects/{project['job_id']}/clips/{index}/download",
            })
    items.sort(key=lambda item: item.get("created_at") or "", reverse=True)
    return items[:limit]


def save_project_state(output_dir: str, job_id: str, clips_in: list[dict]) -> bool:
    loaded = load_project(output_dir, job_id)
    if not loaded:
        return False
    job_dir, manifest = loaded
    with _lock(job_id):
        state = dict(manifest.get("state") or {"v": 1, "clips": []})
        clips_state = [dict(c) for c in state.get("clips", []) if isinstance(c, dict)]
        by_index = {c.get("index"): c for c in clips_state}
        for item in clips_in or []:
            if not isinstance(item, dict) or not isinstance(item.get("index"), int):
                continue
            index = item["index"]
            entry = by_index.setdefault(index, {
                "index": index, "title": "Short", "original_file": None,
                "server_file": None, "active_layers": None,
            })
            entry["active_layers"] = item.get("active_layers")
            if item.get("server_file"):
                filename = os.path.basename(str(item["server_file"]))
                if filename and os.path.exists(os.path.join(job_dir, filename)):
                    entry["server_file"] = filename
        state["clips"] = clips_state
        manifest["state"] = state
        manifest["updated_at"] = _now()
        _atomic_json(_manifest_path(job_dir), manifest)
    return True


def sync_clip_edit(output_dir: str, job_id: str, clip_index: int, filename: str) -> bool:
    loaded = load_project(output_dir, job_id)
    if not loaded:
        return False
    job_dir, manifest = loaded
    filename = os.path.basename(str(filename or ""))
    if not filename or not os.path.exists(os.path.join(job_dir, filename)):
        return False
    with _lock(job_id):
        state = dict(manifest.get("state") or {"v": 1, "clips": []})
        entries = [dict(c) for c in state.get("clips", []) if isinstance(c, dict)]
        entry = next((c for c in entries if c.get("index") == clip_index), None)
        if entry is None:
            entry = {"index": clip_index, "title": "Short", "original_file": filename,
                     "server_file": filename, "active_layers": None}
            entries.append(entry)
        entry["server_file"] = filename
        state["clips"] = entries
        manifest["state"] = state
        manifest["updated_at"] = _now()
        _atomic_json(_manifest_path(job_dir), manifest)
    return True


def project_clip(output_dir: str, job_id: str, clip_index: int) -> tuple[str, dict, str] | None:
    loaded = load_project(output_dir, job_id)
    if not loaded:
        return None
    job_dir, manifest = loaded
    for clip in (manifest.get("state") or {}).get("clips") or []:
        if isinstance(clip, dict) and clip.get("index") == clip_index:
            filename = os.path.basename(str(clip.get("server_file") or ""))
            path = os.path.join(job_dir, filename)
            if filename and os.path.isfile(path):
                return job_dir, clip, path
    return None


def remove_project(output_dir: str, job_id: str) -> bool:
    loaded = load_project(output_dir, job_id)
    if not loaded:
        return False
    job_dir, _manifest = loaded
    with _lock(job_id):
        shutil.rmtree(job_dir)
    return True


def directory_size(path: str) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total

