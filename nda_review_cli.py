#!/usr/bin/env python3
import argparse
import json
import re
from pathlib import Path
from collections import Counter, defaultdict

NDA_PAT = re.compile(r"\b(nda|non[-\s]?disclosure|confidentiality agreement|confidential disclosure agreement|cda|mutual nda|geheimhaltungsvereinbarung|vertraulich|vertrauliche informationen)\b", re.I)

CLAUSE_RULES = {
    "mutuality": {
        "keywords": [r"unilateral", r"mutual", r"both parties", r"disclosing party", r"receiving party", r"vertragspartner", r"überlasser", r"empfänger"],
        "preferred": "Mutual NDA preferred; avoid one-sided obligations.",
        "red_flags": ["unilateral obligations", "only one party bound"],
    },
    "definition_of_confidential_information": {
        "keywords": [r"confidential information", r"marked confidential", r"oral disclosure", r"written disclosure", r"vertrauliche information", r"gekennzeichnet", r"mündlich", r"schriftlich"],
        "preferred": "Broad but clear definition with practical marking/confirmation rules.",
        "red_flags": ["overly vague definition", "no objective boundary"],
    },
    "exceptions": {
        "keywords": [r"public domain", r"already known", r"independently developed", r"rightfully received", r"required by law", r"allgemein bekannt", r"bereits vorher", r"unabhängig.*entwickelt", r"behördlichen.*anordnung", r"zwingender rechtlicher"],
        "preferred": "Keep standard carve-outs: public, prior knowledge, independent development, third-party lawful receipt, legal compulsion.",
        "red_flags": ["missing standard carve-outs", "narrow legal disclosure carve-out"],
    },
    "term_and_survival": {
        "keywords": [r"term", r"survival", r"(\d+) years", r"perpetual", r"indefinite", r"dauer", r"nachwirkung", r"jahre", r"geschäftsgeheimnisse"],
        "preferred": "Finite confidentiality survival (commonly 2–5 years) unless trade-secret carve-out.",
        "red_flags": ["perpetual for all info", "unclear survival"],
    },
    "use_restrictions": {
        "keywords": [r"sole purpose", r"evaluate", r"business relationship", r"purpose", r"zweck", r"nur.*verwenden"],
        "preferred": "Use limited to evaluating/performing the business relationship.",
        "red_flags": ["too broad use license", "purpose not defined"],
    },
    "return_or_destroy": {
        "keywords": [r"return", r"destroy", r"certify destruction", r"retention", r"zurückzugeben", r"vernichten", r"löschen", r"backup", r"archiv"],
        "preferred": "Return/destroy on request with limited backup/legal retention carve-out.",
        "red_flags": ["immediate purge without backup carve-out", "no return/destroy right"],
    },
    "residuals": {
        "keywords": [r"residual", r"unaided memory", r"gedächtnis"],
        "preferred": "Avoid broad residuals clauses; if present, tightly limit scope.",
        "red_flags": ["broad residuals rights"],
    },
    "non_solicit_non_compete": {
        "keywords": [r"non-solicit", r"non solicit", r"non-compete", r"non compete", r"hire", r"abwerb", r"wettbewerb"],
        "preferred": "NDA should avoid hidden non-compete/non-solicit obligations unless explicitly negotiated.",
        "red_flags": ["embedded non-compete", "overbroad non-solicit"],
    },
    "governing_law_jurisdiction": {
        "keywords": [r"governing law", r"jurisdiction", r"venue", r"courts of", r"österreichischem recht", r"zuständige gericht", r"ausschließlich"],
        "preferred": "Prefer neutral/favorable jurisdiction; avoid hard-to-enforce foreign venues where possible.",
        "red_flags": ["exclusive unfavorable venue"],
    },
    "liability_and_remedies": {
        "keywords": [r"injunctive", r"equitable relief", r"damages", r"liability", r"indemn", r"haft", r"gewährleistung", r"istzustand"],
        "preferred": "Allow injunctive relief but avoid unlimited liability expansion hidden in NDA.",
        "red_flags": ["uncapped indemnity in NDA", "asymmetric remedies"],
    },
    "assignment_and_affiliates": {
        "keywords": [r"assignment", r"affiliate", r"successors", r"verbundene unternehmen", r"abtret"],
        "preferred": "Assignment by consent, with affiliate sharing allowed under same obligations when needed.",
        "red_flags": ["free assignment to unknown third parties"],
    },
}

NEGOTIATION_SIGNAL_PATTERNS = {
    "pushback": [r"cannot accept", r"not acceptable", r"we cannot", r"please revise", r"redline", r"counter"],
    "acceptance": [r"looks good", r"agreed", r"approved", r"works for us", r"signed"],
    "risk": [r"liability", r"indemn", r"unlimited", r"perpetual", r"injunctive", r"exclusive jurisdiction"],
}


def load_messages(paths):
    out = []
    for p in paths:
        pp = Path(p)
        if not pp.exists():
            continue
        try:
            data = json.loads(pp.read_text())
        except Exception:
            continue
        if isinstance(data, list):
            out.extend(data)
    return out


def msg_text(msg):
    return "\n".join([
        msg.get("subject", ""),
        msg.get("body", ""),
        msg.get("from", ""),
    ])


def filter_nda(messages):
    seen = set()
    filtered = []
    for m in messages:
        mid = m.get("id") or f"{m.get('threadId','')}-{m.get('date','')}"
        if mid in seen:
            continue
        txt = msg_text(m)
        if NDA_PAT.search(txt):
            seen.add(mid)
            filtered.append(m)
    return filtered


def extract_sentences(text):
    text = re.sub(r"\s+", " ", text)
    return re.split(r"(?<=[.!?])\s+", text)


def extract_clause_snippet(text, keywords, window=240):
    for kw in keywords:
        m = re.search(kw, text, re.I)
        if m:
            start = max(0, m.start() - window)
            end = min(len(text), m.end() + window)
            snippet = text[start:end].strip()
            snippet = re.sub(r"\s+", " ", snippet)
            return snippet
    return ""


def derive_context_and_recommendation(clause, snippet, preferred_position):
    context = f"Clause '{clause}' was detected in the agreement text and should be reviewed against Medicus preferred position."
    if snippet:
        context = f"Detected clause text indicates '{clause}' is present. Validate whether this exact wording aligns with Medicus standards."
    recommendation = f"Align this clause with Medicus position: {preferred_position}"
    return context, recommendation


def build_playbook(messages, drive_items):
    clause_counts = Counter()
    evidence = defaultdict(list)
    signal_counts = Counter()

    for m in messages:
        txt = msg_text(m)
        ltxt = txt.lower()

        for sig, pats in NEGOTIATION_SIGNAL_PATTERNS.items():
            if any(re.search(p, ltxt, re.I) for p in pats):
                signal_counts[sig] += 1

        sents = extract_sentences(txt)
        for clause, cfg in CLAUSE_RULES.items():
            if any(re.search(k, ltxt, re.I) for k in cfg["keywords"]):
                clause_counts[clause] += 1
                if len(evidence[clause]) < 8:
                    for s in sents:
                        if any(re.search(k, s, re.I) for k in cfg["keywords"]):
                            s_clean = s.strip()
                            if s_clean and s_clean not in evidence[clause]:
                                evidence[clause].append(s_clean[:300])
                                break

    total = len(messages)
    top_clauses = clause_counts.most_common()

    playbook = {
        "version": "0.1.0",
        "source_summary": {
            "nda_emails_analyzed": total,
            "drive_docs_flagged": len(drive_items),
        },
        "negotiation_signals": dict(signal_counts),
        "clause_frequency": [{"clause": c, "mentions": n} for c, n in top_clauses],
        "policy": [],
        "review_decision_logic": {
            "block_if": [
                "broad residuals rights",
                "embedded non-compete/non-solicit",
                "uncapped indemnity or unlimited liability expansion",
                "missing standard confidentiality exceptions",
            ],
            "escalate_if": [
                "exclusive unfavorable jurisdiction",
                "perpetual confidentiality without trade-secret scoping",
                "one-sided unilateral NDA when mutual expected",
            ],
            "approve_if": [
                "mutual obligations",
                "standard carve-outs present",
                "clear purpose limitation",
                "reasonable term/survival",
            ],
        },
    }

    for clause, cfg in CLAUSE_RULES.items():
        playbook["policy"].append({
            "clause": clause,
            "preferred_position": cfg["preferred"],
            "red_flags": cfg["red_flags"],
            "keywords": cfg["keywords"],
            "mentions": clause_counts.get(clause, 0),
            "evidence_examples": evidence.get(clause, []),
        })

    return playbook


def review_text(text, playbook):
    findings = []
    risk_score = 0
    ltxt = text.lower()

    for rule in playbook.get("policy", []):
        clause = rule["clause"]
        keywords = rule.get("keywords", [])
        hit = any(re.search(k, ltxt, re.I) for k in keywords)
        if not hit:
            continue

        rf_hits = [rf for rf in rule.get("red_flags", []) if re.search(re.escape(rf.split()[0]), ltxt, re.I)]
        severity = "low"
        if rf_hits:
            severity = "high"
            risk_score += 3
        else:
            risk_score += 1

        snippet = extract_clause_snippet(text, keywords)
        context, recommendation = derive_context_and_recommendation(
            clause,
            snippet,
            rule.get("preferred_position", "")
        )

        findings.append({
            "clause": clause,
            "severity": severity,
            "preferred_position": rule.get("preferred_position"),
            "red_flags": rule.get("red_flags"),
            "clause_snippet": snippet,
            "context": context,
            "recommendation": recommendation,
            "recommended_amendment": f"Amend clause '{clause}' to align with: {rule.get('preferred_position','')}",
        })

    decision = "approve"
    if risk_score >= 10:
        decision = "block"
    elif risk_score >= 5:
        decision = "escalate"

    return {
        "decision": decision,
        "risk_score": risk_score,
        "findings": findings,
        "concerns_summary": [
            {
                "point": i + 1,
                "clause": f.get("clause"),
                "severity": f.get("severity"),
                "concern": ", ".join(f.get("red_flags") or []) or "Clause deviates from preferred Medicus position.",
                "clause_snippet": f.get("clause_snippet", ""),
                "context": f.get("context", ""),
                "recommendation": f.get("recommendation", ""),
                "recommended_amendment": f.get("recommended_amendment"),
            }
            for i, f in enumerate(findings)
        ],
    }


def cmd_build(args):
    base = Path(args.base)
    gmail_paths = [
        base / "data/raw_strict/gmail_baher_strict.json",
        base / "data/raw_strict/gmail_personal_strict.json",
    ]
    drive_paths = [
        base / "data/raw_strict/drive_baher_strict.json",
        base / "data/raw_strict/drive_personal_strict.json",
    ]

    messages = filter_nda(load_messages(gmail_paths))
    drive_items = load_messages(drive_paths)

    playbook = build_playbook(messages, drive_items)

    out_json = base / "output/medicus_nda_playbook.json"
    out_md = base / "output/medicus_nda_playbook.md"
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(playbook, indent=2, ensure_ascii=False))

    md = [
        "# Medicus NDA Playbook",
        "",
        f"- NDA emails analyzed: **{playbook['source_summary']['nda_emails_analyzed']}**",
        f"- Drive docs flagged: **{playbook['source_summary']['drive_docs_flagged']}**",
        "",
        "## Decision Logic",
        "",
        "### Block if",
    ]
    md += [f"- {x}" for x in playbook["review_decision_logic"]["block_if"]]
    md += ["", "### Escalate if"]
    md += [f"- {x}" for x in playbook["review_decision_logic"]["escalate_if"]]
    md += ["", "### Approve if"]
    md += [f"- {x}" for x in playbook["review_decision_logic"]["approve_if"]]
    md += ["", "## Clause Positions", ""]

    for p in playbook["policy"]:
        md += [f"### {p['clause']}", f"- Preferred: {p['preferred_position']}", f"- Mentions: {p['mentions']}"]
        if p["red_flags"]:
            md += ["- Red flags:"] + [f"  - {r}" for r in p["red_flags"]]
        if p["evidence_examples"]:
            md += ["- Evidence snippets:"] + [f"  - \"{e}\"" for e in p["evidence_examples"][:3]]
        md += [""]

    out_md.write_text("\n".join(md))
    print(json.dumps({"ok": True, "playbook_json": str(out_json), "playbook_md": str(out_md)}, indent=2))


def cmd_review(args):
    playbook = json.loads(Path(args.playbook).read_text())
    if args.file:
        text = Path(args.file).read_text(errors="ignore")
    else:
        text = args.text or ""
    result = review_text(text, playbook)
    if args.file:
        result["input_file"] = args.file

    if args.out_json:
        out_json = Path(args.out_json)
        out_json.parent.mkdir(parents=True, exist_ok=True)
        out_json.write_text(json.dumps(result, indent=2, ensure_ascii=False))

    if args.out_md:
        out_md = Path(args.out_md)
        out_md.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            "# NDA Review Result",
            "",
            f"- Decision: **{result.get('decision','unknown').upper()}**",
            f"- Risk score: **{result.get('risk_score',0)}**",
        ]
        if result.get("input_file"):
            lines.append(f"- Input file: `{result['input_file']}`")
        lines += ["", "## Findings", ""]
        for f in result.get("findings", []):
            lines.append(f"### {f.get('clause','unknown')}")
            lines.append(f"- Severity: {f.get('severity','unknown')}")
            lines.append(f"- Preferred position: {f.get('preferred_position','')}")
            if f.get("clause_snippet"):
                lines.append(f"- Exact clause snippet: \"{f.get('clause_snippet')}\"")
            if f.get("context"):
                lines.append(f"- Context: {f.get('context')}")
            if f.get("recommendation"):
                lines.append(f"- Recommendation: {f.get('recommendation')}")
            if f.get("red_flags"):
                lines.append("- Red flags:")
                lines.extend([f"  - {r}" for r in f["red_flags"]])
            lines.append("")

        lines += ["", "## Concerns & Recommended Amendments (for approval)", ""]
        for c in result.get("concerns_summary", []):
            lines.append(f"### {c.get('point')}. {c.get('clause')}")
            lines.append(f"- Severity: {c.get('severity')}")
            if c.get("clause_snippet"):
                lines.append(f"- Exact clause snippet: \"{c.get('clause_snippet')}\"")
            if c.get("context"):
                lines.append(f"- Context: {c.get('context')}")
            lines.append(f"- Concern: {c.get('concern')}")
            if c.get("recommendation"):
                lines.append(f"- Recommendation: {c.get('recommendation')}")
            lines.append(f"- Recommended amendment: {c.get('recommended_amendment')}")
            lines.append("")
        out_md.write_text("\n".join(lines))

    print(json.dumps(result, indent=2, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description="Medicus NDA Review CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_build = sub.add_parser("build-playbook", help="Build NDA playbook from extracted Gmail/Drive corpus")
    p_build.add_argument("--base", default=str(Path(__file__).resolve().parent))
    p_build.set_defaults(func=cmd_build)

    p_review = sub.add_parser("review", help="Review NDA text against generated playbook")
    p_review.add_argument("--playbook", default=str(Path(__file__).resolve().parent / "output/medicus_nda_playbook.json"))
    p_review.add_argument("--file")
    p_review.add_argument("--text")
    p_review.add_argument("--out-json")
    p_review.add_argument("--out-md")
    p_review.set_defaults(func=cmd_review)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
