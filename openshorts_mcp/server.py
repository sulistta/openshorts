"""The OpenShorts stdio MCP surface."""

from __future__ import annotations

import asyncio
import hashlib
import json
import sys
import warnings
from pathlib import Path
from typing import Any

from pydantic_settings.exceptions import IncompleteFieldDefinitionWarning

# pydantic-settings 2.15 emits this third-party forward-reference warning when
# FastMCP builds its own settings object. It is harmless, but a stdio MCP
# server should keep even stderr free of unrelated startup noise.
warnings.filterwarnings(
    "ignore",
    category=IncompleteFieldDefinitionWarning,
)

import anyio
from mcp.shared.message import SessionMessage
from mcp.types import JSONRPCMessage
from mcp.server.fastmcp import FastMCP, Image

from .effects import EFFECT_TYPES
from .jobs import JobManager
from .media import LAYOUTS, OUTPUT_FORMATS, assert_public_url, contact_sheet_timestamps, create_contact_sheet
from .store import Store


INSTRUCTIONS = """OpenShorts MCP is a local, stdio-only video editing server.

Use this workflow:
1. Call import_media with exactly one local path or public URL and confirm_rights=true.
2. Poll get_job_status until the import job is completed.
3. Read the transcript and inspect the contact sheet. You, the LLM, decide the clip ranges.
4. Call render_clips with explicit 15-60 second ranges, output formats and deterministic layouts.
5. Poll the returned job. Every transformation creates a new immutable artifact ID.
6. Optionally apply structured effects, explicit subtitles, or a hook overlay to an artifact.

There is no cloud AI, HTTP endpoint, webhook, publishing integration, browser UI
or automatic subtitles. All results are local absolute paths under
OPENSHORTS_OUTPUT_DIR. Never send raw FFmpeg filters: choose only documented
structured effects and layouts.
"""


def _require_project_ready(store: Store, project_id: str) -> dict[str, Any]:
    project = store.get_project(project_id)
    if not isinstance(project.get("source"), dict) or not project.get("media"):
        raise ValueError("Project import is not complete yet. Poll its import job first.")
    return project


def _read_transcript(store: Store, project: dict[str, Any]) -> dict[str, Any]:
    relative = (project.get("analysis") or {}).get("transcript_path")
    if not relative:
        raise ValueError("No transcript is available yet for this project.")
    path = store.project_path(str(project["project_id"]), str(relative))
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValueError("The local transcript could not be read.") from exc
    if not isinstance(value, dict):
        raise ValueError("The local transcript is invalid.")
    return value


def _validate_clip_requests(project: dict[str, Any], clips: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(clips, list) or not clips:
        raise ValueError("clips must contain at least one requested clip.")
    if len(clips) > 12:
        raise ValueError("At most 12 clips can be rendered in one job.")
    source_duration = float((project.get("media") or {}).get("duration_seconds") or 0)
    validated: list[dict[str, Any]] = []
    for index, clip in enumerate(clips, 1):
        if not isinstance(clip, dict):
            raise ValueError(f"clips[{index}] must be an object.")
        missing = [key for key in ("start_seconds", "end_seconds", "output_format", "layout") if key not in clip]
        if missing:
            raise ValueError(f"clips[{index}] is missing required fields: {', '.join(missing)}.")
        try:
            start = float(clip["start_seconds"])
            end = float(clip["end_seconds"])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"clips[{index}] timestamps must be numbers.") from exc
        if start < 0 or end > source_duration + 0.01 or not 15 <= end - start <= 60:
            raise ValueError(
                f"clips[{index}] must be inside the source and last from 15 to 60 seconds."
            )
        output_format = str(clip["output_format"])
        layout = str(clip["layout"])
        if output_format not in OUTPUT_FORMATS:
            raise ValueError(f"clips[{index}].output_format must be one of {list(OUTPUT_FORMATS)}.")
        if layout not in LAYOUTS:
            raise ValueError(f"clips[{index}].layout must be one of {list(LAYOUTS)}.")
        validated.append(
            {
                "start_seconds": round(start, 3),
                "end_seconds": round(end, 3),
                "output_format": output_format,
                "layout": layout,
                "label": str(clip.get("label") or "")[:120],
            }
        )
    return validated


def create_server(output_dir: str | None = None) -> FastMCP:
    """Create a FastMCP application bound to one local durable store."""
    store = Store(output_dir)
    store.initialize()
    jobs = JobManager(store)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", IncompleteFieldDefinitionWarning)
        mcp = FastMCP("OpenShorts", instructions=INSTRUCTIONS, log_level="WARNING")

    @mcp.tool()
    async def import_media(
        confirm_rights: bool,
        source_path: str | None = None,
        source_url: str | None = None,
    ) -> dict[str, Any]:
        """Import one local file or one public URL, then transcribe it locally.

        The local source is copied into the project. confirm_rights must be true
        because the caller owns the media or has permission to edit it.
        """
        if not confirm_rights:
            raise ValueError("confirm_rights must be true before OpenShorts imports media.")
        if bool(source_path) == bool(source_url):
            raise ValueError("Provide exactly one of source_path or source_url.")
        if source_path:
            local_source = Path(source_path).expanduser().resolve()
            if not local_source.is_file():
                raise ValueError("source_path does not exist or is not a file.")
            source_path = str(local_source)
        if source_url:
            assert_public_url(source_url)
        project = store.create_project()
        payload = {
            "project_id": project["project_id"],
            "source_path": source_path,
            "source_url": source_url,
        }
        job = await jobs.submit("import_media", payload)
        return {
            "project_id": project["project_id"],
            "job": job,
            "next_step": "Poll get_job_status. When complete, call read_transcript and get_contact_sheet.",
        }

    @mcp.tool()
    def get_job_status(job_id: str) -> dict[str, Any]:
        """Read durable status for an import, render or edit job."""
        return store.public_job(store.get_job(job_id))

    @mcp.tool()
    def list_projects() -> dict[str, Any]:
        """List all persistent local OpenShorts MCP projects."""
        return {"output_dir": str(store.root), "projects": store.iter_projects()}

    @mcp.tool()
    def get_project(project_id: str) -> dict[str, Any]:
        """Get source metadata, transcript/contact-sheet paths and artifacts."""
        return store.public_project(store.get_project(project_id))

    @mcp.tool()
    def read_transcript(
        project_id: str,
        start_seconds: float | None = None,
        end_seconds: float | None = None,
        include_words: bool = True,
        offset: int = 0,
        limit: int = 100,
    ) -> dict[str, Any]:
        """Read a paginated transcript slice, optionally with word timestamps."""
        project = _require_project_ready(store, project_id)
        transcript = _read_transcript(store, project)
        start = max(0.0, float(start_seconds or 0.0))
        duration = float((project.get("media") or {}).get("duration_seconds") or 0)
        end = min(duration, float(end_seconds if end_seconds is not None else duration))
        if end <= start:
            raise ValueError("end_seconds must be greater than start_seconds.")
        offset = max(0, int(offset))
        limit = max(1, min(250, int(limit)))
        all_segments = [
            segment
            for segment in transcript.get("segments") or []
            if isinstance(segment, dict)
            and float(segment.get("end") or 0) > start
            and float(segment.get("start") or 0) < end
        ]
        selected = []
        for segment in all_segments[offset : offset + limit]:
            value = dict(segment)
            if not include_words:
                value.pop("words", None)
            selected.append(value)
        return {
            "project_id": project_id,
            "language": transcript.get("language"),
            "range": {"start_seconds": start, "end_seconds": end},
            "offset": offset,
            "limit": limit,
            "total_segments": len(all_segments),
            "has_more": offset + len(selected) < len(all_segments),
            "segments": selected,
        }

    @mcp.tool()
    async def get_contact_sheet(
        project_id: str,
        timestamps: list[float] | None = None,
        start_seconds: float | None = None,
        end_seconds: float | None = None,
        count: int = 12,
    ) -> Any:
        """Return a local contact sheet path for visual editorial decisions.

        Calling without range/timestamps returns the import-time default. A
        custom request creates a new local JPEG immediately; it never uses AI.
        """
        project = _require_project_ready(store, project_id)
        analysis = project.get("analysis") or {}
        has_custom_request = timestamps is not None or start_seconds is not None or end_seconds is not None or count != 12
        if not has_custom_request and analysis.get("contact_sheet_path"):
            selected = analysis.get("contact_sheet_timestamps") or []
            path = store.abs_path(project_id, str(analysis["contact_sheet_path"]))
            response = {
                "project_id": project_id,
                "path": path,
                "mime_type": "image/jpeg",
                "timestamps": selected,
                "note": "The contact sheet image is attached below for visual editorial decisions.",
            }
            return [response, Image(path=path)]
        duration = float((project.get("media") or {}).get("duration_seconds") or 0)
        selected = contact_sheet_timestamps(
            duration,
            timestamps=timestamps,
            start_seconds=start_seconds,
            end_seconds=end_seconds,
            count=count,
        )
        digest = hashlib.sha256(",".join(f"{value:.3f}" for value in selected).encode()).hexdigest()[:16]
        relative = f"analysis/contact-sheet-{digest}.jpg"
        output = store.project_path(project_id, relative)
        if not output.exists():
            source = store.project_path(project_id, str(project["source"]["relative_path"]))
            await asyncio.to_thread(
                create_contact_sheet,
                source,
                output,
                duration,
                timestamps=selected,
            )
        response = {
            "project_id": project_id,
            "path": str(output),
            "mime_type": "image/jpeg",
            "timestamps": selected,
            "note": "The contact sheet image is attached below for visual editorial decisions.",
        }
        return [response, Image(path=output)]

    @mcp.tool()
    async def render_clips(project_id: str, clips: list[dict[str, Any]]) -> dict[str, Any]:
        """Render LLM-selected 15-60 second clips with explicit format and layout.

        Each item must provide start_seconds, end_seconds, output_format
        (vertical, horizontal or square) and layout (center_crop, blur_fill or
        fit). Captions remain disabled unless add_subtitles is called later.
        """
        project = _require_project_ready(store, project_id)
        payload = {"project_id": project_id, "clips": _validate_clip_requests(project, clips)}
        job = await jobs.submit("render_clips", payload)
        return {
            "project_id": project_id,
            "job": job,
            "next_step": "Poll get_job_status. Its completed result lists absolute artifact paths.",
        }

    @mcp.tool()
    async def apply_effects(artifact_id: str, edits: list[dict[str, Any]]) -> dict[str, Any]:
        """Apply validated structured effects to an artifact in a new revision.

        Supported types: zoom_in, punch_in, zoom_pulse, color_pop, bw_moment,
        flash and vignette. Every edit needs type, start_seconds, end_seconds
        and optional strength. Raw FFmpeg filters are deliberately unsupported.
        """
        if not isinstance(edits, list) or not edits:
            raise ValueError("edits must be a non-empty list of structured edit operations.")
        project, _artifact = store.find_artifact(artifact_id)
        job = await jobs.submit(
            "apply_effects",
            {"project_id": project["project_id"], "artifact_id": artifact_id, "edits": edits},
        )
        return {
            "project_id": project["project_id"],
            "job": job,
            "supported_effect_types": list(EFFECT_TYPES),
        }

    @mcp.tool()
    async def add_subtitles(
        artifact_id: str,
        style_options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Burn explicitly requested subtitles into a new immutable artifact.

        Subtitles are off by default. style_options can set style (classic or
        karaoke), position, font_size, colors, border_width and uppercase.
        """
        project, _artifact = store.find_artifact(artifact_id)
        job = await jobs.submit(
            "add_subtitles",
            {
                "project_id": project["project_id"],
                "artifact_id": artifact_id,
                "style_options": dict(style_options or {}),
            },
        )
        return {"project_id": project["project_id"], "job": job}

    @mcp.tool()
    async def add_hook_overlay(
        artifact_id: str,
        text: str,
        position: str = "top",
        style_options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Burn a text hook overlay into a new immutable artifact."""
        project, _artifact = store.find_artifact(artifact_id)
        if position not in ("top", "center", "bottom"):
            raise ValueError("position must be top, center or bottom.")
        job = await jobs.submit(
            "add_hook_overlay",
            {
                "project_id": project["project_id"],
                "artifact_id": artifact_id,
                "text": text,
                "position": position,
                "style_options": dict(style_options or {}),
            },
        )
        return {"project_id": project["project_id"], "job": job}

    @mcp.tool()
    def delete_project(project_id: str, confirm: bool = False) -> dict[str, Any]:
        """Permanently delete one project's copied source, analysis and artifacts."""
        if not confirm:
            raise ValueError("Set confirm=true to permanently delete this local project.")
        store.delete_project(project_id)
        return {"deleted": True, "project_id": project_id}

    return mcp


async def _run_stdio_server(mcp: FastMCP) -> None:
    """Run FastMCP over native asyncio stdin/stdout streams.

    The upstream helper currently wraps file descriptors through AnyIO worker
    threads. Keeping the protocol boundary on asyncio's own executor avoids a
    worker-thread deadlock observed on some Python 3.12 runtimes, while the MCP
    server, schema validation and tool implementation remain the official SDK.
    """
    inbound_send, inbound_receive = anyio.create_memory_object_stream[SessionMessage | Exception](0)
    outbound_send, outbound_receive = anyio.create_memory_object_stream[SessionMessage](0)
    loop = asyncio.get_running_loop()
    stdin_reader = asyncio.StreamReader()
    stdin_protocol = asyncio.StreamReaderProtocol(stdin_reader)
    stdin_transport = None
    try:
        stdin_transport, _ = await loop.connect_read_pipe(
            lambda: stdin_protocol,
            sys.stdin.buffer,
        )
    except (AttributeError, NotImplementedError):
        # Windows' proactor loop has no read-pipe support. The fallback still
        # keeps protocol I/O off FastMCP's AnyIO worker-thread helper.
        stdin_reader = None

    async def read_stdin() -> None:
        try:
            while True:
                raw = (
                    await stdin_reader.readline()
                    if stdin_reader is not None
                    else await asyncio.to_thread(sys.stdin.buffer.readline)
                )
                if not raw:
                    break
                try:
                    message = JSONRPCMessage.model_validate_json(raw)
                except Exception as exc:
                    await inbound_send.send(exc)
                else:
                    await inbound_send.send(SessionMessage(message))
        except (anyio.BrokenResourceError, anyio.ClosedResourceError):
            pass
        finally:
            await inbound_send.aclose()

    async def write_stdout() -> None:
        try:
            async with outbound_receive:
                async for session_message in outbound_receive:
                    raw = session_message.message.model_dump_json(
                        by_alias=True,
                        exclude_none=True,
                    ).encode("utf-8") + b"\n"
                    sys.stdout.buffer.write(raw)
                    sys.stdout.buffer.flush()
        except (BrokenPipeError, anyio.BrokenResourceError, anyio.ClosedResourceError):
            pass

    async with anyio.create_task_group() as group:
        group.start_soon(read_stdin)
        group.start_soon(write_stdout)
        try:
            await mcp._mcp_server.run(
                inbound_receive,
                outbound_send,
                mcp._mcp_server.create_initialization_options(),
            )
        finally:
            group.cancel_scope.cancel()
            if stdin_transport is not None:
                stdin_transport.close()


def run_stdio(output_dir: str | None = None) -> None:
    """Start only the MCP stdio transport; no HTTP listener is created."""
    anyio.run(_run_stdio_server, create_server(output_dir))
