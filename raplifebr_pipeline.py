"""Durable Instagram-first content pipeline for the raplifebr project.

This module owns local queue state and the OpenShorts processing hand-off. It
intentionally stops at ``ready_to_publish``: browser automation is a separate
boundary so login, 2FA, CAPTCHA, and security challenges remain user-visible.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import tempfile
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import parse_qs, unquote, urlparse, urlunparse
from urllib.request import Request, urlopen


STATUSES = {
    "candidate",
    "processing",
    "processed",
    "ready_to_publish",
    "publishing",
    "published",
    "failed",
}
ACTIVE_STATUSES = {"processing", "processed", "ready_to_publish", "publishing", "published"}


class PipelineError(RuntimeError):
    """An expected pipeline failure that should be recorded on the item."""


class RightsNotConfirmed(PipelineError):
    """The source has no local approval for processing."""


class DuplicateClip(PipelineError):
    """The selected artist/track/timestamp was already used."""


class HumanInterventionRequired(PipelineError):
    """Instagram needs a visible user action such as login or 2FA."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_registry() -> dict[str, Any]:
    return {"schema_version": 1, "updated_at": utc_now(), "items": []}


def default_registry_path() -> Path:
    data_dir = Path(os.environ.get("OPENSHORTS_DATA_DIR", "output"))
    return data_dir / "raplifebr" / "instagram-publications.json"


def load_registry(path: str | os.PathLike[str]) -> dict[str, Any]:
    registry_path = Path(path)
    if not registry_path.exists():
        return new_registry()
    with registry_path.open("r", encoding="utf-8") as handle:
        registry = json.load(handle)
    if not isinstance(registry, dict) or not isinstance(registry.get("items"), list):
        raise PipelineError(f"Invalid registry format: {registry_path}")
    registry.setdefault("schema_version", 1)
    registry.setdefault("updated_at", utc_now())
    return registry


def save_registry(path: str | os.PathLike[str], registry: dict[str, Any]) -> None:
    registry_path = Path(path)
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry["updated_at"] = utc_now()
    fd, temporary = tempfile.mkstemp(prefix=f".{registry_path.name}.", dir=registry_path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(registry, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, registry_path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _normalise_source_url(source_url: str) -> str:
    source_url = str(source_url or "").strip()
    parsed = urlparse(source_url)
    host = parsed.netloc.lower().split(":", 1)[0]
    if host in {"youtu.be", "youtube.com", "www.youtube.com", "m.youtube.com"}:
        query = parse_qs(parsed.query)
        video_id = (query.get("v") or [""])[0]
        if not video_id and host == "youtu.be":
            video_id = parsed.path.strip("/").split("/", 1)[0]
        if video_id:
            return f"https://www.youtube.com/watch?v={video_id}"
    return urlunparse((parsed.scheme.lower(), parsed.netloc, parsed.path.rstrip("/"), "", parsed.query, ""))


def _digest(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _source_key(candidate: dict[str, Any]) -> str:
    return _digest([
        str(candidate.get("artist", "")).strip().casefold(),
        str(candidate.get("track", "")).strip().casefold(),
        _normalise_source_url(candidate.get("source_url", "")),
    ])


def _clip_key(artist: str, track: str, start: float, end: float) -> str:
    return _digest([
        str(artist).strip().casefold(),
        str(track).strip().casefold(),
        round(float(start), 3),
        round(float(end), 3),
    ])


def add_candidate(registry: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    required = ("artist", "track", "source_url")
    missing = [field for field in required if not str(candidate.get(field, "")).strip()]
    if missing:
        raise PipelineError(f"Candidate missing required fields: {', '.join(missing)}")
    source_key = _source_key(candidate)
    for item in registry["items"]:
        if item.get("source_key") == source_key:
            return item
    item = {
        "id": source_key[:20],
        "source_key": source_key,
        "artist": str(candidate["artist"]).strip(),
        "track": str(candidate["track"]).strip(),
        "source_url": str(candidate["source_url"]).strip(),
        "rights_status": candidate.get("rights_status", "needs_license"),
        "license_proof": candidate.get("license_proof"),
        "status": "candidate",
        "openshorts_job_id": None,
        "clip_start": None,
        "clip_end": None,
        "final_file": None,
        "generated_at": None,
        "caption": None,
        "instagram_url": None,
        "validation": None,
        "error": None,
        "history": [{"status": "candidate", "at": utc_now()}],
    }
    registry["items"].append(item)
    return item


def approve_items(
    registry: dict[str, Any],
    ids: list[str] | None = None,
    basis: str = "explicit_user_attestation",
) -> int:
    """Record an explicit rights approval without changing pipeline status."""
    selected = set(ids) if ids else None
    count = 0
    for item in registry.get("items", []):
        if selected is not None and item.get("id") not in selected:
            continue
        item["rights_status"] = "approved"
        item["rights_basis"] = basis
        item["rights_confirmed_at"] = utc_now()
        item["error"] = None
        item.setdefault("history", []).append({"status": "rights_approved", "at": item["rights_confirmed_at"]})
        count += 1
    return count


def transition(item: dict[str, Any], status: str, **updates: Any) -> dict[str, Any]:
    if status not in STATUSES:
        raise PipelineError(f"Unknown pipeline status: {status}")
    item.update(updates)
    item["status"] = status
    item.setdefault("history", []).append({"status": status, "at": utc_now()})
    return item


def has_duplicate_clip(
    registry: dict[str, Any],
    artist: str,
    track: str,
    start: float,
    end: float,
    exclude_id: str | None = None,
) -> bool:
    key = _clip_key(artist, track, start, end)
    for item in registry.get("items", []):
        if item.get("id") == exclude_id or item.get("status") == "failed":
            continue
        item_key = item.get("clip_dedupe_key")
        if not item_key and item.get("clip_start") is not None and item.get("clip_end") is not None:
            item_key = _clip_key(item.get("artist", ""), item.get("track", ""), item["clip_start"], item["clip_end"])
        if item_key == key:
            return True
    return False


def _hashtag(value: str) -> str:
    plain = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    plain = re.sub(r"[^A-Za-z0-9]", "", plain).lower()
    return f"#{plain}" if plain else "#rapbr"


def caption_for(artist: str, track: str, hook: str = "", variant: int = 0) -> str:
    artist = str(artist).strip()
    track = str(track).strip()
    hook = str(hook or "").strip()
    templates = (
        "Esse trecho de {track}, do {artist} 🔥\n{hook}\nQual nota pra essa?\n#rap #trap #trapbr #rapbr {hashtag}",
        "{artist} entregou nessa.\n{track} — {hook}\nQual foi a melhor parte? 👀\n#rapbr #trapbr {hashtag}",
        "Quando entra esse trecho de {track}, não tem como ficar parado 🔥\nVocê já conhecia?\n#rap #trapnacional #trapbr {hashtag}",
    )
    template = templates[int(variant) % len(templates)]
    return template.format(
        artist=artist,
        track=track,
        hook=hook or "O momento que fica na cabeça.",
        hashtag=_hashtag(artist),
    )


def validate_probe(
    probe: dict[str, Any],
    *,
    decode_ok: bool,
    min_duration: float = 3.0,
    max_duration: float = 90.0,
    aspect_tolerance: float = 0.03,
) -> dict[str, Any]:
    width = int(probe.get("width") or 0)
    height = int(probe.get("height") or 0)
    duration = float(probe.get("duration") or 0)
    aspect = width / height if width and height else 0.0
    checks = {
        "exists": bool(probe.get("exists", True)),
        "decode": bool(decode_ok),
        "duration": min_duration <= duration <= max_duration,
        "vertical": bool(width and height and height > width and abs(aspect - 9 / 16) <= aspect_tolerance),
        "audio": bool(probe.get("has_audio")),
    }
    return {
        "valid": all(checks.values()),
        "checks": checks,
        "width": width,
        "height": height,
        "duration_seconds": round(duration, 3),
        "aspect_ratio": round(aspect, 5) if aspect else None,
        "manual_review_required": ["captions_not_cut", "opening_and_ending_make_sense"],
        "probe_error": probe.get("probe_error"),
    }


def _probe_video(path: Path) -> tuple[dict[str, Any], bool, str | None]:
    if not path.is_file() or path.stat().st_size <= 0:
        return {"exists": False}, False, "file_missing_or_empty"
    probe_command = [
        "ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)
    ]
    try:
        probe = subprocess.run(probe_command, capture_output=True, text=True, timeout=30)
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return {"exists": True}, False, str(exc)
    if probe.returncode != 0:
        return {"exists": True, "probe_error": probe.stderr[-500:]}, False, probe.stderr[-500:]
    try:
        data = json.loads(probe.stdout)
        streams = data.get("streams") or []
        video = next((stream for stream in streams if stream.get("codec_type") == "video"), {})
        duration = video.get("duration") or (data.get("format") or {}).get("duration") or 0
        values = {
            "exists": True,
            "width": video.get("width"),
            "height": video.get("height"),
            "duration": duration,
            "has_audio": any(stream.get("codec_type") == "audio" for stream in streams),
        }
        return values, True, None
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        return {"exists": True}, False, str(exc)


def validate_video(path: str | os.PathLike[str]) -> dict[str, Any]:
    video_path = Path(path)
    probe, probe_ok, probe_error = _probe_video(video_path)
    decode_ok = False
    decode_error = None
    if probe_ok:
        try:
            decoded = subprocess.run(
                ["ffmpeg", "-v", "error", "-i", str(video_path), "-f", "null", "-"],
                capture_output=True,
                text=True,
                timeout=120,
            )
            decode_ok = decoded.returncode == 0
            if not decode_ok:
                decode_error = decoded.stderr[-500:]
        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            decode_error = str(exc)
    result = validate_probe(probe, decode_ok=decode_ok)
    result["path"] = str(video_path)
    result["probe_error"] = probe_error or result.get("probe_error")
    result["decode_error"] = decode_error
    return result


def resolve_video_path(video_url: str, output_root: str | os.PathLike[str] | None, job_id: str) -> Path:
    parsed = urlparse(str(video_url or ""))
    if parsed.scheme == "file":
        return Path(unquote(parsed.path)).resolve()
    if parsed.scheme in {"http", "https"}:
        relative = parsed.path
    else:
        relative = str(video_url or "")
    if os.path.isabs(relative) and relative.endswith(".mp4"):
        return Path(relative).resolve()
    filename = Path(relative).name
    if not filename:
        raise PipelineError("OpenShorts clip has no video URL")
    root = Path(output_root or os.environ.get("OPENSHORTS_DATA_DIR", "output"))
    return (root / "output" / job_id / filename).resolve()


def select_best_clip(clips: Iterable[dict[str, Any]]) -> dict[str, Any]:
    candidates = [clip for clip in clips if isinstance(clip, dict)]
    if not candidates:
        raise PipelineError("OpenShorts returned no clips")
    return dict(max(candidates, key=lambda clip: float(clip.get("predicted_score") or clip.get("score") or 0)))


def process_candidate(
    item: dict[str, Any],
    registry: dict[str, Any],
    mcp: Any,
    *,
    output_root: str | os.PathLike[str] | None = None,
    validate: Callable[[str | os.PathLike[str]], dict[str, Any]] = validate_video,
    sleep: Callable[[float], None] = time.sleep,
    max_polls: int = 120,
    poll_interval: float = 30.0,
    add_subtitles: bool = True,
) -> dict[str, Any]:
    if item.get("rights_status") != "approved" or not (item.get("license_proof") or item.get("rights_basis")):
        raise RightsNotConfirmed(
            f"{item.get('artist')} — {item.get('track')} has no local rights approval"
        )
    if item.get("status") == "published":
        raise PipelineError("Published item cannot be processed again")

    transition(item, "processing", error=None)
    try:
        started = mcp.process_video(
            item["source_url"],
            confirm_rights=True,
            layouts=["auto", "punch_in"],
            output_format="vertical",
        )
        job_id = started.get("job_id")
        if not job_id:
            raise PipelineError("OpenShorts did not return a job_id")
        item["openshorts_job_id"] = job_id

        status_data = None
        for attempt in range(max_polls):
            status_data = mcp.get_job_status(job_id)
            status = status_data.get("status")
            if status == "completed":
                break
            if status == "failed":
                raise PipelineError("OpenShorts job failed: " + "; ".join(status_data.get("recent_logs") or []))
            if attempt + 1 == max_polls:
                raise PipelineError("OpenShorts job polling timed out")
            sleep(poll_interval)

        listed = mcp.list_clips(job_id)
        selected = select_best_clip(listed.get("clips") or [])
        if add_subtitles:
            subtitled = mcp.add_subtitles(
                job_id,
                int(selected.get("index", 0)),
                style="karaoke",
                position="bottom",
                font_size=16,
                uppercase=False,
            )
            selected["video_url"] = subtitled.get("new_video_url") or subtitled.get("video_url") or selected.get("video_url")

        start = float(selected["start"])
        end = float(selected["end"])
        if has_duplicate_clip(registry, item["artist"], item["track"], start, end, exclude_id=item["id"]):
            raise DuplicateClip(f"Duplicate clip for {item['artist']} / {item['track']} at {start:.3f}-{end:.3f}")
        video_path = resolve_video_path(selected.get("video_url", ""), output_root, job_id)
        validation = validate(video_path)
        item.update({
            "clip_start": start,
            "clip_end": end,
            "clip_dedupe_key": _clip_key(item["artist"], item["track"], start, end),
            "final_file": str(video_path),
            "generated_at": utc_now(),
            "validation": validation,
            "caption": caption_for(
                item["artist"], item["track"], selected.get("viral_hook_text", ""),
                variant=len(item.get("history", [])),
            ),
        })
        if not validation.get("valid"):
            raise PipelineError("Final video validation failed")
        transition(item, "processed")
        transition(item, "ready_to_publish")
        return item
    except RightsNotConfirmed:
        raise
    except Exception as exc:
        transition(item, "failed", error=str(exc))
        raise


def publish_item(
    item: dict[str, Any],
    registry: dict[str, Any],
    publisher: Callable[[str, str], str],
) -> dict[str, Any]:
    """Publish one ready item through an injected browser adapter.

    The adapter must return the public Instagram URL. It may raise
    ``HumanInterventionRequired`` when Instagram shows login, 2FA, CAPTCHA, or
    a security challenge; those cases deliberately remain retryable.
    """
    if item.get("status") != "ready_to_publish":
        raise PipelineError("Only ready_to_publish items can be published")
    final_file = str(item.get("final_file") or "")
    caption = str(item.get("caption") or "")
    if not final_file or not caption:
        raise PipelineError("Ready item is missing final_file or caption")

    transition(item, "publishing", error=None)
    try:
        instagram_url = str(publisher(final_file, caption) or "").strip()
        if not instagram_url.startswith("https://www.instagram.com/"):
            raise PipelineError("Browser adapter did not return an Instagram URL")
        transition(item, "published", instagram_url=instagram_url, published_at=utc_now(), error=None)
        return item
    except HumanInterventionRequired as exc:
        transition(item, "ready_to_publish", error=str(exc))
        raise
    except Exception as exc:
        transition(item, "failed", error=str(exc))
        raise


def process_batch(
    registry: dict[str, Any],
    mcp: Any,
    *,
    limit: int = 10,
    publisher: Callable[[str, str], str] | None = None,
    output_root: str | os.PathLike[str] | None = None,
    validate: Callable[[str | os.PathLike[str]], dict[str, Any]] = validate_video,
    sleep: Callable[[float], None] = time.sleep,
    max_polls: int = 120,
    poll_interval: float = 30.0,
    add_subtitles: bool = True,
) -> dict[str, list[str]]:
    """Process a bounded Instagram-only batch and isolate per-item failures."""
    result = {"processed": [], "published": [], "blocked": [], "failed": []}
    candidates = [
        item for item in registry.get("items", [])
        if item.get("status") in {"candidate", "failed"}
    ][:max(0, int(limit))]
    for item in candidates:
        try:
            process_candidate(
                item,
                registry,
                mcp,
                output_root=output_root,
                validate=validate,
                sleep=sleep,
                max_polls=max_polls,
                poll_interval=poll_interval,
                add_subtitles=add_subtitles,
            )
            result["processed"].append(item["id"])
            if publisher is not None:
                publish_item(item, registry, publisher)
                result["published"].append(item["id"])
        except RightsNotConfirmed as exc:
            item["error"] = str(exc)
            result["blocked"].append(item["id"])
        except Exception as exc:
            item["error"] = str(exc)
            result["failed"].append(item["id"])
    return result


class MCPClient:
    """Small stdlib JSON-RPC client for the local OpenShorts MCP endpoint."""

    def __init__(self, endpoint: str = "http://127.0.0.1:37831/mcp", api_key: str | None = None):
        self.endpoint = endpoint
        self.api_key = api_key
        self._request_id = 0

    def _call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self._request_id += 1
        payload = {
            "jsonrpc": "2.0",
            "id": self._request_id,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.api_key:
            headers["X-Gemini-Key"] = self.api_key
        request = Request(self.endpoint, data=json.dumps(payload).encode(), headers=headers, method="POST")
        try:
            with urlopen(request, timeout=300) as response:
                envelope = json.load(response)
        except Exception as exc:
            raise PipelineError(f"OpenShorts MCP request failed: {exc}") from exc
        result = envelope.get("result") or {}
        if result.get("isError"):
            raise PipelineError(str(result.get("structuredContent") or result.get("content") or result))
        return result.get("structuredContent") or {}

    def process_video(self, source_url: str, **kwargs: Any) -> dict[str, Any]:
        return self._call("process_video", {"source_url": source_url, **kwargs})

    def get_job_status(self, job_id: str) -> dict[str, Any]:
        return self._call("get_job_status", {"job_id": job_id})

    def list_clips(self, job_id: str) -> dict[str, Any]:
        return self._call("list_clips", {"job_id": job_id})

    def add_subtitles(self, job_id: str, clip_index: int, **kwargs: Any) -> dict[str, Any]:
        return self._call("add_subtitles", {"job_id": job_id, "clip_index": clip_index, **kwargs})


def import_candidates(registry: dict[str, Any], candidates_path: str | os.PathLike[str]) -> int:
    with Path(candidates_path).open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    before = len(registry["items"])
    for candidate in payload.get("candidates", []):
        if candidate.get("source_url") and candidate.get("track"):
            add_candidate(registry, candidate)
    return len(registry["items"]) - before


def _cli() -> int:
    parser = argparse.ArgumentParser(description="raplifebr Instagram content pipeline")
    parser.add_argument("command", choices=("seed", "approve", "process", "batch"))
    parser.add_argument("--registry", default=str(default_registry_path()))
    parser.add_argument("--candidates", default="research/raplifebr-music-candidates.json")
    parser.add_argument("--candidate-id")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--mcp-endpoint", default=os.environ.get("OPENSHORTS_MCP_URL", "http://127.0.0.1:37831/mcp"))
    parser.add_argument("--output-root", default=os.environ.get("OPENSHORTS_DATA_DIR", "output"))
    parser.add_argument("--poll-interval", type=float, default=30.0)
    parser.add_argument("--max-polls", type=int, default=120)
    parser.add_argument("--no-subtitles", action="store_true")
    args = parser.parse_args()

    registry = load_registry(args.registry)
    if args.command == "seed":
        added = import_candidates(registry, args.candidates)
        save_registry(args.registry, registry)
        print(json.dumps({"added": added, "total": len(registry["items"]), "registry": args.registry}, ensure_ascii=False))
        return 0

    if args.command == "approve":
        ids = [args.candidate_id] if args.candidate_id else None
        approved = approve_items(registry, ids=ids)
        save_registry(args.registry, registry)
        print(json.dumps({"approved": approved, "registry": args.registry}, ensure_ascii=False))
        return 0

    client = MCPClient(args.mcp_endpoint, os.environ.get("GEMINI_API_KEY"))
    if args.command == "batch":
        result = process_batch(
            registry,
            client,
            limit=args.limit,
            output_root=args.output_root,
            poll_interval=args.poll_interval,
            max_polls=args.max_polls,
            add_subtitles=not args.no_subtitles,
        )
        save_registry(args.registry, registry)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if not result["failed"] else 1

    items = registry["items"]
    item = next((entry for entry in items if args.candidate_id and entry["id"] == args.candidate_id), None)
    if item is None:
        item = next((entry for entry in items if entry.get("status") in {"candidate", "failed"}), None)
    if item is None:
        raise PipelineError("No candidate available")
    try:
        result = process_candidate(
            item,
            registry,
            MCPClient(args.mcp_endpoint, os.environ.get("GEMINI_API_KEY")),
            output_root=args.output_root,
            poll_interval=args.poll_interval,
            max_polls=args.max_polls,
            add_subtitles=not args.no_subtitles,
        )
    except RightsNotConfirmed as exc:
        print(json.dumps({"status": item["status"], "error": str(exc)}, ensure_ascii=False))
        return 2
    except PipelineError as exc:
        print(json.dumps({"status": item["status"], "error": str(exc)}, ensure_ascii=False))
        return 1
    finally:
        save_registry(args.registry, registry)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
