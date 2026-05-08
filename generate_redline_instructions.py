#!/usr/bin/env python3
import argparse
import re
from pathlib import Path


def parse_pack(md_text: str):
    sections = re.split(r"^###\s+", md_text, flags=re.M)
    items = []
    for s in sections[1:]:
        lines = s.splitlines()
        header = lines[0].strip()
        body = "\n".join(lines[1:])

        m = re.match(r"(\d+)\.\s+(.+)", header)
        if not m:
            continue
        point = int(m.group(1))
        clause = m.group(2).strip()

        sev = re.search(r"- Severity \(CLI\):\s*(.+)", body)
        concern = re.search(r"- Concern:\s*(.+)", body)
        proposed = re.search(r"- Proposed amendment \(CLI\):\s*(.+)", body)
        decision = re.search(r"- Pass 2 decision:\s*(.+)", body)
        final = re.search(r"- Final amendment text:\s*(.*)", body)

        decision_val = (decision.group(1).strip() if decision else "").upper()
        # Ignore template placeholders like: [CONFIRM / DOWNGRADE / DROP]
        if "CONFIRM" in decision_val and "DOWNGRADE" in decision_val and "DROP" in decision_val:
            decision_val = ""
        decision_val = decision_val.replace("[", "").replace("]", "").strip()

        # capture inline final amendment text, or multiline text following marker
        final_text = ""
        if final:
            inline = (final.group(1) or "").strip()
            if inline:
                final_text = inline
            else:
                after = body[final.end():].strip()
                final_text = after if after else ""

        items.append({
            "point": point,
            "clause": clause,
            "severity": sev.group(1).strip() if sev else "",
            "concern": concern.group(1).strip() if concern else "",
            "proposed": proposed.group(1).strip() if proposed else "",
            "decision": decision_val,
            "final_text": final_text,
        })
    return items


def build_instructions(items):
    approved = [i for i in items if i["decision"].startswith("CONFIRM") or i["decision"].startswith("DOWNGRADE")]
    lines = []
    lines.append("# Tracked-Redline Instruction Set")
    lines.append("")
    lines.append("Use this to apply tracked changes in Word. Only approved items are included.")
    lines.append("")
    if not approved:
        lines.append("No approved concerns found (CONFIRM/DOWNGRADE).")
        return "\n".join(lines)

    for i, a in enumerate(approved, 1):
        lines.append(f"## {i}. {a['clause']}")
        lines.append(f"- Source point: {a['point']}")
        lines.append(f"- Priority: {a['severity']}")
        lines.append(f"- Concern: {a['concern']}")
        amend = a["final_text"].strip() or a["proposed"]
        lines.append(f"- Amendment to apply (tracked): {amend}")
        lines.append("")

    lines.append("## Final pass checklist")
    lines.append("- Ensure defined terms remain consistent.")
    lines.append("- Ensure no numbering cross-reference breaks.")
    lines.append("- Keep edits minimal and clause-local.")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="Generate tracked-redline instruction set from reviewed hybrid approval pack")
    ap.add_argument("--pack", required=True, help="Path to hybrid approval pack markdown")
    ap.add_argument("--out", required=True, help="Output markdown path")
    args = ap.parse_args()

    pack = Path(args.pack)
    if not pack.exists():
        raise SystemExit(f"Pack not found: {pack}")

    items = parse_pack(pack.read_text())
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(build_instructions(items))
    print(out)


if __name__ == "__main__":
    main()
