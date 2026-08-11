---
name: openshorts
version: 2.0.0
description: Use the local OpenShorts stdio MCP server to inspect media and create deterministic short-form video artifacts.
homepage: https://github.com/mutonby/openshorts
metadata:
  openclaw:
    emoji: "🎬"
  hermes:
    category: media
    tags: [video, clips, shorts, captions, local, mcp]
---

# OpenShorts MCP

OpenShorts is a local stdio MCP server. It does not expose REST, HTTP,
webhooks, a browser UI, social publishing or provider-key features.

## Workflow

1. Call import_media with an absolute source_path or source_url and
   confirm_rights=true.
2. Poll get_job_status until the import completes.
3. Call read_transcript and get_contact_sheet. Use those local signals to make
   editorial choices yourself.
4. Call render_clips with explicit 15-60 second ranges, output_format and
   layout.
5. Poll the render job, then use returned artifact IDs for follow-up edits.

## Editing contract

- output_format: vertical, horizontal or square.
- layout: center_crop, blur_fill or fit.
- Effects are structured decisions only: zoom_in, punch_in, zoom_pulse,
  color_pop, bw_moment, flash and vignette.
- Subtitles are disabled by default. Call add_subtitles explicitly.
- Every transformation makes a new immutable local artifact.
- Use delete_project only after explicit confirmation from the user.

All result paths are absolute local paths under OPENSHORTS_OUTPUT_DIR.
