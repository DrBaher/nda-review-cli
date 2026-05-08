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

# Quality gate 1: ensure Step 3 has actionable amendment points
POINTS_COUNT=$(grep -E '^## [0-9]+\.' "$LATEST_REDLINE" | wc -l | tr -d ' ')
if [[ "${POINTS_COUNT:-0}" -eq 0 ]]; then
  echo "Quality gate failed: no amendment points found in $LATEST_REDLINE"
  echo "Run step2/step3 again after confirming pass-2 decisions."
  exit 1
fi

# Quality gate 2: contradiction scan for AI usage clauses (simple heuristic)
DOC_TXT=$(textutil -convert txt -stdout "$SRC_DOCX" 2>/dev/null || true)
if [[ -n "$DOC_TXT" ]]; then
  if echo "$DOC_TXT" | grep -Eiq 'allow|permit.*AI|training' && echo "$DOC_TXT" | grep -Eiq 'prohibit|forbid|shall not.*AI'; then
    echo "Quality gate warning: potential AI usage contradiction detected (allow + prohibit signals)."
    echo "Please resolve in pass-2 before final tracked redline where possible."
  fi
fi

# Quality gate 3: structured checks via CLI (fail-fast)
TMP_SRC_TXT="$(mktemp /tmp/nda-step4-src-XXXXXX.txt)"
TMP_QG_JSON="$(mktemp /tmp/nda-step4-qg-XXXXXX.json)"
cleanup_tmp() { rm -f "$TMP_SRC_TXT" "$TMP_QG_JSON"; }
trap cleanup_tmp EXIT

textutil -convert txt -output "$TMP_SRC_TXT" "$SRC_DOCX" >/dev/null 2>&1 || true
if [[ -s "$TMP_SRC_TXT" ]]; then
  if ! python3 "$BASE/nda_review_cli.py" quality-gate --redline "$LATEST_REDLINE" --source-text "$TMP_SRC_TXT" --out-json "$TMP_QG_JSON"; then
    echo "Quality gate failed. See: $TMP_QG_JSON"
    exit 1
  fi
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
