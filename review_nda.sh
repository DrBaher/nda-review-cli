#!/usr/bin/env bash
set -euo pipefail

BASE="$(cd "$(dirname "$0")" && pwd)"
INPUT="${1:-}"

if [[ -z "$INPUT" ]]; then
  echo "Usage: $0 /path/to/nda.txt"
  exit 1
fi

if [[ ! -f "$INPUT" ]]; then
  echo "File not found: $INPUT"
  exit 1
fi

PLAYBOOK="$BASE/output/medicus_nda_playbook.json"
if [[ ! -f "$PLAYBOOK" ]]; then
  echo "Playbook missing. Building now..."
  "$BASE/nda_review_cli.py" build-playbook >/dev/null
fi

STAMP="$(date +%Y%m%d-%H%M%S)"
OUTDIR="$BASE/output/reviews"
mkdir -p "$OUTDIR"

OUT_JSON="$OUTDIR/review-$STAMP.json"
OUT_MD="$OUTDIR/review-$STAMP.md"

"$BASE/nda_review_cli.py" review \
  --file "$INPUT" \
  --out-json "$OUT_JSON" \
  --out-md "$OUT_MD"

echo "Saved:"
echo "- $OUT_JSON"
echo "- $OUT_MD"
