---
name: openshorts
version: 1.0.0
description: Use the local OpenShorts desktop app to turn long videos into vertical clips and edit captions.
homepage: https://github.com/mutonby/openshorts
metadata:
  openclaw:
    emoji: "🎬"
    primaryEnv: OPENSHORTS_API_URL
  hermes:
    category: media
    tags: [video, clips, shorts, captions, automation]
---

# OpenShorts: clip and edit video

OpenShorts turns a long video into vertical clips with word-level subtitles.
Jobs are asynchronous: submit, then use a webhook or poll until completion.

## Connect

Point MCP or REST at the running desktop app. The default is
`http://127.0.0.1:37831`; there is no account login or service API key.
Provider BYOK headers are optional:

- `X-Gemini-Key` for AI analysis and editing

## Core loop

1. Submit `POST /api/process` with `{"url":"...", "acknowledged":true}`.
2. Poll `GET /api/status/{job_id}` every few seconds, or provide
   `webhook_url` and `webhook_secret` for an HMAC-signed callback.
3. Read `result.clips` and use their local `video_url` or download endpoint.
4. Optionally restyle captions with `POST /api/subtitle`, then download the
   finished clips locally.

The MCP server exposes `process_video`, `get_job_status`, `list_clips`, and
`add_subtitles`.

## Rules

- Only process content the user owns or is authorized to use.
- Projects are durable in the local application-data directory; deletion is
  explicit through the dashboard or DELETE API endpoint.
- Keep provider costs and credentials under the user's own accounts.

## CLI shortcut

```bash
export OPENSHORTS_API_URL=http://127.0.0.1:37831
uvx openshorts process <url> --wait
openshorts clips <job_id>
```
