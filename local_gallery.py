"""Persistent local actor and UGC gallery storage."""

from __future__ import annotations

import copy
import json
import os
import re
import shutil
import tempfile
import uuid
from datetime import datetime, timezone


_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_json(path: str, data: dict) -> None:
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".gallery-", suffix=".tmp", dir=directory)
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
            data = json.load(handle)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError, TypeError):
        return None


def _valid_id(value: str) -> bool:
    return bool(isinstance(value, str) and _ID_RE.fullmatch(value))


def _root(output_dir: str) -> str:
    path = os.path.realpath(os.path.join(output_dir, "gallery"))
    os.makedirs(os.path.join(path, "actors"), exist_ok=True)
    os.makedirs(os.path.join(path, "videos"), exist_ok=True)
    return path


def _safe_child(parent: str, name: str) -> str | None:
    if not _valid_id(name):
        return None
    root = os.path.realpath(parent)
    target = os.path.realpath(os.path.join(root, name))
    return target if target.startswith(root + os.sep) else None


def save_actor(output_dir: str, source_path: str, description: str = "") -> dict | None:
    if not source_path or not os.path.isfile(source_path):
        return None
    actor_id = uuid.uuid4().hex[:12]
    extension = os.path.splitext(source_path)[1].lower() or ".png"
    if extension not in {".png", ".jpg", ".jpeg", ".webp"}:
        extension = ".png"
    root = _root(output_dir)
    actors = os.path.join(root, "actors")
    filename = f"{actor_id}{extension}"
    destination = os.path.join(actors, filename)
    try:
        shutil.copy2(source_path, destination)
        data = {
            "id": actor_id,
            "url": f"/videos/gallery/actors/{filename}",
            "key": actor_id,
            "description": description or "",
            "created_at": _now(),
        }
        _atomic_json(os.path.join(actors, f"{actor_id}.json"), data)
        return data
    except Exception:
        try:
            os.remove(destination)
        except OSError:
            pass
        return None


def list_actors(output_dir: str) -> list[dict]:
    actors = os.path.join(_root(output_dir), "actors")
    result = []
    for name in os.listdir(actors):
        if not name.endswith(".json"):
            continue
        data = _read(os.path.join(actors, name))
        if not data or not _valid_id(str(data.get("id", ""))):
            continue
        actor_id = data["id"]
        image = next((n for n in os.listdir(actors)
                      if n.startswith(actor_id + ".") and not n.endswith(".json")), None)
        if not image:
            continue
        item = dict(data)
        item["url"] = f"/videos/gallery/actors/{image}"
        item["key"] = actor_id
        result.append(item)
    result.sort(key=lambda item: item.get("created_at") or "", reverse=True)
    return result


def delete_actor(output_dir: str, actor_id: str) -> bool:
    actors = os.path.join(_root(output_dir), "actors")
    directory = _safe_child(actors, actor_id)
    if directory is None:
        return False
    removed = False
    for name in os.listdir(actors):
        if name.startswith(actor_id + "."):
            try:
                os.remove(os.path.join(actors, name))
                removed = True
            except OSError:
                pass
    return removed


def save_video(output_dir: str, video_path: str, actor_path: str | None,
               metadata: dict, video_id: str | None = None) -> dict | None:
    if not video_path or not os.path.isfile(video_path):
        return None
    video_id = video_id or uuid.uuid4().hex[:12]
    if not _valid_id(video_id):
        return None
    root = _root(output_dir)
    videos = os.path.join(root, "videos")
    destination = _safe_child(videos, video_id)
    if destination is None:
        return None
    temp = destination + ".tmp"
    shutil.rmtree(temp, ignore_errors=True)
    os.makedirs(temp, exist_ok=True)
    try:
        shutil.copy2(video_path, os.path.join(temp, "video.mp4"))
        actor_url = ""
        if actor_path and os.path.isfile(actor_path):
            actor_ext = os.path.splitext(actor_path)[1].lower() or ".png"
            if actor_ext not in {".png", ".jpg", ".jpeg", ".webp"}:
                actor_ext = ".png"
            actor_name = f"actor{actor_ext}"
            shutil.copy2(actor_path, os.path.join(temp, actor_name))
            actor_url = f"/videos/gallery/videos/{video_id}/{actor_name}"
        data = copy.deepcopy(metadata or {})
        data.update({
            "video_id": video_id,
            "video_url": f"/videos/gallery/videos/{video_id}/video.mp4",
            "actor_url": actor_url,
            "created_at": data.get("created_at") or _now(),
        })
        _atomic_json(os.path.join(temp, "metadata.json"), data)
        if os.path.exists(destination):
            shutil.rmtree(destination)
        os.replace(temp, destination)
        data["metadata_url"] = f"/videos/gallery/videos/{video_id}/metadata.json"
        return data
    except Exception:
        shutil.rmtree(temp, ignore_errors=True)
        return None


def list_videos(output_dir: str, limit: int = 50) -> list[dict]:
    videos = os.path.join(_root(output_dir), "videos")
    result = []
    for video_id in os.listdir(videos):
        directory = _safe_child(videos, video_id)
        if directory is None or not os.path.isdir(directory):
            continue
        data = _read(os.path.join(directory, "metadata.json"))
        if not data or not _valid_id(str(data.get("video_id", video_id))):
            continue
        if not os.path.isfile(os.path.join(directory, "video.mp4")):
            continue
        data["video_id"] = str(data.get("video_id") or video_id)
        data["video_url"] = f"/videos/gallery/videos/{video_id}/video.mp4"
        actor = next((n for n in os.listdir(directory) if n.startswith("actor.")), None)
        data["actor_url"] = (f"/videos/gallery/videos/{video_id}/{actor}" if actor else "")
        data["metadata_url"] = f"/videos/gallery/videos/{video_id}/metadata.json"
        result.append(data)
    result.sort(key=lambda item: item.get("created_at") or "", reverse=True)
    return result[:limit] if limit else result


def delete_video(output_dir: str, video_id: str) -> bool:
    directory = _safe_child(os.path.join(_root(output_dir), "videos"), video_id)
    if directory is None or not os.path.isdir(directory):
        return False
    shutil.rmtree(directory)
    return True

