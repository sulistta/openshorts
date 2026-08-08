# OpenShorts

OpenShorts is a self-hosted AI video pipeline. It turns long videos into
vertical clips, adds subtitles and effects, and optionally publishes through
provider APIs that you bring yourself. Projects and clip history live on the
local `output/` volume.

This repository runs as a single-tenant local service. It has no account
login, subscription, quota, billing, or remote migration path.

## Quick start with Docker

```bash
cp .env.example .env
# Put GEMINI_API_KEY in .env, or send X-Gemini-Key per request.
docker compose up --build
```

Open the dashboard at <http://localhost:5175> and the API at
<http://localhost:8000/docs>. Mount `./output` and `./uploads` on persistent
storage in production. The backend serves local media under `/videos`.

## Provider keys (BYOK)

The only application-level provider environment variables are optional:

| Variable | Used for |
| --- | --- |
| `GEMINI_API_KEY` | AI analysis, titles, effects and YouTube Studio |
| `UPLOAD_POST_API_KEY` | Optional social publishing |
| `UPLOAD_POST_USER_ID` | Optional Upload-Post profile for API/CLI publishing |
| `YOUTUBE_COOKIES` | Optional YouTube URL ingestion |
| `WHISPER_MODEL`, `WHISPER_DEVICE`, `WHISPER_COMPUTE` | Local transcription tuning |
| `OUTPUT_MAX_GB`, `UPLOADS_MAX_GB` | Local disk caps for transient work |

For browser-only BYOK, configure keys in Settings. Requests can also include
`X-Gemini-Key` and `X-Upload-Post-Key` headers. Keys are not written to the
project library.

## Local persistence

Completed jobs receive a `.openshorts-project.json` manifest in
`output/<job_id>/`. The backend rebuilds its in-memory index from these
manifests after a restart. `GET /api/projects` and `GET /api/history` list the
library; `POST /api/projects/{job_id}/restore` reopens a project; edits are
saved with `PUT /api/projects/{job_id}/state`.

Deletion is explicit:

```bash
curl -X DELETE 'http://localhost:8000/api/projects/<job_id>?confirm=true'
```

Projects do not expire automatically; only temporary uploads and transient
files are subject to local disk cleanup.

## API, MCP and CLI

The REST API is documented at `/docs`. The local MCP endpoint is `/mcp` and
exposes `process_video`, `get_job_status`, `list_clips`, `add_subtitles`, and
`publish_clip`. It is intentionally unauthenticated for a trusted network;
forward BYOK headers when a tool needs a provider key.

```bash
export OPENSHORTS_API_URL=http://localhost:8000
uvx openshorts process 'https://youtube.com/watch?v=...' --wait
openshorts clips <job_id>
```

Use `webhook_url` and `webhook_secret` on `/api/process` for an HMAC-signed
completion callback. Callback clip links point to the local instance and stay
available until the project is explicitly deleted.

## Development

Backend requirements are in `requirements.txt`:

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
uvicorn app:app --reload
```

Dashboard development:

```bash
cd dashboard
npm install
npm run dev
```

Run Python syntax checks with `python3 -m py_compile app.py mcp_server.py
local_library.py`. The test suite contains pipeline and storage tests; some
media tests require FFmpeg and model dependencies.

## License

OpenShorts is released under the MIT License. See [LICENSE](LICENSE).
