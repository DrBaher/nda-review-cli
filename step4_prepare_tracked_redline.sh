#!/usr/bin/env bash
set -euo pipefail

BASE="$(cd "$(dirname "$0")" && pwd)"
SRC_DOCX="${1:-}"
COUNTERPARTY="${2:-}"
EDITOR_NAME="${3:-Medicus AI Legal}"

if [[ -z "$SRC_DOCX" || -z "$COUNTERPARTY" ]]; then
  echo "Usage: $0 /path/to/source.docx 'Counterparty Name' ['Editor Name']"
  exit 1
fi
if [[ ! -f "$SRC_DOCX" ]]; then
  echo "File not found: $SRC_DOCX"
  exit 1
fi

LATEST_REDLINE=$(ls -t "$BASE"/output/reviews/redline-instructions-*.md | head -n1 || true)
if [[ -z "$LATEST_REDLINE" ]]; then
  echo "No step-3 redline instructions found. Run step3_redline_pack.sh first."
  exit 1
fi

STAMP="$(date +%Y%m%d-%H%M%S)"
SAFE_CP=$(echo "$COUNTERPARTY" | tr ' /' '__' | tr -cd '[:alnum:]_-.')
OUTDIR="$BASE/output/tracked-redline/$SAFE_CP-$STAMP"
mkdir -p "$OUTDIR"

OUT_DOCX="$OUTDIR/CDA_${SAFE_CP}_MedicusAI_TRACKED.docx"
cp "$SRC_DOCX" "$OUT_DOCX"

RUNBOOK="$OUTDIR/TRACKED_CHANGES_RUNBOOK.md"
cat > "$RUNBOOK" <<EOF
# Step 4 — Tracked Word Redline Package

- Counterparty: **$COUNTERPARTY**
- Editor/Reviewer name to set in Word: **$EDITOR_NAME**
- Source copied to: $OUT_DOCX
- Redline instructions (Step 3):
  - $LATEST_REDLINE

## Apply in Word (required for true tracked changes)
1. Open: $OUT_DOCX
2. Set reviewer identity in Word to: **$EDITOR_NAME**
3. Turn on **Track Changes**
4. Apply each amendment from: 
   - $LATEST_REDLINE
5. Save as final tracked version (same file or _v2 suffix).

## Output expectation
- A .docx with native tracked changes authored under reviewer: **$EDITOR_NAME**.
EOF

echo "Step 4 package ready:"
echo "- $OUT_DOCX"
echo "- $RUNBOOK"
