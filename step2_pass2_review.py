#!/usr/bin/env python3
import argparse
import json
import re
from pathlib import Path

ITEM_RE = re.compile(
    r"###\s+(?P<point>\d+)\.\s+(?P<clause>[^\n]+)\n"
    r"- Severity \(CLI\):\s*(?P<severity>[^\n]*)\n"
    r"- Concern:\s*(?P<concern>[^\n]*)\n"
    r"- Proposed amendment \(CLI\):\s*(?P<proposed>[^\n]*)\n"
    r"- Pass 2 decision:\s*(?P<decision>[^\n]*)\n"
    r"- Final amendment text:\s*(?P<final>[\s\S]*?)(?=\n###\s+\d+\.|\Z)",
    re.M,
)

VALID = {"CONFIRM", "DOWNGRADE", "DROP", "SKIP"}


def parse_items(text: str):
    items = []
    for m in ITEM_RE.finditer(text):
        items.append({k: m.group(k).strip() for k in ["point", "clause", "severity", "concern", "proposed", "decision", "final"]})
    return items


def normalize_decision(raw: str) -> str:
    x = raw.strip().upper().replace("[", "").replace("]", "")
    if x in VALID:
        return x
    if "CONFIRM" in x and "/" in x:
        return ""
    return x


def apply_updates(text: str, updates: dict):
    def repl(m):
        point = m.group("point")
        if point not in updates:
            return m.group(0)
        u = updates[point]
        final = u.get("final", "").strip()
        block = (
            f"### {point}. {m.group('clause').strip()}\n"
            f"- Severity (CLI): {m.group('severity').strip()}\n"
            f"- Concern: {m.group('concern').strip()}\n"
            f"- Proposed amendment (CLI): {m.group('proposed').strip()}\n"
            f"- Pass 2 decision: {u.get('decision','').strip()}\n"
            f"- Final amendment text: {final}\n"
        )
        return block

    return ITEM_RE.sub(repl, text)


def run_interactive(items):
    updates = {}
    print("Pass 2 review loop: enter c=CONFIRM, d=DOWNGRADE, r=DROP, s=SKIP")
    for it in items:
        point = it["point"]
        print("\n" + "=" * 72)
        print(f"[{point}] {it['clause']}  (severity: {it['severity']})")
        print(f"Concern: {it['concern']}")
        print(f"Proposed: {it['proposed']}")
        current_decision = normalize_decision(it.get("decision", "")) or "(unset)"
        current_final = it.get("final", "").strip() or "(empty)"
        print(f"Current decision: {current_decision}")
        print(f"Current final text: {current_final}")

        raw = input("Decision [c/d/r/s]: ").strip().lower()
        if raw == "s" or raw == "":
            continue
        decision = {"c": "CONFIRM", "d": "DOWNGRADE", "r": "DROP"}.get(raw)
        if not decision:
            print("Invalid input, skipped.")
            continue

        final = ""
        if decision in {"CONFIRM", "DOWNGRADE"}:
            final = input("Final amendment text (Enter = keep proposed): ").strip() or it["proposed"]
        updates[point] = {"decision": decision, "final": final}
    return updates


def run_from_json(items, decisions):
    by_point = {str(d["point"]): d for d in decisions}
    updates = {}
    for it in items:
        p = it["point"]
        if p not in by_point:
            continue
        d = normalize_decision(by_point[p].get("decision", ""))
        if d not in {"CONFIRM", "DOWNGRADE", "DROP"}:
            continue
        final = by_point[p].get("final", "")
        if d in {"CONFIRM", "DOWNGRADE"} and not final:
            final = it["proposed"]
        updates[p] = {"decision": d, "final": final}
    return updates


def run_default_recommendations(items):
    """
    Apply default pass-2 decisions without prompting.
    Heuristic:
      - high severity -> CONFIRM
      - low severity  -> DOWNGRADE
    Final amendment text defaults to the proposed amendment text.
    """
    updates = {}
    for it in items:
        sev = (it.get("severity") or "").strip().lower()
        decision = "CONFIRM" if sev == "high" else "DOWNGRADE"
        updates[it["point"]] = {
            "decision": decision,
            "final": it.get("proposed", "").strip(),
        }
    return updates


def main():
    ap = argparse.ArgumentParser(description="Step 2 Pass-2 loop over hybrid approval pack")
    ap.add_argument("--pack", required=True, help="Path to hybrid-approval-pack-*.md")
    ap.add_argument("--decisions-json", help='Optional JSON file: [{"point":"1","decision":"CONFIRM|DOWNGRADE|DROP","final":"..."}]')
    ap.add_argument("--mode", choices=["interactive", "defaults"], default="interactive", help="interactive=review one-by-one, defaults=apply recommended defaults automatically")
    ap.add_argument("--out", help="Output pack path (default overwrite input)")
    ap.add_argument("--export-json", help="Write applied decisions to JSON")
    args = ap.parse_args()

    pack = Path(args.pack)
    text = pack.read_text()
    items = parse_items(text)
    if not items:
        raise SystemExit("No pass-2 items found in pack.")

    if args.decisions_json:
        decisions = json.loads(Path(args.decisions_json).read_text())
        updates = run_from_json(items, decisions)
    elif args.mode == "defaults":
        updates = run_default_recommendations(items)
    else:
        updates = run_interactive(items)

    if not updates:
        print("No updates applied.")
        return

    out_text = apply_updates(text, updates)
    out_path = Path(args.out) if args.out else pack
    out_path.write_text(out_text)
    print(f"Updated pack: {out_path}")

    if args.export_json:
        payload = [{"point": p, **v} for p, v in sorted(updates.items(), key=lambda kv: int(kv[0]))]
        Path(args.export_json).write_text(json.dumps(payload, indent=2, ensure_ascii=False))
        print(f"Exported decisions: {args.export_json}")


if __name__ == "__main__":
    main()
