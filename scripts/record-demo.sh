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

say "Generate a mutual NDA in markdown + Word format:"
run "$CLI draft --base $SANDBOX --template mutual \
    --party-a 'Acme Inc.' --party-a-address '123 Main St' \
    --party-b 'Beta LLC' --party-b-address '10 Market Way' \
    --purpose 'evaluating a partnership' \
    --out $SANDBOX/output/drafts/mutual.md \
    --out-docx $SANDBOX/output/drafts/mutual.docx 2>&1 | head -20"
run "ls -la $SANDBOX/output/drafts/"

say "Doctor — corpus-free setup is now detected and skipped, not failed:"
run "$CLI doctor --base $SANDBOX 2>&1 | python3 -c 'import sys,json; d=json.loads(sys.stdin.read()); print(\"ok:\", d[\"ok\"]); [print(\" \", c[\"name\"], c[\"status\"]) for c in d[\"checks\"]]'"

say "That's it. Read GETTING_STARTED.md for scenario-based onboarding."
