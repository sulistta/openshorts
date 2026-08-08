---
name: openshorts
version: 1.0.0
description: Use a self-hosted OpenShorts instance to turn long videos into vertical clips, edit captions, and optionally publish them.
homepage: https://github.com/mutonby/openshorts
metadata:
  openclaw:
    emoji: "🎬"
    primaryEnv: OPENSHORTS_API_URL
  hermes:
    category: media
    tags: [video, clips, shorts, social-media, publishing, automation]
---

# OpenShorts: clip and publish video

OpenShorts turns a long video into vertical clips with word-level subtitles.
Jobs are asynchronous: submit, then use a webhook or poll until completion.

## Connect

Point MCP or REST at the instance managed by the user. The default is
`http://localhost:8000`; there is no account login or service API key.
Provider BYOK headers are optional:

- `X-Gemini-Key` for AI analysis and editing
- `X-Upload-Post-Key` for social publishing
- `X-Upload-Post-User` for the Upload-Post profile used to publish

## Core loop

1. Submit `POST /api/process` with `{"url":"...", "acknowledged":true}`.
2. Poll `GET /api/status/{job_id}` every few seconds, or provide
   `webhook_url` and `webhook_secret` for an HMAC-signed callback.
3. Read `result.clips` and use their local `video_url` or download endpoint.
4. Optionally restyle captions with `POST /api/subtitle`, then publish with
   `POST /api/social/post`.

The MCP server exposes `process_video`, `get_job_status`, `list_clips`,
`add_subtitles`, and `publish_clip`.

## Rules

- Only process content the user owns or is authorized to use.
- Projects and gallery files are durable on the local output volume; deletion
  is explicit through the dashboard or DELETE API endpoints.
- Keep provider costs and credentials under the user's own accounts.

## CLI shortcut

```bash
export OPENSHORTS_API_URL=http://localhost:8000
uvx openshorts process <url> --wait
openshorts clips <job_id>
openshorts publish <job_id> 0 --platforms tiktok
```
