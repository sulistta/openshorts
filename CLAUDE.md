# OpenShorts contributor notes

OpenShorts is a local desktop video clipping application. Keep the backend,
desktop shell, MCP server, and CLI aligned with the loopback-only contract.

## Runtime boundaries

- In the packaged desktop app, durable projects live in the operating system's
  application-data directory; source development uses `output/`.
- A project manifest is `.openshorts-project.json`; recovery rebuilds the
  in-memory job index from manifests at startup.
- Temporary uploads may be cleaned up to enforce disk caps. Durable projects
  must not expire automatically.
- The dashboard stores BYOK provider keys locally and sends them in headers.
- The loopback API has no application login or bearer token.

## API contract

The public MCP tools are `process_video`, `get_job_status`, `list_clips`,
`add_subtitles`, and `publish_clip`. Do not add account, quota, billing, or
remote-storage assumptions to these contracts. Deletion requires an explicit
`confirm=true` query parameter.

## Useful commands

```bash
python3 -m py_compile app.py mcp_server.py local_library.py
cd dashboard && npm run build
```

Use `apply_patch` for source edits and preserve unrelated user changes.
