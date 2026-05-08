#!/usr/bin/env bash
set -euo pipefail

BASE="$(cd "$(dirname "$0")" && pwd)"
PACK="${1:-}"

if [[ -z "$PACK" ]]; then
  PACK=$(ls -t "$BASE"/output/reviews/hybrid-approval-pack-*.md | head -n1 || true)
fi

if [[ -z "$PACK" || ! -f "$PACK" ]]; then
  echo "Usage: $0 /path/to/hybrid-approval-pack-*.md"
  echo "(or run without args after creating at least one hybrid pack)"
  exit 1
fi

STAMP="$(date +%Y%m%d-%H%M%S)"
OUT="$BASE/output/reviews/redline-instructions-$STAMP.md"

python3 "$BASE/generate_redline_instructions.py" --pack "$PACK" --out "$OUT" >/tmp/redline_pack_path.txt

echo "Redline instruction set ready:"
cat /tmp/redline_pack_path.txt
