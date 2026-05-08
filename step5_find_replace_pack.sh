#!/usr/bin/env bash
set -euo pipefail

BASE="$(cd "$(dirname "$0")" && pwd)"
SRC_TXT="${1:-}"
REDLINE_MD="${2:-}"

if [[ -z "$SRC_TXT" || ! -f "$SRC_TXT" ]]; then
  echo "Usage: $0 /path/to/source.txt [/path/to/redline-instructions.md]"
  exit 1
fi

if [[ -z "${REDLINE_MD:-}" ]]; then
  REDLINE_MD=$(ls -t "$BASE"/output/reviews/redline-instructions-*.md | head -n1 || true)
fi
if [[ -z "$REDLINE_MD" || ! -f "$REDLINE_MD" ]]; then
  echo "Redline instructions not found. Run Step 3 first."
  exit 1
fi

STAMP="$(date +%Y%m%d-%H%M%S)"
OUT="$BASE/output/reviews/find-replace-pack-$STAMP.md"

python3 - <<'PY' "$SRC_TXT" "$REDLINE_MD" "$OUT"
import re,sys,pathlib
src_path,redline_path,out_path=sys.argv[1:4]
src=pathlib.Path(src_path).read_text(errors='ignore')
red=pathlib.Path(redline_path).read_text(errors='ignore')

items=[]
for m in re.finditer(r"##\s+\d+\.\s+([^\n]+)\n(?:.|\n)*?- Amendment to apply \(tracked\):\s*(.+)", red):
    clause=m.group(1).strip()
    amend=m.group(2).strip()
    items.append((clause,amend))

# Clause anchors for common NDA sections
anchors={
 'definition_of_confidential_information':[r'confidential information',r'vertrauliche information',r'INFORMATIONEN'],
 'exceptions':[r'public domain',r'allgemein bekannt',r'independently developed',r'unabhängig'],
 'term_and_survival':[r'survival',r'nachwirkung',r'term',r'dauer'],
 'use_restrictions':[r'sole purpose',r'zweck',r'only.*use',r'nur.*verwenden'],
 'return_or_destroy':[r'return',r'destroy',r'vernichten',r'zurückzugeben',r'löschen'],
 'governing_law_jurisdiction':[r'governing law',r'österreichischem recht',r'gericht',r'jurisdiction'],
 'assignment_and_affiliates':[r'assignment',r'abtret',r'affiliate',r'verbundene unternehmen'],
}

def find_anchor(clause,text):
    pats=anchors.get(clause,[])
    for p in pats:
        m=re.search(p,text,re.I)
        if m:
            s=max(0,m.start()-140)
            e=min(len(text),m.end()+220)
            return text[s:e].replace('\n',' ').strip()
    return ''

lines=[]
lines.append('# Step 5 — Find/Replace Execution Pack')
lines.append('')
lines.append(f'- Source text: `{src_path}`')
lines.append(f'- Redline source: `{redline_path}`')
lines.append('')
lines.append('Use with Word Track Changes enabled. For each item, locate the anchor text, then apply the amendment.')
lines.append('')
for i,(clause,amend) in enumerate(items,1):
    anchor=find_anchor(clause,src)
    lines.append(f'## {i}. {clause}')
    lines.append(f'- Find anchor (in document): "{anchor}"' if anchor else '- Find anchor (in document): [manually locate clause]')
    lines.append(f'- Replace/insert with: {amend}')
    lines.append('')

pathlib.Path(out_path).parent.mkdir(parents=True,exist_ok=True)
pathlib.Path(out_path).write_text('\n'.join(lines))
print(out_path)
PY

echo "Step 5 pack ready:"
echo "- $OUT"
