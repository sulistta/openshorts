"""Local media import, analysis and deterministic FFmpeg rendering helpers."""

from __future__ import annotations

import ipaddress
import json
import math
import os
import shutil
import socket
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

from PIL import Image, ImageDraw, ImageFont

from .effects import build_filter
from .transcription import NoAudioError, transcribe


OUTPUT_FORMATS = ("vertical", "horizontal", "square")
LAYOUTS = ("center_crop", "blur_fill", "fit")
FORMAT_SIZES = {
    "vertical": (1080, 1920),
    "horizontal": (1920, 1080),
    "square": (1080, 1080),
}


class MediaError(RuntimeError):
    """FFmpeg, local media or remote-source work failed."""


def _run(command: list[str], *, timeout: int = 3600) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise MediaError(
            f"Required executable was not found: {command[0]}. Install FFmpeg and ensure it is on PATH."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise MediaError(f"Media operation timed out after {timeout} seconds.") from exc
    if result.returncode != 0:
        message = (result.stderr or result.stdout or "unknown FFmpeg error").strip()
        raise MediaError(message[-1600:])
    return result


def _as_number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _fps(value: Any) -> float:
    text = str(value or "")
    if "/" in text:
        numerator, denominator = text.split("/", 1)
        denominator_value = _as_number(denominator)
        return _as_number(numerator) / denominator_value if denominator_value else 0.0
    return _as_number(text)


def probe_media(path: str | Path) -> dict[str, Any]:
    """Return stable local metadata for a video file."""
    media_path = Path(path)
    if not media_path.is_file():
        raise MediaError(f"Media file does not exist: {media_path}")
    result = _run(
        [
            "ffprobe",
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(media_path),
        ],
        timeout=90,
    )
    try:
        raw = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise MediaError("ffprobe did not return valid media metadata.") from exc
    streams = list(raw.get("streams") or [])
    video = next((stream for stream in streams if stream.get("codec_type") == "video"), None)
    if not video:
        raise MediaError("The source has no video stream.")
    duration = _as_number((raw.get("format") or {}).get("duration"))
    if duration <= 0:
        duration = _as_number(video.get("duration"))
    if duration <= 0:
        raise MediaError("Could not determine the source duration.")
    width = int(_as_number(video.get("width")))
    height = int(_as_number(video.get("height")))
    if width <= 0 or height <= 0:
        raise MediaError("Could not determine the source dimensions.")
    return {
        "duration_seconds": round(duration, 3),
        "width": width,
        "height": height,
        "fps": round(_fps(video.get("avg_frame_rate") or video.get("r_frame_rate")) or 30.0, 4),
        "has_audio": any(stream.get("codec_type") == "audio" for stream in streams),
        "video_codec": video.get("codec_name"),
        "container": (raw.get("format") or {}).get("format_name"),
        "size_bytes": media_path.stat().st_size,
    }


def _is_public_ip(value: str) -> bool:
    address = ipaddress.ip_address(value)
    return not (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_multicast
        or address.is_unspecified
    )


def assert_public_url(value: str) -> str:
    """Reject non-public URLs before giving them to the local downloader."""
    if not isinstance(value, str) or not value.strip():
        raise MediaError("source_url must be a non-empty public HTTP(S) URL.")
    parsed = urlparse(value.strip())
    if parsed.scheme.lower() not in ("http", "https") or not parsed.hostname:
        raise MediaError("source_url must be a public HTTP(S) URL.")
    host = parsed.hostname
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        if not _is_public_ip(str(literal)):
            raise MediaError("source_url must not point to a private or loopback address.")
        return value.strip()
    try:
        records = socket.getaddrinfo(host, parsed.port or None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise MediaError(f"Could not resolve source_url host {host!r}: {exc}") from exc
    resolved = {record[4][0] for record in records}
    if not resolved or any(not _is_public_ip(address) for address in resolved):
        raise MediaError("source_url must resolve only to public addresses.")
    return value.strip()


def copy_local_source(source_path: str, destination_dir: str | Path) -> tuple[Path, str]:
    """Copy a local source into the project for reproducible artifacts."""
    original = Path(source_path).expanduser().resolve()
    if not original.is_file():
        raise MediaError(f"source_path does not exist or is not a file: {original}")
    suffix = original.suffix.lower() or ".media"
    destination = Path(destination_dir) / f"source{suffix}"
    shutil.copy2(original, destination)
    return destination, original.name


def download_public_source(source_url: str, destination_dir: str | Path) -> tuple[Path, str]:
    """Download a public URL locally through yt-dlp."""
    url = assert_public_url(source_url)
    try:
        import yt_dlp
    except ImportError as exc:  # pragma: no cover - installation-specific
        raise MediaError("yt-dlp is not installed.") from exc

    destination_dir = Path(destination_dir)
    destination_dir.mkdir(parents=True, exist_ok=True)
    options = {
        "outtmpl": str(destination_dir / "download.%(ext)s"),
        "format": "bv*+ba/b",
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "overwrites": True,
        "restrictfilenames": True,
        "socket_timeout": 60,
        "retries": 3,
    }
    try:
        with yt_dlp.YoutubeDL(options) as downloader:
            info = downloader.extract_info(url, download=True)
    except Exception as exc:
        raise MediaError(f"Could not download source_url: {exc}") from exc

    candidates = sorted(
        (
            path
            for path in destination_dir.glob("download.*")
            if path.is_file() and path.suffix.lower() not in {".part", ".ytdl"}
        ),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        raise MediaError("yt-dlp finished without producing a local media file.")
    downloaded = candidates[0]
    suffix = downloaded.suffix.lower() or ".mp4"
    source = destination_dir / f"source{suffix}"
    if source.exists():
        source.unlink()
    downloaded.rename(source)
    title = str((info or {}).get("title") or source.name)
    return source, title


def contact_sheet_timestamps(
    duration_seconds: float,
    *,
    timestamps: Iterable[float] | None = None,
    start_seconds: float | None = None,
    end_seconds: float | None = None,
    count: int = 12,
) -> list[float]:
    duration = float(duration_seconds)
    if duration <= 0:
        raise MediaError("Cannot create a contact sheet without a positive duration.")
    if timestamps is not None:
        values = []
        for value in timestamps:
            try:
                values.append(max(0.0, min(float(value), max(0.0, duration - 0.001))))
            except (TypeError, ValueError):
                raise MediaError("timestamps must contain only numbers.")
        if not values:
            raise MediaError("timestamps must not be empty.")
        if len(values) > 24:
            raise MediaError("A contact sheet supports at most 24 timestamps.")
        return values

    start = max(0.0, float(start_seconds or 0.0))
    end = min(duration, float(end_seconds if end_seconds is not None else duration))
    if end <= start:
        raise MediaError("end_seconds must be greater than start_seconds.")
    count = int(count)
    if not 1 <= count <= 24:
        raise MediaError("count must be between 1 and 24.")
    span = end - start
    return [start + span * ((index + 0.5) / count) for index in range(count)]


def create_contact_sheet(
    source_path: str | Path,
    output_path: str | Path,
    duration_seconds: float,
    *,
    timestamps: Iterable[float] | None = None,
    start_seconds: float | None = None,
    end_seconds: float | None = None,
    count: int = 12,
) -> list[float]:
    """Extract labelled stills locally and save a JPEG contact sheet."""
    source = Path(source_path)
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    selected = contact_sheet_timestamps(
        duration_seconds,
        timestamps=timestamps,
        start_seconds=start_seconds,
        end_seconds=end_seconds,
        count=count,
    )
    with tempfile.TemporaryDirectory(prefix="openshorts-frames-", dir=output.parent) as temporary:
        frames: list[Image.Image] = []
        for index, timestamp in enumerate(selected):
            frame_path = Path(temporary) / f"frame-{index:02d}.jpg"
            _run(
                [
                    "ffmpeg",
                    "-y",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-ss",
                    f"{timestamp:.3f}",
                    "-i",
                    str(source),
                    "-frames:v",
                    "1",
                    "-vf",
                    "scale=320:-2",
                    str(frame_path),
                ],
                timeout=180,
            )
            with Image.open(frame_path) as image:
                frames.append(image.convert("RGB"))

        tile_width = 320
        label_height = 30
        tile_height = max(frame.height for frame in frames) + label_height
        columns = min(4, len(frames))
        rows = math.ceil(len(frames) / columns)
        sheet = Image.new("RGB", (tile_width * columns, tile_height * rows), "#111111")
        draw = ImageDraw.Draw(sheet)
        font = ImageFont.load_default()
        for index, (frame, timestamp) in enumerate(zip(frames, selected)):
            x = (index % columns) * tile_width
            y = (index // columns) * tile_height
            if frame.width != tile_width:
                ratio = tile_width / frame.width
                frame = frame.resize((tile_width, int(frame.height * ratio)))
            sheet.paste(frame, (x, y))
            draw.rectangle((x, y + tile_height - label_height, x + tile_width, y + tile_height), fill="#111111")
            draw.text((x + 8, y + tile_height - 22), f"{timestamp:.1f}s", fill="white", font=font)
        sheet.save(output, quality=88)
    return [round(value, 3) for value in selected]


def _encoder_args() -> list[str]:
    return [
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "18",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-map_metadata",
        "-1",
        "-map_chapters",
        "-1",
        "-movflags",
        "+faststart",
    ]


def _target_size(output_format: str) -> tuple[int, int]:
    if output_format not in FORMAT_SIZES:
        raise MediaError(f"output_format must be one of: {', '.join(OUTPUT_FORMATS)}.")
    return FORMAT_SIZES[output_format]


def _reframe_graph(layout: str, width: int, height: int) -> tuple[str, bool]:
    if layout not in LAYOUTS:
        raise MediaError(f"layout must be one of: {', '.join(LAYOUTS)}.")
    if layout == "center_crop":
        return (
            f"scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height}",
            False,
        )
    if layout == "fit":
        return (
            f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black",
            False,
        )
    return (
        f"[0:v]split=2[background][foreground];"
        f"[background]scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},boxblur=20:1[background_blurred];"
        f"[foreground]scale={width}:{height}:force_original_aspect_ratio=decrease[foreground_scaled];"
        f"[background_blurred][foreground_scaled]overlay=(W-w)/2:(H-h)/2,format=yuv420p[video]",
        True,
    )


def render_clip(
    source_path: str | Path,
    output_path: str | Path,
    *,
    start_seconds: float,
    end_seconds: float,
    output_format: str,
    layout: str,
) -> dict[str, Any]:
    """Cut an exact range and deterministically reframe it."""
    start = float(start_seconds)
    end = float(end_seconds)
    if start < 0 or end <= start:
        raise MediaError("Clip timestamps must satisfy 0 <= start_seconds < end_seconds.")
    width, height = _target_size(output_format)
    graph, complex_graph = _reframe_graph(layout, width, height)
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    command = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(source_path),
        "-ss",
        f"{start:.3f}",
        "-t",
        f"{end - start:.3f}",
    ]
    if complex_graph:
        command.extend(["-filter_complex", graph, "-map", "[video]"])
    else:
        command.extend(["-vf", graph, "-map", "0:v:0"])
    command.extend(["-map", "0:a?", *_encoder_args(), str(output)])
    _run(command)
    metadata = probe_media(output)
    return {
        "duration_seconds": metadata["duration_seconds"],
        "width": metadata["width"],
        "height": metadata["height"],
    }


def apply_effects(
    input_path: str | Path,
    output_path: str | Path,
    edits: list[dict[str, Any]],
    *,
    has_text_layers: bool,
) -> list[dict[str, float | str]]:
    """Render a new artifact with a validated effect decision list."""
    metadata = probe_media(input_path)
    filter_graph, applied = build_filter(
        edits,
        float(metadata["duration_seconds"]),
        float(metadata["fps"]),
        int(metadata["width"]),
        int(metadata["height"]),
        has_text_layers=has_text_layers,
    )
    if not filter_graph or not applied:
        reason = "Zoom effects are unavailable after captions or hook overlays."
        raise MediaError(f"No valid effects to apply. {reason}")
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    _run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(input_path),
            "-vf",
            filter_graph,
            "-map",
            "0:v:0",
            "-map",
            "0:a?",
            *_encoder_args(),
            str(output),
        ]
    )
    return applied


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split())


def _collect_caption_blocks(
    transcript: dict[str, Any],
    source_start: float,
    source_end: float,
    *,
    max_chars: int,
    max_duration: float,
) -> list[list[dict[str, float | str]]]:
    words: list[dict[str, float | str]] = []
    for segment in transcript.get("segments") or []:
        for word in segment.get("words") or []:
            start = _as_number(word.get("start"), -1)
            end = _as_number(word.get("end"), -1)
            if end <= source_start or start >= source_end:
                continue
            text = _clean_text(word.get("word"))
            if not text:
                continue
            words.append(
                {
                    "word": text,
                    "start": max(0.0, start - source_start),
                    "end": max(0.0, min(end, source_end) - source_start),
                }
            )
    words.sort(key=lambda item: (float(item["start"]), float(item["end"])))
    blocks: list[list[dict[str, float | str]]] = []
    current: list[dict[str, float | str]] = []
    for word in words:
        if not current:
            current = [word]
            continue
        current_text = " ".join(str(item["word"]) for item in current)
        duration = float(word["end"]) - float(current[0]["start"])
        if len(current_text) + 1 + len(str(word["word"])) > max_chars or duration > max_duration:
            blocks.append(current)
            current = [word]
        else:
            current.append(word)
    if current:
        blocks.append(current)
    return blocks


def _ass_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    value = seconds % 60
    return f"{hours}:{minutes:02d}:{value:05.2f}"


def _ass_color(value: Any, fallback: str) -> str:
    text = str(value or fallback).strip().lstrip("#")
    if len(text) != 6 or any(character not in "0123456789abcdefABCDEF" for character in text):
        text = fallback
    return f"&H00{text[4:6]}{text[2:4]}{text[:2]}&"


def _ass_text(value: str) -> str:
    return value.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}").replace("\n", "\\N")


def _safe_font_name(value: Any) -> str:
    clean = "".join(character for character in str(value or "") if character.isalnum() or character in " _-").strip()
    return clean or "Arial"


def write_ass(
    transcript: dict[str, Any],
    output_path: str | Path,
    *,
    source_start: float,
    source_end: float,
    style_options: dict[str, Any],
) -> None:
    """Create an ASS subtitle file whose timing is relative to the artifact."""
    max_chars = max(8, min(48, int(_as_number(style_options.get("max_chars"), 18))))
    max_duration = max(0.5, min(4.0, _as_number(style_options.get("max_duration"), 1.6)))
    blocks = _collect_caption_blocks(
        transcript,
        source_start,
        source_end,
        max_chars=max_chars,
        max_duration=max_duration,
    )
    if not blocks:
        raise MediaError("The selected clip range has no word-level transcript for subtitles.")

    style = str(style_options.get("style") or "karaoke").lower()
    if style not in ("classic", "karaoke"):
        raise MediaError("Subtitle style must be 'classic' or 'karaoke'.")
    position = str(style_options.get("position") or "bottom").lower()
    alignment = {"top": 8, "middle": 5, "bottom": 2}.get(position)
    if alignment is None:
        raise MediaError("Subtitle position must be top, middle or bottom.")
    font_size = max(20, min(120, int(_as_number(style_options.get("font_size"), 54))))
    primary = _ass_color(style_options.get("font_color"), "FFFFFF")
    highlight = _ass_color(style_options.get("highlight_color"), "FFE500")
    outline = _ass_color(style_options.get("border_color"), "000000")
    border_width = max(1, min(10, int(_as_number(style_options.get("border_width"), 4))))
    font_name = _safe_font_name(style_options.get("font_name") or "Arial")
    uppercase = bool(style_options.get("uppercase", True))
    margin = {"top": 100, "middle": 0, "bottom": 250}[position]

    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        "PlayResX: 1080\n"
        "PlayResY: 1920\n"
        "ScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,"
        "Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,"
        "Alignment,MarginL,MarginR,MarginV,Encoding\n"
        f"Style: Default,{font_name},{font_size},{primary},{highlight},{outline},&H00000000&,"
        f"1,0,0,0,100,100,0,0,1,{border_width},0,{alignment},60,60,{margin},1\n\n"
        "[Events]\n"
        "Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text\n"
    )
    events: list[str] = []
    for block in blocks:
        block_start = float(block[0]["start"])
        block_end = float(block[-1]["end"])
        if block_end <= block_start:
            continue
        if style == "classic":
            text = " ".join(_ass_text(str(word["word"]).upper() if uppercase else str(word["word"])) for word in block)
            events.append(
                f"Dialogue: 0,{_ass_time(block_start)},{_ass_time(block_end)},Default,,0,0,0,,{text}"
            )
            continue
        for index, active in enumerate(block):
            event_start = float(active["start"])
            event_end = float(block[index + 1]["start"]) if index + 1 < len(block) else block_end
            if event_end <= event_start:
                continue
            pieces = []
            for word in block:
                text = str(word["word"])
                if uppercase:
                    text = text.upper()
                if word is active:
                    pieces.append(f"{{\\c{highlight}}}{_ass_text(text)}{{\\r}}")
                else:
                    pieces.append(_ass_text(text))
            events.append(
                f"Dialogue: 0,{_ass_time(event_start)},{_ass_time(event_end)},Default,,0,0,0,,{' '.join(pieces)}"
            )
    if not events:
        raise MediaError("No usable subtitle timings were produced.")
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(header + "\n".join(events) + "\n", encoding="utf-8")


def _escape_filter_path(path: str | Path) -> str:
    return str(path).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")


def burn_subtitles(
    input_path: str | Path,
    output_path: str | Path,
    transcript: dict[str, Any],
    *,
    source_start: float,
    source_end: float,
    style_options: dict[str, Any],
) -> str:
    """Burn explicit subtitles into a new artifact. Never called automatically."""
    output = Path(output_path)
    ass_path = output.with_suffix(".ass")
    write_ass(
        transcript,
        ass_path,
        source_start=source_start,
        source_end=source_end,
        style_options=style_options,
    )
    _run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(input_path),
            "-vf",
            f"ass=filename='{_escape_filter_path(ass_path)}'",
            "-map",
            "0:v:0",
            "-map",
            "0:a?",
            *_encoder_args(),
            str(output),
        ]
    )
    return str(ass_path)


HOOK_STYLES = {
    "classic": {"background": (255, 255, 255, 242), "foreground": (0, 0, 0, 255), "outline": 0},
    "dark": {"background": (24, 24, 27, 242), "foreground": (255, 255, 255, 255), "outline": 0},
    "yellow": {"background": (250, 204, 21, 245), "foreground": (0, 0, 0, 255), "outline": 0},
    "red": {"background": (220, 38, 38, 245), "foreground": (255, 255, 255, 255), "outline": 0},
    "outline": {"background": (0, 0, 0, 0), "foreground": (255, 255, 255, 255), "outline": 5},
}


def _load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        Path(__file__).resolve().parents[1] / "fonts" / "NotoSerif-Bold.ttf",
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    ]
    for candidate in candidates:
        try:
            if candidate.is_file():
                return ImageFont.truetype(str(candidate), size)
        except OSError:
            continue
    return ImageFont.load_default()


def _wrap_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_width: int) -> list[str]:
    lines: list[str] = []
    for paragraph in text.splitlines() or [text]:
        words = paragraph.split()
        if not words:
            lines.append("")
            continue
        current = ""
        for word in words:
            candidate = f"{current} {word}".strip()
            if current and draw.textlength(candidate, font=font) > max_width:
                lines.append(current)
                current = word
            else:
                current = candidate
        if current:
            lines.append(current)
    return lines or [text]


def add_hook_overlay(
    input_path: str | Path,
    output_path: str | Path,
    text: str,
    *,
    position: str,
    style_options: dict[str, Any],
) -> None:
    """Create a local text-card image and burn it as an overlay."""
    if not isinstance(text, str) or not text.strip():
        raise MediaError("Hook text must be non-empty.")
    if len(text) > 280:
        raise MediaError("Hook text must be at most 280 characters.")
    if position not in ("top", "center", "bottom"):
        raise MediaError("Hook position must be top, center or bottom.")
    style_name = str(style_options.get("style") or "classic").lower()
    if style_name not in HOOK_STYLES:
        raise MediaError(f"Hook style must be one of: {', '.join(HOOK_STYLES)}.")
    scale = max(0.6, min(1.8, _as_number(style_options.get("font_scale"), 1.0)))
    metadata = probe_media(input_path)
    width = int(metadata["width"])
    height = int(metadata["height"])
    max_width = int(width * 0.86)
    font = _load_font(max(22, int(width * 0.045 * scale)))
    scratch = Image.new("RGBA", (1, 1))
    draw = ImageDraw.Draw(scratch)
    lines = _wrap_text(draw, text.strip(), font, max_width - 80)
    line_boxes = [draw.textbbox((0, 0), line, font=font, stroke_width=0) for line in lines]
    line_height = max((box[3] - box[1] for box in line_boxes), default=40)
    content_width = min(max_width - 80, max((int(draw.textlength(line, font=font)) for line in lines), default=1))
    box_width = content_width + 80
    box_height = line_height * len(lines) + 60 + max(0, len(lines) - 1) * 10
    look = HOOK_STYLES[style_name]
    image = Image.new("RGBA", (box_width + 20, box_height + 20), (0, 0, 0, 0))
    image_draw = ImageDraw.Draw(image)
    if look["background"][3]:
        image_draw.rounded_rectangle((10, 10, box_width + 10, box_height + 10), radius=24, fill=look["background"])
    cursor_y = 40
    for line in lines:
        line_width = int(image_draw.textlength(line, font=font))
        cursor_x = 10 + (box_width - line_width) // 2
        image_draw.text(
            (cursor_x, cursor_y),
            line,
            font=font,
            fill=look["foreground"],
            stroke_width=int(look["outline"]),
            stroke_fill=(0, 0, 0, 255),
        )
        cursor_y += line_height + 10

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    overlay = output.with_suffix(".hook.png")
    image.save(overlay)
    x = "(W-w)/2"
    if position == "top":
        y = str(int(height * 0.10))
    elif position == "center":
        y = "(H-h)/2"
    else:
        y = str(int(height * 0.70))
    try:
        _run(
            [
                "ffmpeg",
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                str(input_path),
                "-loop",
                "1",
                "-i",
                str(overlay),
                "-filter_complex",
                f"[0:v][1:v]overlay={x}:{y}:shortest=1[video]",
                "-map",
                "[video]",
                "-map",
                "0:a?",
                "-shortest",
                *_encoder_args(),
                str(output),
            ]
        )
    finally:
        try:
            overlay.unlink()
        except FileNotFoundError:
            pass


def transcribe_source(source_path: str | Path) -> dict[str, Any]:
    """Transcribe speech locally; silent media gets an explicit empty transcript."""
    try:
        return transcribe(source_path)
    except NoAudioError:
        return {
            "text": "",
            "language": "none",
            "segments": [],
            "note": "Source has no audio stream; no transcript was generated.",
        }
