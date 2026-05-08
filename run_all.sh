#!/usr/bin/env bash
set -euo pipefail

BASE="$(cd "$(dirname "$0")" && pwd)"
INPUT="${1:-}"
COUNTERPARTY="${2:-Counterparty}"
REVIEWER="${3:-Medicus AI Legal}"

if [[ -z "$INPUT" || ! -f "$INPUT" ]]; then
  echo "Usage: $0 /path/to/nda.txt-or-docx 'Counterparty Name' ['Reviewer Name']"
  exit 1
fi

WORK_INPUT="$INPUT"
if [[ "$INPUT" == *.docx ]]; then
  mkdir -p "$BASE/output"
  WORK_INPUT="$BASE/output/_run_all_input_$(date +%Y%m%d-%H%M%S).txt"
  textutil -convert txt -stdout "$INPUT" > "$WORK_INPUT"
fi

# 1) deterministic review
"$BASE/review_nda.sh" "$WORK_INPUT" >/tmp/nda_run_all_det.out

# 2) hybrid pack
"$BASE/hybrid_review.sh" "$WORK_INPUT" >/tmp/nda_run_all_hybrid.out
LATEST_PACK=$(ls -t "$BASE"/output/reviews/hybrid-approval-pack-*.md | head -n1)

# 3) step3 redline instructions (will be empty until pass2 decisions are filled)
"$BASE/step3_redline_pack.sh" "$LATEST_PACK" >/tmp/nda_run_all_step3.out
LATEST_STEP3=$(ls -t "$BASE"/output/reviews/redline-instructions-*.md | head -n1)

# 4) step5 find/replace pack
"$BASE/step5_find_replace_pack.sh" "$WORK_INPUT" "$LATEST_STEP3" >/tmp/nda_run_all_step5.out
LATEST_STEP5=$(ls -t "$BASE"/output/reviews/find-replace-pack-*.md | head -n1)
LATEST_REVIEW_JSON=$(ls -t "$BASE"/output/reviews/review-*.json | head -n1)
LATEST_REVIEW_MD=$(ls -t "$BASE"/output/reviews/review-*.md | head -n1)

# Optional: prepare step4 package only for docx input
STEP4_DOCX=""
STEP4_RUNBOOK=""
if [[ "$INPUT" == *.docx ]]; then
  "$BASE/step4_prepare_tracked_redline.sh" "$INPUT" "$COUNTERPARTY" "$REVIEWER" >/tmp/nda_run_all_step4.out
  STEP4_RUNBOOK=$(ls -t "$BASE"/output/tracked-redline/*/TRACKED_CHANGES_RUNBOOK.md | head -n1)
  STEP4_DOCX=$(dirname "$STEP4_RUNBOOK")/$(ls "$(dirname "$STEP4_RUNBOOK")" | grep -E '\.docx$' | head -n1)
fi

# 5) audit manifest
STAMP="$(date +%Y%m%d-%H%M%S)"
MANIFEST="$BASE/output/reviews/run-manifest-$STAMP.json"
MANIFEST_FILES=(
  "$WORK_INPUT"
  "$LATEST_PACK"
  "$LATEST_REVIEW_JSON"
  "$LATEST_REVIEW_MD"
  "$LATEST_STEP3"
  "$LATEST_STEP5"
)
if [[ -n "$STEP4_RUNBOOK" ]]; then
  MANIFEST_FILES+=("$STEP4_RUNBOOK")
fi
if [[ -n "$STEP4_DOCX" && -f "$STEP4_DOCX" ]]; then
  MANIFEST_FILES+=("$STEP4_DOCX")
fi

"$BASE/nda_review_cli.py" create-manifest \
  --base "$BASE" \
  --counterparty "$COUNTERPARTY" \
  --playbook "$BASE/output/medicus_nda_playbook.json" \
  --decisions-source "$LATEST_PACK" \
  --files "${MANIFEST_FILES[@]}" \
  --out "$MANIFEST" >/tmp/nda_run_all_manifest.out

echo "Run-all complete:"
echo "- Hybrid approval pack: $LATEST_PACK"
echo "- Step3 redline instructions: $LATEST_STEP3"
echo "- Step5 find/replace pack: $LATEST_STEP5"
if [[ -n "$STEP4_RUNBOOK" ]]; then
  echo "- Step4 runbook: $STEP4_RUNBOOK"
  echo "- Step4 docx: $STEP4_DOCX"
fi
echo "- Audit manifest: $MANIFEST"
