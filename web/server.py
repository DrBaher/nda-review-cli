#!/usr/bin/env python3
"""Sandboxed read-only web demo for nda-review-cli.

Runs the CLI in per-session temp directories. Stateless across restarts;
sessions auto-expire after SESSION_TTL_SECONDS. Exposes three demo flows:

    POST /api/draft     — render a draft NDA (mutual / one-way-out / common-paper-mutual)
    POST /api/review    — score the bundled sample NDA against quickstart defaults
    POST /api/simulate  — run negotiate simulate with chosen stances; return trajectory

Plus:
    GET  /              — single-page demo UI
    GET  /static/*      — static assets
    GET  /api/download/<session_id>/<filename>  — download a generated artifact
    GET  /api/health    — readiness probe

Stdlib only; no Flask, no FastAPI. Threading server so concurrent sessions
don't block each other on subprocess calls.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from collections import defaultdict, deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

# ---------------------------------------------------------------------------
# Configuration

REPO_ROOT = Path(__file__).resolve().parent.parent
CLI = REPO_ROOT / "nda_review_cli.py"
STATIC_DIR = Path(__file__).resolve().parent / "static"
SANDBOX_ROOT = Path(os.environ.get("NDA_DEMO_SANDBOX", "/tmp/nda-demo-sandbox"))

SESSION_TTL_SECONDS = int(os.environ.get("NDA_DEMO_SESSION_TTL", "1800"))   # 30 min
MAX_CONCURRENT_SESSIONS = int(os.environ.get("NDA_DEMO_MAX_SESSIONS", "200"))
RATE_LIMIT_PER_MINUTE = int(os.environ.get("NDA_DEMO_RATE_LIMIT", "30"))
SUBPROCESS_TIMEOUT = int(os.environ.get("NDA_DEMO_SUBPROCESS_TIMEOUT", "30"))
MAX_INPUT_FIELD_LEN = 200
MAX_PURPOSE_LEN = 500
PORT = int(os.environ.get("PORT", "8080"))

# Field validation: keep input ASCII-printable + a few common unicode chars,
# refuse control characters or anything that could be CLI-flag-like.
SAFE_FIELD_RE = re.compile(r"^[\w\s\.,\-_'\"&/()@#:!? -￿]*$")

# ---------------------------------------------------------------------------
# Session + rate-limit state (thread-safe)

_sessions = {}      # session_id -> {created_at, sandbox_dir}
_sessions_lock = threading.Lock()
_rate_limits = defaultdict(deque)   # ip -> deque of timestamps
_rate_limits_lock = threading.Lock()


def _new_session():
    with _sessions_lock:
        if len(_sessions) >= MAX_CONCURRENT_SESSIONS:
            # Drop oldest non-expired to make room
            oldest = min(_sessions.items(), key=lambda kv: kv[1]["created_at"])[0]
            _expire_session(oldest)
        sid = uuid.uuid4().hex
        sandbox = SANDBOX_ROOT / sid
        sandbox.mkdir(parents=True, exist_ok=True)
        _sessions[sid] = {"created_at": time.time(), "sandbox_dir": sandbox}
        return sid, sandbox


def _expire_session(session_id):
    info = _sessions.pop(session_id, None)
    if info:
        shutil.rmtree(info["sandbox_dir"], ignore_errors=True)


def _reaper_loop():
    while True:
        time.sleep(60)
        now = time.time()
        with _sessions_lock:
            expired = [sid for sid, info in _sessions.items() if now - info["created_at"] > SESSION_TTL_SECONDS]
            for sid in expired:
                _expire_session(sid)


def _rate_check(ip):
    now = time.time()
    with _rate_limits_lock:
        q = _rate_limits[ip]
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= RATE_LIMIT_PER_MINUTE:
            return False
        q.append(now)
        return True


# ---------------------------------------------------------------------------
# Input validation helpers — keep it tight; refuse anything weird.

def _clean_field(name, value, max_len=MAX_INPUT_FIELD_LEN, allow_empty=False):
    if value is None:
        if allow_empty:
            return ""
        raise ValueError(f"Missing field: {name}")
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    value = value.strip()
    if not value and not allow_empty:
        raise ValueError(f"{name} is empty")
    if len(value) > max_len:
        raise ValueError(f"{name} exceeds {max_len} characters")
    if not SAFE_FIELD_RE.match(value):
        raise ValueError(f"{name} contains disallowed characters")
    return value


def _clean_choice(name, value, choices):
    if value not in choices:
        raise ValueError(f"{name} must be one of: {sorted(choices)}")
    return value


# ---------------------------------------------------------------------------
# CLI invocation (no shell=True; always list args)

def _run_cli(args, cwd=None, timeout=SUBPROCESS_TIMEOUT):
    proc = subprocess.run(
        [sys.executable, str(CLI), *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=cwd or str(REPO_ROOT),
        env={**os.environ, "NDA_CLI_QUIET": "1"},
    )
    return proc


def _quickstart(base):
    proc = _run_cli(["quickstart", "--base", str(base), "--no-prompt", "--yes"])
    if proc.returncode != 0:
        raise RuntimeError(f"quickstart failed: {proc.stderr[-400:]}")


# ---------------------------------------------------------------------------
# Endpoint handlers — each returns (status_code, response_dict).

def handle_draft(payload):
    template = _clean_choice("template", payload.get("template", "mutual"),
                             {"mutual", "one-way-out", "common-paper-mutual"})
    purpose = _clean_field("purpose", payload.get("purpose"), max_len=MAX_PURPOSE_LEN)

    sid, sandbox = _new_session()
    _quickstart(sandbox)

    out_md = sandbox / "draft.md"
    out_docx = sandbox / "draft.docx"

    args = [
        "draft",
        "--base", str(sandbox),
        "--template", template,
        "--purpose", purpose,
        "--out", str(out_md),
        "--out-docx", str(out_docx),
    ]
    if template == "one-way-out":
        args += [
            "--disclosing-party", _clean_field("disclosing_party", payload.get("disclosing_party")),
            "--disclosing-party-address", _clean_field("disclosing_party_address", payload.get("disclosing_party_address")),
            "--receiving-party", _clean_field("receiving_party", payload.get("receiving_party")),
            "--receiving-party-address", _clean_field("receiving_party_address", payload.get("receiving_party_address")),
        ]
    else:
        args += [
            "--party-a", _clean_field("party_a", payload.get("party_a")),
            "--party-a-address", _clean_field("party_a_address", payload.get("party_a_address")),
            "--party-b", _clean_field("party_b", payload.get("party_b")),
            "--party-b-address", _clean_field("party_b_address", payload.get("party_b_address")),
        ]
    if template == "common-paper-mutual":
        # Common Paper template requires a governing-law value; default to
        # California if user didn't supply one (matches their published default).
        args += ["--governing-law", _clean_field("governing_law", payload.get("governing_law") or "California", max_len=80)]

    proc = _run_cli(args)
    if proc.returncode != 0:
        return 400, {"ok": False, "error": proc.stderr[-400:] or "draft failed"}

    return 200, {
        "ok": True,
        "session_id": sid,
        "markdown": out_md.read_text(),
        "download_docx": f"/api/download/{sid}/draft.docx",
        "stdout": proc.stdout[-200:] if proc.stdout else "",
    }


def handle_review(payload):
    sid, sandbox = _new_session()
    _quickstart(sandbox)

    # Build a minimal playbook for the demo (corpus-free).
    raw = sandbox / "data" / "raw_strict"
    raw.mkdir(parents=True, exist_ok=True)
    for name in ("gmail_primary.json", "gmail_secondary.json", "drive_primary.json", "drive_secondary.json"):
        (raw / name).write_text("[]")
    proc = _run_cli(["build-playbook", "--base", str(sandbox)])
    if proc.returncode != 0:
        return 500, {"ok": False, "error": "playbook build failed: " + proc.stderr[-300:]}

    sample = REPO_ROOT / "tests" / "fixtures" / "sample_nda.txt"
    out_json = sandbox / "review.json"
    out_md = sandbox / "review.md"
    why = bool(payload.get("why", True))
    args = [
        "review",
        "--base", str(sandbox),
        "--playbook", str(sandbox / "output" / "nda_playbook.json"),
        "--file", str(sample),
        "--out-json", str(out_json),
        "--out-md", str(out_md),
    ]
    if why:
        args.append("--why")
    proc = _run_cli(args)
    if proc.returncode != 0:
        return 400, {"ok": False, "error": proc.stderr[-400:] or "review failed"}

    review = json.loads(out_json.read_text())
    return 200, {
        "ok": True,
        "session_id": sid,
        "decision": review.get("decision"),
        "risk_score": review.get("risk_score"),
        "findings": [
            {
                "clause": f.get("clause"),
                "severity": f.get("severity"),
                "concern": f.get("concern") or f.get("preferred_position"),
                "snippet": (f.get("clause_snippet") or "")[:240],
                "rule_hits": f.get("rule_hits", [])[:5],
            }
            for f in review.get("findings", [])
        ],
        "markdown": out_md.read_text() if out_md.exists() else "",
    }


def handle_simulate(payload):
    stance_a = _clean_choice("stance_a", payload.get("stance_a", "balanced"),
                             {"conservative", "middleground", "compromising"})
    stance_b = _clean_choice("stance_b", payload.get("stance_b", "balanced"),
                             {"conservative", "middleground", "compromising"})
    diverge_b = bool(payload.get("diverge_b", True))

    sid, sandbox = _new_session()
    party_a = sandbox / "a"
    party_b = sandbox / "b"
    _quickstart(party_a)
    _quickstart(party_b)

    # Make B's preferred clause text differ on a few clauses so we have
    # something to actually negotiate over.
    if diverge_b:
        b_policy = party_b / "config" / "org-policy.json"
        o = json.loads(b_policy.read_text())
        o["org_name"] = "Beta"
        o["clause_rules"]["term_and_survival"]["preferred"] = "NDA term 5 years, survival 10 years."
        o["clause_rules"]["return_or_destroy"]["preferred"] = "Destroy and certify within 7 days."
        o["clause_rules"]["residuals"]["preferred"] = "Accept broad residual knowledge."
        o["clause_rules"]["mutuality"]["preferred"] = "Unilateral receiving-party-bound NDA only."
        b_policy.write_text(json.dumps(o))

    a_policy = party_a / "config" / "org-policy.json"
    o = json.loads(a_policy.read_text())
    o["org_name"] = "Acme"
    a_policy.write_text(json.dumps(o))

    out_report = sandbox / "report.json"
    out_state = sandbox / "state.json"
    proc = _run_cli([
        "negotiate", "simulate",
        "--party-a-base", str(party_a),
        "--party-b-base", str(party_b),
        "--stance-a", stance_a,
        "--stance-b", stance_b,
        "--mode", "auto",
        "--max-rounds", "12",
        "--out", str(out_report),
        "--state", str(out_state),
    ], timeout=45)
    if proc.returncode != 0:
        return 400, {"ok": False, "error": proc.stderr[-400:] or "simulate failed"}

    report = json.loads(out_report.read_text())

    # Enrich the trajectory with per-round amendment_source + stance + summary,
    # which simulate's report omits but the state file contains. Lets the demo
    # UI surface "auto:conservative+fatigue" tags so users can SEE fatigue
    # concession kick in when they pick the conservative-x-conservative pair.
    state = json.loads(out_state.read_text()) if out_state.exists() else {"rounds": []}
    rounds_by_index = {r["round"]: r for r in state.get("rounds", [])}
    enriched_trajectory = []
    for t in report.get("trajectory", []) or []:
        r = rounds_by_index.get(t.get("round"), {})
        enriched_trajectory.append({
            **t,
            "amendment_source": r.get("amendment_source"),
            "stance": r.get("stance"),
            "fatigue_concessions": r.get("fatigue_concessions") or [],
        })

    return 200, {
        "ok": True,
        "session_id": sid,
        "outcome": report.get("outcome"),
        "rounds_used": report.get("rounds_used"),
        "stances": report.get("stances"),
        "trajectory": enriched_trajectory,
        "winner_per_clause": report.get("winner_per_clause"),
        "block_diagnosis": report.get("block_diagnosis"),
        "final_status": report.get("final_status"),
    }


# ---------------------------------------------------------------------------
# HTTP plumbing

class DemoHandler(BaseHTTPRequestHandler):
    server_version = "nda-review-cli-demo/0.1"

    def log_message(self, fmt, *args):
        sys.stderr.write(f"[{self.address_string()}] {fmt % args}\n")

    # ------- utilities

    def _client_ip(self):
        # Honor X-Forwarded-For when behind Railway/Fly/Render
        fwd = self.headers.get("X-Forwarded-For", "")
        return fwd.split(",")[0].strip() if fwd else self.client_address[0]

    def _send_json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_static(self, path):
        full = STATIC_DIR / path
        if not full.exists() or not full.is_file():
            return self._send_json(404, {"ok": False, "error": "not found"})
        # Avoid path traversal
        try:
            full.resolve().relative_to(STATIC_DIR.resolve())
        except ValueError:
            return self._send_json(403, {"ok": False, "error": "forbidden"})
        ext = full.suffix.lower()
        ctype = {
            ".html": "text/html; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
            ".svg": "image/svg+xml",
            ".png": "image/png",
            ".ico": "image/x-icon",
        }.get(ext, "application/octet-stream")
        body = full.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "public, max-age=300")
        self.end_headers()
        self.wfile.write(body)

    def _read_json_body(self):
        length = int(self.headers.get("Content-Length") or "0")
        if length <= 0:
            return {}
        if length > 50_000:
            return None
        raw = self.rfile.read(length).decode("utf-8", errors="replace")
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None

    # ------- dispatch

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path in ("/", "/index.html"):
            return self._send_static("index.html")
        if parsed.path.startswith("/static/"):
            return self._send_static(parsed.path[len("/static/"):])
        if parsed.path == "/api/health":
            return self._send_json(200, {"ok": True, "sessions": len(_sessions)})
        if parsed.path.startswith("/api/download/"):
            return self._handle_download(parsed.path)
        return self._send_json(404, {"ok": False, "error": "not found"})

    def do_POST(self):
        if not _rate_check(self._client_ip()):
            return self._send_json(429, {"ok": False, "error": "rate limit exceeded"})

        path = urlparse(self.path).path
        payload = self._read_json_body()
        if payload is None:
            return self._send_json(400, {"ok": False, "error": "invalid JSON body"})

        try:
            if path == "/api/draft":
                status, body = handle_draft(payload)
            elif path == "/api/review":
                status, body = handle_review(payload)
            elif path == "/api/simulate":
                status, body = handle_simulate(payload)
            else:
                return self._send_json(404, {"ok": False, "error": "unknown endpoint"})
        except ValueError as e:
            return self._send_json(400, {"ok": False, "error": str(e)})
        except subprocess.TimeoutExpired:
            return self._send_json(504, {"ok": False, "error": "CLI subprocess timed out"})
        except Exception as e:
            sys.stderr.write(f"[ERROR] {path}: {e}\n")
            return self._send_json(500, {"ok": False, "error": "internal error"})

        return self._send_json(status, body)

    def _handle_download(self, path):
        # /api/download/<session_id>/<filename>
        parts = path.split("/")
        if len(parts) != 5:
            return self._send_json(400, {"ok": False, "error": "bad download path"})
        _, _, _, session_id, filename = parts
        if not re.match(r"^[a-f0-9]{32}$", session_id):
            return self._send_json(400, {"ok": False, "error": "bad session id"})
        if not re.match(r"^[\w.\-]+$", filename):
            return self._send_json(400, {"ok": False, "error": "bad filename"})
        info = _sessions.get(session_id)
        if not info:
            return self._send_json(404, {"ok": False, "error": "session expired or unknown"})
        full = info["sandbox_dir"] / filename
        if not full.exists() or not full.is_file():
            return self._send_json(404, {"ok": False, "error": "file not found"})
        try:
            full.resolve().relative_to(info["sandbox_dir"].resolve())
        except ValueError:
            return self._send_json(403, {"ok": False, "error": "forbidden"})
        body = full.read_bytes()
        ctype = {
            ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ".md": "text/markdown; charset=utf-8",
            ".json": "application/json; charset=utf-8",
        }.get(full.suffix.lower(), "application/octet-stream")
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.end_headers()
        self.wfile.write(body)


def main():
    SANDBOX_ROOT.mkdir(parents=True, exist_ok=True)

    threading.Thread(target=_reaper_loop, daemon=True).start()
    server = ThreadingHTTPServer(("0.0.0.0", PORT), DemoHandler)
    sys.stderr.write(
        f"nda-review-cli demo listening on :{PORT} "
        f"(ttl={SESSION_TTL_SECONDS}s max_sessions={MAX_CONCURRENT_SESSIONS} "
        f"rate_limit={RATE_LIMIT_PER_MINUTE}/min)\n"
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        sys.stderr.write("shutting down\n")
        server.shutdown()


if __name__ == "__main__":
    main()
