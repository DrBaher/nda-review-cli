# Deploying the demo

The `web/` directory is a self-contained sandboxed demo that wraps the CLI behind a stdlib-only HTTP service. Three deployment targets are pre-configured here. Pick whichever fits your stack — they're all functionally equivalent because they all build from the same `deploy/Dockerfile`.

## Railway

```bash
# Install the Railway CLI: https://docs.railway.app/develop/cli
railway login
railway init   # link to a new or existing project
railway up     # uses railway.json + deploy/Dockerfile automatically
```

`railway.json` points to `deploy/Dockerfile`, sets `/api/health` as the health probe path, and configures restart-on-failure.

## Fly.io

```bash
# Install flyctl: https://fly.io/docs/hands-on/install-flyctl/
fly auth login
fly launch --copy-config --no-deploy   # accept the existing fly.toml
fly deploy
```

Edit `[app]` in `deploy/fly.toml` to set your app name, and `primary_region` to a city near your audience. The default `auto_stop_machines = "stop"` puts the service to sleep when idle (cheap) and wakes it on the next request (~1s cold start).

## Render

```bash
# In the Render dashboard:
#   New → Blueprint → connect this repo, point at deploy/render.yaml
# Or via CLI:
render deploy --service nda-review-cli-demo
```

`render.yaml` declares a Docker web service with `/api/health` as the readiness path.

## Configuration (env vars)

All three targets accept the same env vars; see `web/server.py` for the full list. Most-likely-to-tune:

| Var | Default | Purpose |
|---|---|---|
| `PORT` | `8080` | Service port; Railway/Fly/Render set this automatically |
| `NDA_DEMO_SESSION_TTL` | `1800` (30 min) | Auto-expire idle session sandboxes |
| `NDA_DEMO_MAX_SESSIONS` | `200` | Concurrent-session cap; oldest expired first when full |
| `NDA_DEMO_RATE_LIMIT` | `30` | Requests per IP per minute |
| `NDA_DEMO_SUBPROCESS_TIMEOUT` | `30` | Seconds before any single CLI invocation is killed |

## What's bundled into the image

`deploy/Dockerfile` only copies the files the demo actually needs:

- `nda_review_cli.py`, `rule_engine.py` — the CLI itself
- `config/default-policy.json`, `config/scoring-profiles.json`, `config/llm.json.example` — committed config
- `templates/*.md` — bundled NDA templates
- `tests/fixtures/sample_nda.txt` — demo's review-card fixture
- `web/` — the HTTP service + frontend

Image is ~80 MB on `python:3.11-slim`. No additional Python packages installed (stdlib only).

## Operational notes

- **Sandbox cleanup** runs in a background thread inside the service; no cron/sidecar needed.
- **Logs** go to stderr — Railway/Fly/Render all aggregate stderr into their dashboards.
- **Health probe** lives at `/api/health` and reports `{ "ok": true, "sessions": N }`.
- **No persistence** is expected. Restart wipes all sandboxes; that's intentional.

## Local Docker test

```bash
docker build -f deploy/Dockerfile -t nda-demo:local .
docker run --rm -p 8080:8080 nda-demo:local
# Open http://localhost:8080
```

## Updating

Re-deploy by pushing to the linked branch. Railway/Fly/Render all watch the linked branch and rebuild on push (subject to your project's autoDeploy setting).
