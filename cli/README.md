# OpenShorts CLI

Clip long videos into vertical 9:16 shorts from the terminal. The CLI has no
provider or account credentials of its own: it talks to the local OpenShorts
REST API, which uses the instance's configured BYOK keys.

```bash
pip install openshorts        # or: uvx openshorts / pipx run openshorts
export OPENSHORTS_API_URL=http://localhost:8000

openshorts process "https://youtube.com/watch?v=..." --wait
openshorts clips <job_id>
openshorts publish <job_id> 0 --platforms tiktok,youtube
```

For pipelines, pass `--webhook` and `--webhook-secret`. The API sends one
HMAC-signed callback (`X-OpenShorts-Signature`) when the job finishes, with
local clip URLs and download endpoints.

The same local API is available to the dashboard and MCP server. There is no
account login, subscription, usage quota, or hosted endpoint.
