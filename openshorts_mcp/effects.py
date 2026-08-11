"""Validated, deterministic video-effect decision lists.

The MCP accepts an LLM's *intent* (effect type, time and strength), never a
raw FFmpeg filter expression. This keeps execution local and bounded while
letting the model make editorial choices.
"""

from __future__ import annotations

from typing import Any

EFFECT_LIMITS: dict[str, dict[str, float | bool]] = {
    "zoom_in": {"zoom": True, "max_strength": 0.15, "default_strength": 0.10},
    "punch_in": {"zoom": True, "max_strength": 0.15, "default_strength": 0.09},
    "zoom_pulse": {"zoom": True, "max_strength": 0.10, "default_strength": 0.07},
    "color_pop": {"zoom": False, "max_strength": 1.0, "default_strength": 0.50},
    "bw_moment": {"zoom": False, "max_strength": 1.0, "default_strength": 1.0},
    "flash": {"zoom": False, "max_strength": 1.0, "default_strength": 1.0},
    "vignette": {"zoom": False, "max_strength": 1.0, "default_strength": 1.0},
}

EFFECT_TYPES = tuple(EFFECT_LIMITS)
MAX_EDITS = 12
MIN_EFFECT_SECONDS = 0.15
MAX_ZOOM_SECONDS = 8.0
FLASH_SECONDS = 0.15
ZOOM_CENTER_Y = 0.45


def normalize_edits(
    edits: list[dict[str, Any]] | Any,
    duration: float,
    *,
    has_text_layers: bool = False,
) -> list[dict[str, float | str]]:
    """Validate an edit decision list and return bounded, sorted operations."""
    if not isinstance(edits, list) or duration <= 0:
        return []

    clean: list[dict[str, float | str]] = []
    for item in edits:
        if not isinstance(item, dict):
            continue
        effect_type = str(item.get("type", "")).strip().lower()
        limits = EFFECT_LIMITS.get(effect_type)
        if not limits or (has_text_layers and bool(limits["zoom"])):
            continue
        try:
            start = float(item["start_seconds"])
            end = float(item["end_seconds"])
        except (KeyError, TypeError, ValueError):
            continue
        start = max(0.0, min(start, duration))
        end = max(0.0, min(end, duration))
        if end - start < MIN_EFFECT_SECONDS:
            continue
        if bool(limits["zoom"]) and end - start > MAX_ZOOM_SECONDS:
            end = start + MAX_ZOOM_SECONDS
        try:
            strength = float(item.get("strength", 0.0))
        except (TypeError, ValueError):
            strength = 0.0
        if strength <= 0:
            strength = float(limits["default_strength"])
        strength = min(strength, float(limits["max_strength"]))
        clean.append(
            {
                "type": effect_type,
                "start_seconds": start,
                "end_seconds": end,
                "strength": strength,
            }
        )

    clean.sort(key=lambda value: (float(value["start_seconds"]), float(value["end_seconds"])))
    result: list[dict[str, float | str]] = []
    last_zoom_end = -1.0
    flashes = 0
    for item in clean:
        effect_type = str(item["type"])
        if bool(EFFECT_LIMITS[effect_type]["zoom"]):
            if float(item["start_seconds"]) < last_zoom_end:
                continue
            last_zoom_end = float(item["end_seconds"])
        if effect_type == "flash":
            if flashes >= 2:
                continue
            flashes += 1
        result.append(item)
        if len(result) >= MAX_EDITS:
            break
    return result


def _zoom_term(edit: dict[str, float | str], fps: float) -> str:
    start_frame = int(round(float(edit["start_seconds"]) * fps))
    end_frame = max(start_frame + 2, int(round(float(edit["end_seconds"]) * fps)))
    span = end_frame - start_frame
    strength = float(edit["strength"])
    gate = f"between(on,{start_frame},{end_frame})"
    if edit["type"] == "zoom_in":
        return f"{strength:.4f}*clip((on-{start_frame})/{span},0,1)*{gate}"
    if edit["type"] == "zoom_pulse":
        midpoint = (start_frame + end_frame) / 2.0
        half = max(span / 2.0, 1.0)
        return f"{strength:.4f}*(1-abs((on-{midpoint:.1f})/{half:.1f}))*{gate}"
    return f"{strength:.4f}*{gate}"


def build_filter(
    edits: list[dict[str, Any]],
    duration: float,
    fps: float,
    width: int,
    height: int,
    *,
    has_text_layers: bool,
) -> tuple[str | None, list[dict[str, float | str]]]:
    """Build a safe FFmpeg video filter from valid structured operations."""
    applied = normalize_edits(edits, duration, has_text_layers=has_text_layers)
    if not applied:
        return None, []

    fps = float(fps) if fps and fps > 0 else 30.0
    width = int(width or 1080)
    height = int(height or 1920)
    filters: list[str] = []
    zoom_terms: list[str] = []

    for edit in applied:
        start = float(edit["start_seconds"])
        end = float(edit["end_seconds"])
        strength = float(edit["strength"])
        effect_type = str(edit["type"])
        if bool(EFFECT_LIMITS[effect_type]["zoom"]):
            zoom_terms.append(_zoom_term(edit, fps))
        elif effect_type == "color_pop":
            filters.append(
                "eq="
                f"contrast={1.0 + 0.15 * strength:.2f}:"
                f"saturation={1.0 + 0.60 * strength:.2f}:"
                f"enable='between(t,{start:.3f},{end:.3f})'"
            )
        elif effect_type == "bw_moment":
            filters.append(f"hue=s=0:enable='between(t,{start:.3f},{end:.3f})'")
        elif effect_type == "flash":
            filters.append(
                f"eq=brightness=0.35:enable='between(t,{start:.3f},{min(start + FLASH_SECONDS, end):.3f})'"
            )
        elif effect_type == "vignette":
            filters.append(f"vignette=angle=PI/4.5:enable='between(t,{start:.3f},{end:.3f})'")

    if zoom_terms:
        expression = "1+" + "+".join(zoom_terms)
        filters.append(
            "zoompan="
            f"z='{expression}':"
            "x='iw/2-(iw/zoom)/2':"
            f"y='ih*{ZOOM_CENTER_Y}-(ih/zoom)/2':"
            f"d=1:fps={fps:g}:s={width}x{height}"
        )
    return (",".join(filters) if filters else None), applied
