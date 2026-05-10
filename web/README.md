# nda-review-cli web demo

A sandboxed read-only demo that wraps the CLI behind a stdlib-only HTTP service. Stateless across restarts; per-session sandboxes auto-expire after 30 minutes.

## What it shows

Three cards on a single-page UI, each driving one of the headline CLI flows:

1. **Draft an NDA** — pick a template (`mutual` / `one-way-out` / `common-paper-mutual`), fill the parties + purpose, get markdown output and a `.docx` download.
2. **Review the bundled sample NDA** — runs `review` with `--why` against quickstart-default house policy and shows findings inline.
3. **Game-theoretic negotiation simulator** — pick stance A and stance B, see the per-round trajectory and which party's preferred text won each clause.

## What it deliberately doesn't show

- **No LLM features.** `--llm`, `--agent`, profile-learning all work the same way locally; install the CLI to use them. Wiring a server-side key into the demo is a cost / abuse / safety problem we'd rather not solve for a public demo.
- **No real two-party negotiate flow.** The simulator captures the same insight (does the stance pair converge or block?) without needing two browser windows + transport.
- **No persistence.** Sandboxes are wiped on TTL or restart; nothing you enter sticks around.

## Run locally

```bash
cd <repo-root>
python3 web/server.py
# Open http://localhost:8080
```

No dependencies — stdlib only. The service shells out to `nda_review_cli.py` at the repo root for every request.

## Configuration

Env vars (all optional):

| Var | Default | Notes |
|---|---|---|
| `PORT` | `8080` | Service port |
| `NDA_DEMO_SANDBOX` | `/tmp/nda-demo-sandbox` | Where per-session temp dirs live |
| `NDA_DEMO_SESSION_TTL` | `1800` | Seconds before idle session sandboxes are reaped |
| `NDA_DEMO_MAX_SESSIONS` | `200` | Concurrent-session cap |
| `NDA_DEMO_RATE_LIMIT` | `30` | Per-IP requests per minute |
| `NDA_DEMO_SUBPROCESS_TIMEOUT` | `30` | Seconds before any single CLI invocation is killed |

## Architecture

```
web/
  server.py          stdlib http.server-based service (~330 lines)
  static/
    index.html       single-page UI; three cards
    style.css        minimal, no framework
    app.js           vanilla JS; fetch() to JSON endpoints
  README.md          this file
```

The service:
- Threads via `ThreadingHTTPServer` so concurrent sessions don't block on subprocess
- Per-session UUID sandbox under `NDA_DEMO_SANDBOX`; CLI runs with `--base <sandbox>`
- Background reaper thread purges expired sandboxes every 60 s
- Per-IP rate limiter (token-bucket-ish, in-memory)
- Strict input validation: regex-allowlisted chars, length caps, choice validation; never invokes `subprocess` with `shell=True`
- Honors `X-Forwarded-For` for rate-limit keying behind Railway/Fly/Render proxies

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET`  | `/` | UI |
| `GET`  | `/static/*` | CSS / JS / favicon |
| `POST` | `/api/draft` | Render an NDA draft; returns markdown + a `download_docx` URL |
| `POST` | `/api/review` | Score the bundled sample NDA; returns decision + findings |
| `POST` | `/api/simulate` | Run `negotiate simulate`; returns trajectory + winners |
| `GET`  | `/api/download/<sid>/<filename>` | Download an artifact from a session sandbox |
| `GET`  | `/api/health` | Liveness probe |

All POST endpoints accept JSON and return JSON. Failures return `{ ok: false, error: "..." }` with a 4xx/5xx status.

## Deploying

See [`deploy/README.md`](../deploy/README.md). Pre-configured for Railway, Fly.io, and Render — all three build from the same `deploy/Dockerfile`.

## Security posture

- Stdlib-only Python — no third-party dep surface
- No shell expansion in subprocess calls — all args passed as a list
- Per-session sandbox; CLI runs as a non-root user in the container
- Path-traversal blocked on download (`resolve().relative_to(sandbox_dir)`)
- Rate limit + concurrent-session cap to bound abuse
- Default Dockerfile runs as UID 10001
- No persistent storage; restart wipes sessions

If you find a security issue with the demo, see the project root [`SECURITY.md`](../SECURITY.md) for disclosure.
