#!/usr/bin/env bash
# record-demo.sh — script an asciinema recording of the canonical demo flow.
# Requires: asciinema (https://asciinema.org/), the CLI installed or on PATH.
#
# Usage:
#   asciinema rec --command "./scripts/record-demo.sh" demo.cast
#   asciinema upload demo.cast    # gives you a URL to embed
#
# Or pass --no-rec to just step through the demo locally:
#   ./scripts/record-demo.sh --no-rec
set -euo pipefail

# Headless asciinema recordings can land with TERM unset or "dumb"; either makes
# `clear` and the colored echoes below fail. Force a real terminal type if so.
if [[ -z "${TERM:-}" || "$TERM" == "dumb" ]]; then
  export TERM=xterm-256color
fi

REPO="$(cd "$(dirname "$0")/.." && pwd)"
SANDBOX="$(mktemp -d -t nda-demo-XXXXXX)"
CLI="$REPO/nda_review_cli.py"

pause() { sleep "${PAUSE:-1.5}"; }
say() { printf "\n\033[1;36m# %s\033[0m\n" "$*"; pause; }
run() { printf "\033[1;33m$ %s\033[0m\n" "$*"; pause; eval "$@"; pause; }

trap 'rm -rf "$SANDBOX"' EXIT

clear
say "NDA Review CLI — 60-second demo"
say "Sandbox: $SANDBOX"

run "$CLI --version"

say "Bare invocation prints a friendly first-run hint:"
run "$CLI || true"

say "Quickstart writes config + profile + replayable answers (interactive normally; non-interactive here):"
run "$CLI quickstart --base $SANDBOX --no-prompt --yes 2>&1 | head -20"

say "Build a playbook (corpus-free here; seeded with tiny stubs for the demo):"
mkdir -p "$SANDBOX/data/raw_strict"
echo '[{"id":"1","subject":"NDA review","body":"Mutual NDA with finite term."}]' > "$SANDBOX/data/raw_strict/gmail_primary.json"
echo '[]' > "$SANDBOX/data/raw_strict/gmail_secondary.json"
echo '[]' > "$SANDBOX/data/raw_strict/drive_primary.json"
echo '[]' > "$SANDBOX/data/raw_strict/drive_secondary.json"
run "$CLI build-playbook --base $SANDBOX 2>&1 | head -5"

say "Review the bundled sample NDA with explainability:"
run "$CLI review --base $SANDBOX --playbook $SANDBOX/output/nda_playbook.json \
    --file $REPO/tests/fixtures/sample_nda.txt --why \
    --out-md $SANDBOX/output/reviews/sample.md 2>&1 | head -20"

say "Generate a mutual NDA in your house language (markdown + Word format):"
run "$CLI draft --base $SANDBOX --template mutual \
    --party-a 'Acme Inc.' --party-a-address '123 Main St' \
    --party-b 'Beta LLC' --party-b-address '10 Market Way' \
    --purpose 'evaluating a partnership' \
    --out $SANDBOX/output/drafts/mutual.md \
    --out-docx $SANDBOX/output/drafts/mutual.docx 2>&1 | head -10"

say "Or generate the Common Paper Mutual NDA Version 1.0 (CC BY 4.0) — industry standard:"
run "$CLI draft --base $SANDBOX --template common-paper-mutual \
    --party-a 'Acme Inc.' --party-a-address '123 Main St' \
    --party-b 'Beta LLC' --party-b-address '10 Market Way' \
    --purpose 'evaluating a partnership' \
    --governing-law 'California' \
    --out $SANDBOX/output/drafts/cp-mutual.md \
    --out-docx $SANDBOX/output/drafts/cp-mutual.docx 2>&1 | head -10"

say "Two-party negotiation — both sides drive their own CLI; state is one tamper-evident JSON:"
mkdir -p "$SANDBOX/neg/a" "$SANDBOX/neg/b"
"$CLI" quickstart --base "$SANDBOX/neg/a" --no-prompt --yes >/dev/null
"$CLI" quickstart --base "$SANDBOX/neg/b" --no-prompt --yes >/dev/null
python3 - <<PY >/dev/null
import json
for d, name in [("$SANDBOX/neg/a", "Acme"), ("$SANDBOX/neg/b", "Beta")]:
    p = f"{d}/config/org-policy.json"
    o = json.load(open(p))
    o["org_name"] = name
    json.dump(o, open(p, "w"))
PY
run "$CLI negotiate init --base $SANDBOX/neg/a --template mutual \
     --party-a-name 'Acme' --party-a-address '1 Main' \
     --party-b-name 'Beta' --party-b-address '2 Side' \
     --purpose 'demo deal' --effective-date '2026-01-01' \
     --out $SANDBOX/neg/state.json 2>&1 | head -3"
run "$CLI negotiate counter --base $SANDBOX/neg/b --state $SANDBOX/neg/state.json --auto 2>&1 | head -3"
run "$CLI negotiate accept --base $SANDBOX/neg/a --state $SANDBOX/neg/state.json --as a 2>&1 | head -3"
run "$CLI negotiate sign-off --base $SANDBOX/neg/a --state $SANDBOX/neg/state.json --as a --yes 2>&1 | tail -3 && \
     $CLI negotiate sign-off --base $SANDBOX/neg/b --state $SANDBOX/neg/state.json --as b --yes 2>&1 | tail -3"
run "$CLI negotiate finalize --base $SANDBOX/neg/a --state $SANDBOX/neg/state.json \
       --out-md $SANDBOX/neg/agreed.md --out-docx $SANDBOX/neg/agreed.docx 2>&1 | head -3"

say "Doctor — corpus-free setup is detected and skipped, not failed:"
run "$CLI doctor --base $SANDBOX 2>&1 | python3 -c 'import sys,json; d=json.loads(sys.stdin.read()); print(\"ok:\", d[\"ok\"]); [print(\" \", c[\"name\"], c[\"status\"]) for c in d[\"checks\"]]'"

say "That's it. Read GETTING_STARTED.md for scenario-based onboarding."
