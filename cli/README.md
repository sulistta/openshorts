# OpenShorts CLI

Clip long videos into vertical 9:16 shorts from the terminal. The CLI has no
provider or account credentials of its own: it talks to the local OpenShorts
REST API exposed by the running desktop app, which uses its configured BYOK
keys.

```bash
pip install openshorts        # or: uvx openshorts / pipx run openshorts
export OPENSHORTS_API_URL=http://127.0.0.1:37831

openshorts process "https://youtube.com/watch?v=..." --wait
openshorts clips <job_id>
openshorts publish <job_id> 0 --platforms tiktok,youtube
```

For pipelines, pass `--webhook` and `--webhook-secret`. The API sends one
HMAC-signed callback (`X-OpenShorts-Signature`) when the job finishes, with
local clip URLs and download endpoints.

Start OpenShorts before using the CLI. The same local API is available to the
desktop UI and MCP server while the app is running. There is no account login,
subscription, usage quota, or hosted endpoint.
