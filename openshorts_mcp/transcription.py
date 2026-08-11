"""Local faster-whisper transcription with word timestamps."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any


class NoAudioError(RuntimeError):
    """The imported media has no usable audio stream."""


def _has_audio(path: Path) -> bool:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "a",
            "-show_entries",
            "stream=index",
            "-of",
            "csv=p=0",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    return bool(result.stdout.strip())


def _merge_continuations(words: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    for word in words:
        value = str(word.get("word") or "")
        if merged and value and not value.startswith(" "):
            merged[-1]["word"] = str(merged[-1]["word"]) + value
            merged[-1]["end"] = word["end"]
        else:
            merged.append(dict(word))
    return merged


def transcribe(path: str | Path) -> dict[str, Any]:
    """Return a normalized, local transcript with word timestamps."""
    media_path = Path(path)
    if not _has_audio(media_path):
        raise NoAudioError("The imported media has no audio stream.")

    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:  # pragma: no cover - depends on optional runtime
        raise RuntimeError(
            "faster-whisper is not installed. Reinstall OpenShorts MCP with its video dependencies."
        ) from exc

    model_name = os.environ.get("WHISPER_MODEL", "small")
    device = os.environ.get("WHISPER_DEVICE", "cpu")
    compute_type = os.environ.get("WHISPER_COMPUTE", "int8")
    try:
        model = WhisperModel(model_name, device=device, compute_type=compute_type)
        segments, info = model.transcribe(
            str(media_path),
            beam_size=5,
            vad_filter=True,
            condition_on_previous_text=False,
            word_timestamps=True,
        )
        materialized = list(segments)
    except Exception as exc:
        if device.lower() == "cpu":
            raise RuntimeError(f"Local transcription failed: {exc}") from exc
        try:
            model = WhisperModel(model_name, device="cpu", compute_type="int8")
            segments, info = model.transcribe(
                str(media_path),
                beam_size=5,
                vad_filter=True,
                condition_on_previous_text=False,
                word_timestamps=True,
            )
            materialized = list(segments)
        except Exception as fallback_exc:
            raise RuntimeError(f"Local transcription failed: {fallback_exc}") from fallback_exc

    normalized: list[dict[str, Any]] = []
    text_parts: list[str] = []
    for segment in materialized:
        words = _merge_continuations(
            [
                {
                    "word": str(word.word or ""),
                    "start": float(word.start),
                    "end": float(word.end),
                }
                for word in (segment.words or [])
            ]
        )
        text = str(segment.text or "").strip()
        normalized.append(
            {
                "start": float(segment.start),
                "end": float(segment.end),
                "text": text,
                "words": words,
            }
        )
        if text:
            text_parts.append(text)
    return {
        "text": " ".join(text_parts),
        "language": str(getattr(info, "language", "unknown") or "unknown"),
        "segments": normalized,
    }
