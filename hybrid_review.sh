#!/usr/bin/env bash
set -euo pipefail

BASE="$(cd "$(dirname "$0")" && pwd)"
INPUT="${1:-}"

if [[ -z "$INPUT" || ! -f "$INPUT" ]]; then
  echo "Usage: $0 /path/to/nda.txt"
  exit 1
fi

# Pass 1: deterministic CLI review
"$BASE/review_nda.sh" "$INPUT" >/tmp/nda_cli_stdout.json

LATEST_JSON=$(ls -t "$BASE"/output/reviews/review-*.json | head -n1)
LATEST_MD=$(ls -t "$BASE"/output/reviews/review-*.md | head -n1)

STAMP="$(date +%Y%m%d-%H%M%S)"
PACK="$BASE/output/reviews/hybrid-approval-pack-$STAMP.md"

python3 - <<'PY' "$LATEST_JSON" "$LATEST_MD" "$INPUT" "$PACK"
import json,sys,pathlib
jpath,mdpath,input_path,pack=sys.argv[1:5]
obj=json.loads(pathlib.Path(jpath).read_text())
lines=[]
lines.append('# Hybrid NDA Review — Approval Pack')
lines.append('')
lines.append(f'- Input: `{input_path}`')
lines.append(f'- CLI JSON: `{jpath}`')
lines.append(f'- CLI MD: `{mdpath}`')
lines.append(f"- Pass 1 Decision: **{obj.get('decision','unknown').upper()}**")
lines.append(f"- Pass 1 Risk Score: **{obj.get('risk_score',0)}**")
lines.append('')
lines.append('## Pass 2 (Model) Instructions')
lines.append('Review each concern below and mark one of: **CONFIRM / DOWNGRADE / DROP**.')
lines.append('If CONFIRM or DOWNGRADE, rewrite the amendment in concrete legal language.')
lines.append('')
lines.append('## Point-by-point review sheet')
lines.append('')
concerns=obj.get('concerns_summary',[])
if not concerns:
    lines.append('_No concerns were raised by Pass 1._')
for c in concerns:
    lines.append(f"### {c.get('point')}. {c.get('clause')}")
    lines.append(f"- Severity (CLI): {c.get('severity')}")
    lines.append(f"- Concern: {c.get('concern')}")
    lines.append(f"- Proposed amendment (CLI): {c.get('recommended_amendment')}")
    lines.append('- Pass 2 decision: [CONFIRM / DOWNGRADE / DROP]')
    lines.append('- Final amendment text:')
    lines.append('')
pathlib.Path(pack).write_text('\n'.join(lines))
print(pack)
PY

echo "Hybrid pack ready:"
echo "- $PACK"
echo "- $LATEST_JSON"
echo "- $LATEST_MD"