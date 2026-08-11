# OpenShorts MCP

OpenShorts is now a local, stdio-only MCP server. It has no dashboard, Tauri
application, REST API, webhook, social-network publishing integration, Docker
requirement, Gemini integration, thumbnail generator or automatic clip picker.

An LLM receives local transcript and contact-sheet context, decides the cut
ranges and sends only validated editing operations. FFmpeg performs the cuts,
reframe, captions, overlays and structured effects locally. No Gemini key is
read or required.

## Install

Requirements: Python 3.10+, FFmpeg and FFprobe available on PATH.

    python3 -m venv .venv
    . .venv/bin/activate
    python -m pip install -e .

The first transcription downloads the selected faster-whisper model if it is
not already cached. Inference then runs on the local machine. Select a model
or device with WHISPER_MODEL, WHISPER_DEVICE and WHISPER_COMPUTE.

## One-time legacy migration

The old output directory is intentionally never moved when the MCP starts.
If this repository already has legacy projects under output, run this explicit
command once:

    .venv/bin/openshorts-mcp migrate-legacy

It renames output to output-legacy-YYYYMMDD-HHMMSS and creates a clean output
directory. Set OPENSHORTS_OUTPUT_DIR before the command to migrate a different
dedicated output folder. Existing legacy media is preserved; it is not
compatible with the new artifact manifest format.

## Connect an MCP client

Use the installed executable as a stdio server. A typical MCP configuration is:

    {
      "mcpServers": {
        "openshorts": {
          "command": "/absolute/path/to/openshorts/.venv/bin/openshorts-mcp",
          "env": {
            "OPENSHORTS_OUTPUT_DIR": "/absolute/path/to/openshorts/output"
          }
        }
      }
    }

Do not configure an HTTP URL. Starting the command opens no port; stdout is
reserved exclusively for MCP messages.

## Editing workflow

1. Call import_media with one absolute local source_path or public source_url
   and confirm_rights=true.
2. Poll get_job_status until it completes.
3. Call read_transcript and get_contact_sheet. The contact sheet is returned as
   an MCP image as well as an absolute local JPEG path.
4. The LLM chooses exact 15-60 second ranges, then calls render_clips.
5. Poll the render job. Its result exposes immutable artifact IDs and absolute
   paths.
6. Optionally call apply_effects, add_subtitles or add_hook_overlay. Each
   creates a new artifact and leaves its parent intact.

Available reframe layouts are center_crop, blur_fill and fit. Output formats
are vertical, horizontal and square. Effects are zoom_in, punch_in, zoom_pulse,
color_pop, bw_moment, flash and vignette. Raw FFmpeg filters are not accepted.
Zoom effects are refused after text layers so captions and hooks stay visible.

Subtitles are disabled by default and are only burned when add_subtitles is
called explicitly.

## Local data

OPENSHORTS_OUTPUT_DIR defaults to ./output. The server stores:

- copied source media;
- media metadata, word-level transcript and contact sheets;
- persistent job states;
- immutable MP4 artifacts and subtitle sidecars.

Use list_projects, get_project and delete_project to manage that local state.
delete_project requires confirm=true and permanently removes that one project's
copied source, analysis and artifacts.

## Development checks

    .venv/bin/python -m pip install -e ".[dev]"
    .venv/bin/python -m pytest
    .venv/bin/python -m py_compile openshorts_mcp/*.py

## License

OpenShorts is released under the MIT License. See [LICENSE](LICENSE).
