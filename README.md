# OpenShorts

OpenShorts is a local desktop application that turns long videos into vertical
clips and adds subtitles and effects. It uses Tauri v2 for the native shell and
runs the Python video pipeline as a loopback-only sidecar.

The project has no hosted account, subscription, quota, billing, or remote
project storage. In packaged builds, projects live in the operating system's
application-data directory; in source development, they live under `output/`.

## Run in development

Install the Tauri prerequisites for your operating system, Python 3.11+, Node
20+, Rust, and FFmpeg. Then create a Python environment and launch the desktop
app:

```bash
cd dashboard
npm install
npm run setup:backend
npm run tauri:dev
```

`setup:backend` creates `.venv` at the repository root and installs the Python
requirements with CPU PyTorch wheels by default. `tauri:dev` automatically uses that environment, starts the
local backend on `127.0.0.1:37831`, and opens the native window. Browser-only
frontend work remains available with `npm run dev`; it proxies API calls to
that same local backend.

For a CUDA or ROCm build, set `OPENSHORTS_TORCH_INDEX` to the matching PyTorch
wheel index before running `npm run setup:backend` or `npm run tauri:build`.

## Build native installers

Build each target on a matching operating system and architecture. The build
packages the Python backend with PyInstaller, names the sidecar for Tauri's
target triple, then produces the platform installer.

```bash
cd dashboard
npm install
npm run setup:backend
npm run tauri:build
```

The backend bundle includes the Python application and its model libraries.
FFmpeg remains a native system prerequisite, so it must be installed and
available on `PATH` on each computer that processes video.

## Provider keys (BYOK)

The optional provider environment variables are:

| Variable | Used for |
| --- | --- |
| `GEMINI_API_KEY` | AI analysis, titles, effects and thumbnail studio |
| `YOUTUBE_COOKIES` | Optional YouTube URL ingestion |
| `WHISPER_MODEL`, `WHISPER_DEVICE`, `WHISPER_COMPUTE` | Local transcription tuning |
| `AUTO_CAPTIONS` | Set to `1` to burn subtitles automatically on new clips (off by default) |
| `OUTPUT_MAX_GB`, `UPLOADS_MAX_GB` | Local disk caps for transient work |

For browser-supplied BYOK, configure the Gemini key in Settings or send it as
`X-Gemini-Key`; keys are not stored in the project library. OpenShorts creates,
edits, and downloads clips locally; it does not publish to social networks.

## Local API, MCP and CLI

While OpenShorts is running, its API is deliberately bound only to
`http://127.0.0.1:37831`. The REST documentation is at `/docs` and the MCP
endpoint is `/mcp`. This allows the bundled CLI and local automation tools to
use the same active application without exposing a network service.

```bash
export OPENSHORTS_API_URL=http://127.0.0.1:37831
uvx openshorts process 'https://youtube.com/watch?v=...' --wait
openshorts clips <job_id>
```

Use `webhook_url` and `webhook_secret` on `/api/process` for an HMAC-signed
completion callback. Callback links are local to the running app and remain
available until the project is explicitly deleted.

## Development checks

```bash
python3 -m py_compile app.py mcp_server.py local_library.py desktop/backend.py
cd dashboard && npm run build
cd dashboard/src-tauri && cargo check
```

Some media checks require FFmpeg and model dependencies.

## License

OpenShorts is released under the MIT License. See [LICENSE](LICENSE).
