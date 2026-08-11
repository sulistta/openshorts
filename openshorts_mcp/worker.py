"""Isolated workers for long-running local OpenShorts MCP jobs.

Workers intentionally run in child processes. Their logs can never corrupt the
stdio JSON-RPC stream used by the MCP server, and expensive local models do not
share mutable state with the protocol process.
"""

from __future__ import annotations

import argparse
import traceback
import uuid
from pathlib import Path
from typing import Any, Callable

from . import media
from .store import Store


def _artifact_id() -> str:
    return f"artifact-{uuid.uuid4().hex}"


def _artifact_path(store: Store, project_id: str, artifact_id: str) -> Path:
    return store.project_path(project_id, f"artifacts/{artifact_id}.mp4")


def _source_path(store: Store, project: dict[str, Any]) -> Path:
    source = project.get("source")
    if not isinstance(source, dict) or not source.get("relative_path"):
        raise RuntimeError("Project source is not ready.")
    return store.project_path(str(project["project_id"]), str(source["relative_path"]))


def _artifact_record(
    *,
    artifact_id: str,
    relative_path: str,
    kind: str,
    parent: dict[str, Any] | None,
    source_range: dict[str, float],
    output_format: str,
    layout: str,
    duration_seconds: float,
    layers: list[dict[str, Any]],
    operation: dict[str, Any] | None = None,
    sidecars: list[str] | None = None,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "artifact_id": artifact_id,
        "relative_path": relative_path,
        "kind": kind,
        "parent_artifact_id": parent.get("artifact_id") if parent else None,
        "source_range": source_range,
        "output_format": output_format,
        "layout": layout,
        "duration_seconds": round(float(duration_seconds), 3),
        "layers": layers,
    }
    if operation:
        record["operation"] = operation
    if sidecars:
        record["sidecars"] = sidecars
    return record


def _update_project_status(store: Store, project_id: str, status: str, **extra: Any) -> None:
    def update(project: dict[str, Any]) -> None:
        project["status"] = status
        project.update(extra)

    store.update_project(project_id, update)


def _import_media(store: Store, job_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    project_id = str(payload["project_id"])
    project_dir = store.project_dir(project_id)
    source_dir = project_dir / "source"
    store.append_job_log(job_id, "Copying or downloading the source into the project.")
    if payload.get("source_path"):
        source, original_name = media.copy_local_source(str(payload["source_path"]), source_dir)
        source_kind = "local_file"
        source_url = None
    else:
        source, original_name = media.download_public_source(str(payload["source_url"]), source_dir)
        source_kind = "public_url"
        source_url = str(payload["source_url"])

    metadata = media.probe_media(source)
    relative_source = str(source.relative_to(project_dir))

    def set_imported(project: dict[str, Any]) -> None:
        project["status"] = "analyzing"
        project["source"] = {
            "kind": source_kind,
            "original_name": original_name,
            "source_url": source_url,
            "relative_path": relative_source,
        }
        project["media"] = metadata

    store.update_project(project_id, set_imported)
    store.append_job_log(job_id, "Creating the local contact sheet.")
    contact_relative = "analysis/contact-sheet.jpg"
    selected = media.create_contact_sheet(
        source,
        store.project_path(project_id, contact_relative),
        float(metadata["duration_seconds"]),
        count=12,
    )
    store.append_job_log(job_id, "Transcribing speech locally with faster-whisper.")
    transcript = media.transcribe_source(source)
    transcript_relative = "analysis/transcript.json"
    store.write_project_json(project_id, transcript_relative, transcript)

    def complete(project: dict[str, Any]) -> None:
        project["status"] = "ready"
        project["analysis"] = {
            "transcript_path": transcript_relative,
            "contact_sheet_path": contact_relative,
            "contact_sheet_timestamps": selected,
            "transcript_language": transcript.get("language"),
            "transcript_segment_count": len(transcript.get("segments") or []),
        }

    project = store.update_project(project_id, complete)
    store.append_job_log(job_id, "Import and local analysis complete.")
    return {"project": store.public_project(project)}


def _render_clips(store: Store, job_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    project_id = str(payload["project_id"])
    project = store.get_project(project_id)
    source = _source_path(store, project)
    source_duration = float((project.get("media") or {}).get("duration_seconds") or 0)
    artifacts: list[dict[str, Any]] = []
    clips = list(payload.get("clips") or [])
    for index, clip in enumerate(clips, 1):
        start = float(clip["start_seconds"])
        end = float(clip["end_seconds"])
        duration = end - start
        if duration < 15 or duration > 60 or start < 0 or end > source_duration + 0.01:
            raise RuntimeError(
                f"Clip {index} must be inside the source and between 15 and 60 seconds."
            )
        artifact_id = _artifact_id()
        relative_path = f"artifacts/{artifact_id}.mp4"
        output = _artifact_path(store, project_id, artifact_id)
        store.append_job_log(job_id, f"Rendering clip {index}/{len(clips)}.")
        details = media.render_clip(
            source,
            output,
            start_seconds=start,
            end_seconds=end,
            output_format=str(clip["output_format"]),
            layout=str(clip["layout"]),
        )
        record = _artifact_record(
            artifact_id=artifact_id,
            relative_path=relative_path,
            kind="clip",
            parent=None,
            source_range={"start_seconds": round(start, 3), "end_seconds": round(end, 3)},
            output_format=str(clip["output_format"]),
            layout=str(clip["layout"]),
            duration_seconds=float(details["duration_seconds"]),
            layers=[],
            operation={"label": str(clip.get("label") or f"Clip {index}")[:120]},
        )
        store.add_artifact(project_id, record)
        artifacts.append(store.public_artifact(project_id, record))
    store.append_job_log(job_id, f"Rendered {len(artifacts)} immutable artifact(s).")
    return {"project_id": project_id, "artifacts": artifacts}


def _effect_artifact(store: Store, job_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    project_id = str(payload["project_id"])
    project, parent = store.find_artifact(str(payload["artifact_id"]))
    if project["project_id"] != project_id:
        raise RuntimeError("Artifact does not belong to the requested project.")
    input_path = store.project_path(project_id, str(parent["relative_path"]))
    artifact_id = _artifact_id()
    relative_path = f"artifacts/{artifact_id}.mp4"
    output = _artifact_path(store, project_id, artifact_id)
    has_text = any(layer.get("type") in {"subtitles", "hook"} for layer in parent.get("layers") or [])
    store.append_job_log(job_id, "Applying validated structured effects.")
    applied = media.apply_effects(
        input_path,
        output,
        list(payload.get("edits") or []),
        has_text_layers=has_text,
    )
    record = _artifact_record(
        artifact_id=artifact_id,
        relative_path=relative_path,
        kind="effects",
        parent=parent,
        source_range=dict(parent["source_range"]),
        output_format=str(parent["output_format"]),
        layout=str(parent["layout"]),
        duration_seconds=float(media.probe_media(output)["duration_seconds"]),
        layers=list(parent.get("layers") or []) + [{"type": "effects", "edits": applied}],
        operation={"edits": applied},
    )
    store.add_artifact(project_id, record)
    return {"project_id": project_id, "artifact": store.public_artifact(project_id, record)}


def _subtitle_artifact(store: Store, job_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    project_id = str(payload["project_id"])
    project, parent = store.find_artifact(str(payload["artifact_id"]))
    if project["project_id"] != project_id:
        raise RuntimeError("Artifact does not belong to the requested project.")
    analysis = project.get("analysis") or {}
    transcript_relative = analysis.get("transcript_path")
    if not transcript_relative:
        raise RuntimeError("This project has no local transcript.")
    transcript_path = store.project_path(project_id, str(transcript_relative))
    try:
        import json

        transcript = json.loads(transcript_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError("Could not read the project transcript.") from exc
    source_range = dict(parent["source_range"])
    artifact_id = _artifact_id()
    relative_path = f"artifacts/{artifact_id}.mp4"
    output = _artifact_path(store, project_id, artifact_id)
    store.append_job_log(job_id, "Burning explicit subtitles.")
    ass_path = media.burn_subtitles(
        store.project_path(project_id, str(parent["relative_path"])),
        output,
        transcript,
        source_start=float(source_range["start_seconds"]),
        source_end=float(source_range["end_seconds"]),
        style_options=dict(payload.get("style_options") or {}),
    )
    relative_ass = str(Path(ass_path).relative_to(store.project_dir(project_id)))
    record = _artifact_record(
        artifact_id=artifact_id,
        relative_path=relative_path,
        kind="subtitles",
        parent=parent,
        source_range=source_range,
        output_format=str(parent["output_format"]),
        layout=str(parent["layout"]),
        duration_seconds=float(media.probe_media(output)["duration_seconds"]),
        layers=list(parent.get("layers") or []) + [{"type": "subtitles", "style": payload.get("style_options") or {}}],
        operation={"style_options": payload.get("style_options") or {}},
        sidecars=[relative_ass],
    )
    store.add_artifact(project_id, record)
    return {"project_id": project_id, "artifact": store.public_artifact(project_id, record)}


def _hook_artifact(store: Store, job_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    project_id = str(payload["project_id"])
    project, parent = store.find_artifact(str(payload["artifact_id"]))
    if project["project_id"] != project_id:
        raise RuntimeError("Artifact does not belong to the requested project.")
    artifact_id = _artifact_id()
    relative_path = f"artifacts/{artifact_id}.mp4"
    output = _artifact_path(store, project_id, artifact_id)
    store.append_job_log(job_id, "Burning the explicit hook overlay.")
    media.add_hook_overlay(
        store.project_path(project_id, str(parent["relative_path"])),
        output,
        str(payload["text"]),
        position=str(payload.get("position") or "top"),
        style_options=dict(payload.get("style_options") or {}),
    )
    record = _artifact_record(
        artifact_id=artifact_id,
        relative_path=relative_path,
        kind="hook_overlay",
        parent=parent,
        source_range=dict(parent["source_range"]),
        output_format=str(parent["output_format"]),
        layout=str(parent["layout"]),
        duration_seconds=float(media.probe_media(output)["duration_seconds"]),
        layers=list(parent.get("layers") or [])
        + [
            {
                "type": "hook",
                "text": str(payload["text"]),
                "position": str(payload.get("position") or "top"),
                "style": payload.get("style_options") or {},
            }
        ],
        operation={
            "text": str(payload["text"]),
            "position": str(payload.get("position") or "top"),
            "style_options": payload.get("style_options") or {},
        },
    )
    store.add_artifact(project_id, record)
    return {"project_id": project_id, "artifact": store.public_artifact(project_id, record)}


HANDLERS: dict[str, Callable[[Store, str, dict[str, Any]], dict[str, Any]]] = {
    "import_media": _import_media,
    "render_clips": _render_clips,
    "apply_effects": _effect_artifact,
    "add_subtitles": _subtitle_artifact,
    "add_hook_overlay": _hook_artifact,
}


def run_job(root: str, job_id: str) -> int:
    store = Store(root)
    store.initialize()
    job = store.get_job(job_id)
    handler = HANDLERS.get(str(job.get("type")))
    if not handler:
        store.update_job(job_id, status="failed", error=f"Unsupported job type: {job.get('type')}")
        return 2
    store.update_job(job_id, status="running", error=None)
    try:
        result = handler(store, job_id, dict(job.get("payload") or {}))
        store.update_job(job_id, status="completed", result=result, error=None)
        return 0
    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"
        try:
            project_id = (job.get("payload") or {}).get("project_id")
            if project_id:
                _update_project_status(store, str(project_id), "ready", last_error=message)
        except Exception:
            pass
        store.update_job(job_id, status="failed", error=message)
        traceback.print_exc()
        return 1


def main() -> None:
    parser = argparse.ArgumentParser(description="OpenShorts MCP isolated job worker")
    parser.add_argument("--root", required=True)
    parser.add_argument("--job", required=True)
    args = parser.parse_args()
    raise SystemExit(run_job(args.root, args.job))


if __name__ == "__main__":
    main()
