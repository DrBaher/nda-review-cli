#!/usr/bin/env bash
# review_nda.sh — convenience wrapper for one-shot NDA review.
# Forwards $LLM and $COUNTERPARTY env vars to the CLI when set.
# For full control, call `./nda_review_cli.py review` directly.
set -euo pipefail

BASE="$(cd "$(dirname "$0")" && pwd)"
INPUT="${1:-}"

if [[ -z "$INPUT" ]]; then
  echo "Usage: $0 /path/to/nda.txt"
  echo "  Optional env vars: COUNTERPARTY=<name>  LLM=<provider>"
  exit 1
fi

if [[ ! -f "$INPUT" ]]; then
  echo "File not found: $INPUT"
  exit 1
fi

PLAYBOOK="${PLAYBOOK:-$BASE/output/nda_playbook.json}"
if [[ ! -f "$PLAYBOOK" ]]; then
  echo "Playbook missing. Building now..."
  "$BASE/nda_review_cli.py" build-playbook >/dev/null
fi

STAMP="$(date +%Y%m%d-%H%M%S)"
OUTDIR="$BASE/output/reviews"
mkdir -p "$OUTDIR"

OUT_JSON="$OUTDIR/review-$STAMP.json"
OUT_MD="$OUTDIR/review-$STAMP.md"

EXTRA_ARGS=(--why)
if [[ -n "${COUNTERPARTY:-}" ]]; then
  EXTRA_ARGS+=(--counterparty "$COUNTERPARTY" --learn-profile)
fi
if [[ -n "${LLM:-}" ]]; then
  EXTRA_ARGS+=(--llm "$LLM" --yes-llm-send)
fi

"$BASE/nda_review_cli.py" review \
  --playbook "$PLAYBOOK" \
  --file "$INPUT" \
  --out-json "$OUT_JSON" \
  --out-md "$OUT_MD" \
  "${EXTRA_ARGS[@]}"

echo "Saved:"
echo "- $OUT_JSON"
echo "- $OUT_MD"
