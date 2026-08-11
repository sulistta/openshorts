# OpenShorts + n8n

Importable workflow: a video URL goes in through a form, OpenShorts clips it,
and a signed webhook returns the finished clips. No polling loop is required.

## Import

1. In n8n, choose **Workflows → Import from file** and select
   `openshorts-clip-and-notify.json`.
2. Set the backend URL in the **Start OpenShorts job** node (default:
   `http://127.0.0.1:37831`). Keep the OpenShorts desktop app running. Add an `X-Gemini-Key` header if the app does
   not define `GEMINI_API_KEY`.
3. Copy the production URL from **Clips ready (webhook)** and put it in the
   `webhook_url` field of the start node.
4. Optionally set `webhook_secret`. OpenShorts signs the raw body with
   HMAC-SHA256 and sends `X-OpenShorts-Signature: sha256=<hex>`.

## Webhook payload

```json
{
  "event": "job.completed",
  "job_id": "…",
  "status": "completed",
  "clips": [
    { "index": 0, "title": "…", "video_url": "/videos/…", "download_url": "/api/projects/…/clips/0/download" }
  ]
}
```

Failed jobs fire the same webhook with `"event": "job.failed"` and an
`error` field. URLs point at the local OpenShorts app and do not expire unless
you delete the project.

## Next steps

After **One item per clip**, chain notifications, save the local download links,
or send the finished files to your own workflow.
