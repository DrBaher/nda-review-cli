#!/usr/bin/env python3
import argparse
import json
import re
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from collections import Counter, defaultdict
import difflib
from typing import Optional
from rule_engine import clause_hit, red_flag_hits

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


def extract_clause_snippet(text, keywords, window=260):
    """
    Return a cleaner clause snippet anchored to sentence/paragraph boundaries
    instead of raw character windows.
    """

    def clean(s):
        s = s.replace("\u2028", " ").replace("\u2029", " ")
        s = re.sub(r"\s+", " ", s)
        return s.strip()

    # Split by paragraph-ish blocks first to preserve legal structure.
    blocks = [b for b in re.split(r"\n\s*\n", text) if b.strip()]

    for kw in keywords:
        rx = re.compile(kw, re.I)

        # 1) Prefer exact paragraph block containing keyword.
        for block in blocks:
            if rx.search(block):
                snippet = clean(block)
                if len(snippet) <= 700:
                    return snippet

                # 2) If block is huge, trim to sentence neighborhood.
                m = rx.search(block)
                if not m:
                    continue
                local = block
                # sentence boundaries around keyword hit
                left = max(local.rfind('.', 0, m.start()), local.rfind(';', 0, m.start()), local.rfind('\n', 0, m.start()))
                right_candidates = [
                    local.find('.', m.end()),
                    local.find(';', m.end()),
                    local.find('\n', m.end()),
                ]
                right_candidates = [c for c in right_candidates if c != -1]
                right = min(right_candidates) if right_candidates else len(local)
                start = max(0, (left + 1) if left != -1 else m.start() - window)
                end = min(len(local), right + 1 if right != -1 else m.end() + window)
                return clean(local[start:end])

        # 3) Fallback to global sentence window
        m = rx.search(text)
        if m:
            start = max(0, m.start() - window)
            end = min(len(text), m.end() + window)
            raw = text[start:end]
            # expand to nearest sentence delimiters in raw window
            lcut = max(raw.find('. '), raw.find('\n'))
            if lcut > 0:
                raw = raw[lcut + 1:]
            return clean(raw)

    return ""


def locate_clause(text, keywords):
    lines = text.splitlines()
    heading = ""
    para_idx = None
    for i, ln in enumerate(lines):
        line = ln.strip()
        if not line:
            continue
        is_heading = (line.startswith("•") or line.isupper() or (len(line) < 80 and line.endswith(":")))
        for kw in keywords:
            if re.search(kw, line, re.I):
                # find nearest heading above
                j = i
                while j >= 0:
                    cand = lines[j].strip()
                    if cand and (cand.startswith("•") or cand.isupper() or (len(cand) < 80 and cand.endswith(":"))):
                        heading = cand
                        break
                    j -= 1
                para_idx = i + 1
                return heading, para_idx
        if is_heading and not heading:
            heading = line
    return heading, para_idx


def derive_context_and_recommendation(clause, snippet, preferred_position):
    context = f"Clause '{clause}' was detected in the agreement text and should be reviewed against Medicus preferred position."
    if snippet:
        context = f"Detected clause text indicates '{clause}' is present. Validate whether this exact wording aligns with Medicus standards."
    recommendation = f"Align this clause with Medicus position: {preferred_position}"
    return context, recommendation


def load_counterparty_profile(base: Path, counterparty: Optional[str]):
    if not counterparty:
        return {}
    p = base / "profiles" / f"{counterparty.lower().replace(' ', '_')}.json"
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            return {}
    return {}


def risk_weights(profile: Optional[dict] = None):
    w = {
        "legal": 1.0,
        "commercial": 1.0,
        "operational": 1.0,
        "severity_high": 3,
        "severity_low": 1,
    }
    if profile and isinstance(profile.get("risk_weights"), dict):
        w.update(profile["risk_weights"])
    return w


def classify_risk_bucket(clause):
    if clause in {"liability_and_remedies", "exceptions", "term_and_survival", "definition_of_confidential_information"}:
        return "legal"
    if clause in {"governing_law_jurisdiction", "mutuality", "assignment_and_affiliates", "non_solicit_non_compete"}:
        return "commercial"
    return "operational"


def rule_hit_details(text, keywords):
    hits = []
    for k in keywords:
        m = re.search(k, text, re.I)
        if not m:
            continue
        matched_text = text[m.start():m.end()]
        hits.append({"pattern": k, "match": matched_text})
    return hits


def sha256_file(path: Path):
    if not path.exists() or not path.is_file():
        return None
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def suggest_posture(clause, profile):
    if not profile:
        return "Use Medicus default position."
    prefs = profile.get("clause_preferences", {})
    if clause in prefs:
        return f"Counterparty posture: {prefs[clause]}"
    fallback = profile.get("fallback_posture")
    if fallback:
        return f"Counterparty fallback posture: {fallback}"
    return "Use Medicus default position."


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


def review_text(text, playbook, profile=None):
    findings = []
    risk_score = 0
    breakdown = {"legal": 0.0, "commercial": 0.0, "operational": 0.0}
    weights = risk_weights(profile)
    ltxt = text.lower()

    for rule in playbook.get("policy", []):
        clause = rule["clause"]
        keywords = rule.get("keywords", [])
        hit, keyword_hits = clause_hit(ltxt, keywords)
        if not hit:
            continue

        rf_trigger_hits = red_flag_hits(clause, ltxt)
        rf_hits = [rf for rf in rule.get("red_flags", []) if rf_trigger_hits]
        severity = "low"
        if rf_hits:
            severity = "high"
            score = weights["severity_high"]
        else:
            score = weights["severity_low"]

        bucket = classify_risk_bucket(clause)
        weighted = score * float(weights.get(bucket, 1.0))
        risk_score += weighted
        breakdown[bucket] += weighted

        snippet = extract_clause_snippet(text, keywords)
        clause_heading, paragraph_index = locate_clause(text, keywords)
        context, recommendation = derive_context_and_recommendation(
            clause,
            snippet,
            rule.get("preferred_position", "")
        )

        hit_details = rule_hit_details(text, keywords)

        findings.append({
            "clause": clause,
            "severity": severity,
            "preferred_position": rule.get("preferred_position"),
            "red_flags": rule.get("red_flags"),
            "clause_snippet": snippet,
            "context": context,
            "recommendation": recommendation,
            "recommended_amendment": f"Amend clause '{clause}' to align with: {rule.get('preferred_position','')}",
            "rule_hits": keyword_hits,
            "rule_hit_details": hit_details,
            "red_flag_trigger_hits": rf_trigger_hits,
            "risk_bucket": bucket,
            "score": weighted,
            "clause_heading": clause_heading,
            "paragraph_index": paragraph_index,
            "posture_suggestion": suggest_posture(clause, profile),
        })

    decision = "approve"
    if risk_score >= 10:
        decision = "block"
    elif risk_score >= 5:
        decision = "escalate"

    return {
        "decision": decision,
        "risk_score": round(risk_score, 2),
        "risk_breakdown": {k: round(v, 2) for k, v in breakdown.items()},
        "risk_weights": weights,
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
                "rule_hits": f.get("rule_hits", []),
                "rule_hit_details": f.get("rule_hit_details", []),
                "red_flag_trigger_hits": f.get("red_flag_trigger_hits", []),
                "risk_bucket": f.get("risk_bucket", ""),
                "score": f.get("score", 0),
                "clause_heading": f.get("clause_heading", ""),
                "paragraph_index": f.get("paragraph_index"),
                "posture_suggestion": f.get("posture_suggestion", ""),
            }
            for i, f in enumerate(findings)
        ],
    }


def cmd_playbook_lock(args):
    base = Path(args.base)
    playbook = json.loads(Path(args.playbook).read_text())
    counterparty = args.counterparty.strip()
    lock_dir = base / "output" / "playbook_locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    safe = counterparty.lower().replace(" ", "_")
    out = lock_dir / f"{safe}.json"
    payload = {
        "counterparty": counterparty,
        "locked_at": datetime.now(timezone.utc).isoformat(),
        "playbook": playbook,
    }
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    print(out)


def cmd_playbook_snapshot(args):
    base = Path(args.base)
    playbook = json.loads(Path(args.playbook).read_text())
    snap_dir = base / "output" / "playbook_versions"
    snap_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    out = snap_dir / f"playbook-{ts}.json"
    playbook["snapshot_created_at"] = datetime.now(timezone.utc).isoformat()
    out.write_text(json.dumps(playbook, indent=2, ensure_ascii=False))
    print(out)


def cmd_playbook_diff(args):
    a = json.loads(Path(args.a).read_text()).get("policy", [])
    b = json.loads(Path(args.b).read_text()).get("policy", [])
    ta = json.dumps(a, indent=2, ensure_ascii=False).splitlines()
    tb = json.dumps(b, indent=2, ensure_ascii=False).splitlines()
    diff = "\n".join(difflib.unified_diff(ta, tb, fromfile=args.a, tofile=args.b, lineterm=""))
    if args.out:
        Path(args.out).write_text(diff)
    print(diff)


def cmd_generate_redlines(args):
    review = json.loads(Path(args.review_json).read_text())
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# Clause-ready Redline Draft", ""]
    for c in review.get("concerns_summary", []):
        decision = str(c.get("pass2_decision", "")).upper()
        if decision and decision not in {"CONFIRM", "DOWNGRADE"}:
            continue
        lines.append(f"## {c.get('point')}. {c.get('clause')}")
        lines.append(f"- Replace this snippet: \"{c.get('clause_snippet','')}\"")
        lines.append(f"- Concern: {c.get('concern')}")
        lines.append(f"- Recommendation: {c.get('recommendation')}")
        lines.append(f"- Strict replacement: {c.get('recommended_amendment')}")
        lines.append(f"- Moderate replacement: {c.get('recommended_amendment')}")
        lines.append(f"- Soft fallback replacement: Consider adding a clarifying qualifier while preserving business intent.")
        lines.append("")
    out.write_text("\n".join(lines))
    print(out)


def cmd_generate_office_script(args):
    pack = Path(args.find_replace_pack).read_text(errors="ignore")
    entries = re.findall(r"##\s+\d+\.\s+([^\n]+)\n- Find anchor \(in document\):\s*\"?([^\n\"]*)\"?\n- Replace/insert with:\s*([^\n]+)", pack)
    body = [
        "' Auto-generated Word VBA macro skeleton from Step 5 pack",
        "Sub ApplyNdaTrackedEdits()",
        "    ' Enable Track Changes in Word before running",
        "    If ActiveDocument Is Nothing Then Exit Sub",
        "    ActiveDocument.TrackRevisions = True",
        "",
    ]
    for i, (_, find, repl) in enumerate(entries, 1):
        esc_find = find.replace('"', '""')
        esc_repl = repl.replace('"', '""')
        body.append(f"    ' {i}")
        body.append("    With Selection.Find")
        body.append(f"        .Text = \"{esc_find}\"")
        body.append("        .Forward = True")
        body.append("        .Wrap = wdFindStop")
        body.append("    End With")
        body.append("    If Selection.Find.Execute Then")
        body.append(f"        Selection.TypeText Text:=\"{esc_repl}\"")
        body.append("    End If")
        body.append("")
    body.append("End Sub")
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(body))
    print(out)


def cmd_quality_gate(args):
    redline = Path(args.redline).read_text(errors="ignore")
    problems = []

    # Numbering continuity in redline instruction headings
    points = [int(x) for x in re.findall(r"^##\s+(\d+)\.", redline, re.M)]
    if points:
        expected = list(range(1, len(points) + 1))
        if points != expected:
            problems.append(f"Numbering mismatch: got {points}, expected {expected}")

    # Amendment text present
    missing = re.findall(r"^##\s+\d+\..*?(?:\n(?!## ).*)*- Amendment to apply \(tracked\):\s*$", redline, re.M)
    if missing:
        problems.append("One or more redline points have empty amendment text.")

    # Optional source checks
    if args.source_text and Path(args.source_text).exists():
        src = Path(args.source_text).read_text(errors="ignore")
        src_lower = src.lower()
        if re.search(r"allow|permit.*ai|training", src_lower, re.I) and re.search(r"prohibit|forbid|shall not.*ai", src_lower, re.I):
            problems.append("Potential AI usage contradiction detected in source text.")
        refs = re.findall(r"section\s+(\d+(?:\.\d+)*)", redline, re.I)
        for r in refs[:10]:
            if not re.search(rf"section\s+{re.escape(r)}\b", src, re.I):
                problems.append(f"Cross-reference check: Section {r} referenced in redline but not found in source text.")

    status = "ok" if not problems else "fail"
    payload = {"status": status, "problems": problems}
    if args.out_json:
        p = Path(args.out_json)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    print(json.dumps(payload, ensure_ascii=False))
    if problems:
        raise SystemExit(2)


def cmd_create_manifest(args):
    base = Path(args.base)
    files = [Path(p) for p in args.files]
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    artifacts = []
    for fp in files:
        resolved = fp if fp.is_absolute() else (base / fp)
        artifacts.append({
            "path": str(fp),
            "resolved_path": str(resolved),
            "exists": resolved.exists(),
            "sha256": sha256_file(resolved),
        })

    manifest = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "counterparty": args.counterparty,
        "playbook": args.playbook,
        "profile": args.profile,
        "decisions_source": args.decisions_source,
        "artifacts": artifacts,
    }
    out.write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
    print(out)


def _prompt_with_default(label, default):
    raw = input(f"{label} [{default}]: ").strip()
    return raw or default


def _prompt_choice(label, choices, default):
    options = "/".join(choices)
    while True:
        raw = input(f"{label} ({options}) [{default}]: ").strip().lower()
        val = raw or default
        if val in choices:
            return val
        print(f"Invalid choice: {val}")


def _parse_csv(raw):
    return [x.strip() for x in raw.split(",") if x.strip()]


def cmd_init(args):
    base = Path(args.base)
    cfg_dir = base / "config"
    prof_dir = base / "profiles"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    prof_dir.mkdir(parents=True, exist_ok=True)

    if args.interactive:
        org_name = _prompt_with_default("Organization name", args.org_name or "Your Org")
        posture = _prompt_choice("Risk posture", ["strict", "balanced", "commercial"], args.risk_posture)
        jurisdiction = _prompt_with_default("Preferred jurisdictions (comma-separated)", args.preferred_jurisdictions)
        survival_years = int(_prompt_with_default("Default confidentiality survival years", str(args.survival_years)))
        ai_usage = _prompt_choice("AI policy", ["restricted", "guardrailed", "permissive"], args.ai_policy)
        retention = _prompt_with_default("Default retention carve-out", args.retention_carveout)
    else:
        org_name = args.org_name or "Your Org"
        posture = args.risk_posture
        jurisdiction = args.preferred_jurisdictions
        survival_years = args.survival_years
        ai_usage = args.ai_policy
        retention = args.retention_carveout

    if posture == "strict":
        weights = {"legal": 1.4, "commercial": 1.0, "operational": 1.2, "severity_high": 4, "severity_low": 1}
    elif posture == "commercial":
        weights = {"legal": 1.0, "commercial": 0.9, "operational": 1.0, "severity_high": 3, "severity_low": 1}
    else:
        weights = {"legal": 1.2, "commercial": 1.0, "operational": 1.0, "severity_high": 3, "severity_low": 1}

    org_policy = {
        "version": "0.2.0",
        "org_name": org_name,
        "risk_posture": posture,
        "preferred_jurisdictions": _parse_csv(jurisdiction),
        "defaults": {
            "survival_years": survival_years,
            "ai_policy": ai_usage,
            "retention_carveout": retention,
        },
        "risk_weights": weights,
    }

    profile = {
        "profile_name": "default",
        "fallback_posture": f"{org_name} prefers {posture} posture.",
        "risk_weights": weights,
        "clause_preferences": {
            "term_and_survival": f"Finite survival ({survival_years} years) unless trade secret/legal carve-out.",
            "governing_law_jurisdiction": f"Prefer jurisdictions: {', '.join(_parse_csv(jurisdiction)) or 'neutral/favorable'}.",
            "return_or_destroy": retention,
        },
    }

    org_out = cfg_dir / "org-policy.json"
    prof_out = prof_dir / "default.json"
    org_out.write_text(json.dumps(org_policy, indent=2, ensure_ascii=False))
    prof_out.write_text(json.dumps(profile, indent=2, ensure_ascii=False))
    print(json.dumps({"ok": True, "org_policy": str(org_out), "default_profile": str(prof_out)}, ensure_ascii=False))


def _read_any_text(path: Path):
    if path.suffix.lower() == ".docx":
        # lightweight fallback: rely on caller to provide txt when possible
        return ""
    return path.read_text(errors="ignore")


def cmd_ingest(args):
    base = Path(args.base)
    kdir = base / "knowledge"
    kdir.mkdir(parents=True, exist_ok=True)
    proposed_dir = kdir / "proposed"
    proposed_dir.mkdir(parents=True, exist_ok=True)

    paths = [Path(p) for p in args.files]
    sources = []
    aggregate = {k: {"hits": 0, "examples": []} for k in CLAUSE_RULES.keys()}

    for p in paths:
        rp = p if p.is_absolute() else (base / p)
        if not rp.exists():
            continue
        text = _read_any_text(rp)
        ltxt = text.lower()
        matched = []
        for clause, cfg in CLAUSE_RULES.items():
            hit, pats = clause_hit(ltxt, cfg.get("keywords", []))
            if not hit:
                continue
            matched.append(clause)
            aggregate[clause]["hits"] += 1
            if len(aggregate[clause]["examples"]) < 3:
                aggregate[clause]["examples"].append(pats[0])
        sources.append({"path": str(rp), "matched_clauses": matched, "sha256": sha256_file(rp)})

    suggestions = []
    for clause, data in aggregate.items():
        if data["hits"] == 0:
            continue
        suggestions.append({
            "clause": clause,
            "proposed_preference": CLAUSE_RULES[clause]["preferred"],
            "confidence": "high" if data["hits"] >= 3 else "medium",
            "seen_count": data["hits"],
            "evidence": data["examples"],
            "status": "proposed",
        })

    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    out = proposed_dir / f"ingest-suggestions-{ts}.json"
    payload = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "sources": sources,
        "suggestions": suggestions,
        "note": "Proposed-only. Review before promotion to active policy/profile.",
    }
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    print(json.dumps({"ok": True, "suggestions_file": str(out), "sources_ingested": len(sources)}, ensure_ascii=False))


def cmd_setup(args):
    # Combined flow: init + optional ingest.
    class Obj:
        pass

    init_args = Obj()
    init_args.base = args.base
    init_args.interactive = args.interactive
    init_args.org_name = args.org_name
    init_args.risk_posture = args.risk_posture
    init_args.preferred_jurisdictions = args.preferred_jurisdictions
    init_args.survival_years = args.survival_years
    init_args.ai_policy = args.ai_policy
    init_args.retention_carveout = args.retention_carveout
    cmd_init(init_args)

    if args.ingest_files:
        ingest_args = Obj()
        ingest_args.base = args.base
        ingest_args.files = args.ingest_files
        cmd_ingest(ingest_args)


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
    base = Path(args.base)
    profile = load_counterparty_profile(base, args.counterparty)
    if args.file:
        text = Path(args.file).read_text(errors="ignore")
    else:
        text = args.text or ""
    result = review_text(text, playbook, profile=profile)
    if args.file:
        result["input_file"] = args.file
    if args.counterparty:
        result["counterparty"] = args.counterparty
    if profile:
        result["counterparty_profile_loaded"] = True

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
            f"- Risk breakdown: legal={result.get('risk_breakdown',{}).get('legal',0)}, commercial={result.get('risk_breakdown',{}).get('commercial',0)}, operational={result.get('risk_breakdown',{}).get('operational',0)}",
        ]
        if result.get("input_file"):
            lines.append(f"- Input file: `{result['input_file']}`")
        lines += ["", "## Findings", ""]
        for f in result.get("findings", []):
            lines.append(f"### {f.get('clause','unknown')}")
            lines.append(f"- Severity: {f.get('severity','unknown')}")
            lines.append(f"- Risk bucket: {f.get('risk_bucket','')}")
            lines.append(f"- Weighted score: {f.get('score',0)}")
            lines.append(f"- Preferred position: {f.get('preferred_position','')}")
            if f.get("clause_snippet"):
                lines.append(f"- Exact clause snippet: \"{f.get('clause_snippet')}\"")
            if f.get("clause_heading"):
                lines.append(f"- Section heading: {f.get('clause_heading')}")
            if f.get("paragraph_index") is not None:
                lines.append(f"- Paragraph index: {f.get('paragraph_index')}")
            if f.get("context"):
                lines.append(f"- Context: {f.get('context')}")
            if f.get("recommendation"):
                lines.append(f"- Recommendation: {f.get('recommendation')}")
            if f.get("posture_suggestion"):
                lines.append(f"- Counterparty posture suggestion: {f.get('posture_suggestion')}")
            if f.get("rule_hits"):
                lines.append("- Why flagged (rule hits):")
                lines.extend([f"  - {r}" for r in f.get("rule_hits", [])])
            if f.get("rule_hit_details"):
                lines.append("- Matched terms:")
                for d in f.get("rule_hit_details", []):
                    lines.append(f"  - pattern `{d.get('pattern')}` matched \"{d.get('match')}\"")
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
            if c.get("clause_heading"):
                lines.append(f"- Section heading: {c.get('clause_heading')}")
            if c.get("paragraph_index") is not None:
                lines.append(f"- Paragraph index: {c.get('paragraph_index')}")
            if c.get("context"):
                lines.append(f"- Context: {c.get('context')}")
            lines.append(f"- Concern: {c.get('concern')}")
            if c.get("recommendation"):
                lines.append(f"- Recommendation: {c.get('recommendation')}")
            if c.get("posture_suggestion"):
                lines.append(f"- Counterparty posture suggestion: {c.get('posture_suggestion')}")
            if c.get("rule_hits"):
                lines.append(f"- Why flagged: {', '.join(c.get('rule_hits', []))}")
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
    p_review.add_argument("--base", default=str(Path(__file__).resolve().parent))
    p_review.add_argument("--playbook", default=str(Path(__file__).resolve().parent / "output/medicus_nda_playbook.json"))
    p_review.add_argument("--counterparty", help="Counterparty profile name (loads profiles/<name>.json)")
    p_review.add_argument("--file")
    p_review.add_argument("--text")
    p_review.add_argument("--out-json")
    p_review.add_argument("--out-md")
    p_review.set_defaults(func=cmd_review)

    p_snap = sub.add_parser("playbook-snapshot", help="Snapshot current playbook version")
    p_snap.add_argument("--base", default=str(Path(__file__).resolve().parent))
    p_snap.add_argument("--playbook", default=str(Path(__file__).resolve().parent / "output/medicus_nda_playbook.json"))
    p_snap.set_defaults(func=cmd_playbook_snapshot)

    p_diff = sub.add_parser("playbook-diff", help="Diff two playbook snapshots")
    p_diff.add_argument("--a", required=True)
    p_diff.add_argument("--b", required=True)
    p_diff.add_argument("--out")
    p_diff.set_defaults(func=cmd_playbook_diff)

    p_lock = sub.add_parser("playbook-lock", help="Lock current playbook for a specific counterparty")
    p_lock.add_argument("--base", default=str(Path(__file__).resolve().parent))
    p_lock.add_argument("--playbook", default=str(Path(__file__).resolve().parent / "output/medicus_nda_playbook.json"))
    p_lock.add_argument("--counterparty", required=True)
    p_lock.set_defaults(func=cmd_playbook_lock)

    p_red = sub.add_parser("generate-redlines", help="Generate clause-ready redline draft from review JSON")
    p_red.add_argument("--review-json", required=True)
    p_red.add_argument("--out", required=True)
    p_red.set_defaults(func=cmd_generate_redlines)

    p_office = sub.add_parser("generate-office-script", help="Generate Office Script bridge from step5 find/replace pack")
    p_office.add_argument("--find-replace-pack", required=True)
    p_office.add_argument("--out", required=True)
    p_office.set_defaults(func=cmd_generate_office_script)

    p_gate = sub.add_parser("quality-gate", help="Run pre-step4 quality checks")
    p_gate.add_argument("--redline", required=True)
    p_gate.add_argument("--source-text")
    p_gate.add_argument("--out-json")
    p_gate.set_defaults(func=cmd_quality_gate)

    p_manifest = sub.add_parser("create-manifest", help="Create audit trail manifest for a run")
    p_manifest.add_argument("--base", default=str(Path(__file__).resolve().parent))
    p_manifest.add_argument("--counterparty", default="")
    p_manifest.add_argument("--playbook", default="")
    p_manifest.add_argument("--profile", default="")
    p_manifest.add_argument("--decisions-source", default="")
    p_manifest.add_argument("--files", nargs="+", required=True)
    p_manifest.add_argument("--out", required=True)
    p_manifest.set_defaults(func=cmd_create_manifest)

    p_init = sub.add_parser("init", help="Onboarding wizard/questionnaire to generate org config + default profile")
    p_init.add_argument("--base", default=str(Path(__file__).resolve().parent))
    p_init.add_argument("--interactive", action="store_true")
    p_init.add_argument("--org-name")
    p_init.add_argument("--risk-posture", default="balanced", choices=["strict", "balanced", "commercial"])
    p_init.add_argument("--preferred-jurisdictions", default="Austria")
    p_init.add_argument("--survival-years", type=int, default=5)
    p_init.add_argument("--ai-policy", default="guardrailed", choices=["restricted", "guardrailed", "permissive"])
    p_init.add_argument("--retention-carveout", default="Allow limited backup/legal retention under continuing confidentiality obligations.")
    p_init.set_defaults(func=cmd_init)

    p_ingest = sub.add_parser("ingest", help="Ingest existing contracts/playbooks and propose policy/profile updates")
    p_ingest.add_argument("--base", default=str(Path(__file__).resolve().parent))
    p_ingest.add_argument("--files", nargs="+", required=True)
    p_ingest.set_defaults(func=cmd_ingest)

    p_setup = sub.add_parser("setup", help="Combined setup: init plus optional ingest")
    p_setup.add_argument("--base", default=str(Path(__file__).resolve().parent))
    p_setup.add_argument("--interactive", action="store_true")
    p_setup.add_argument("--org-name")
    p_setup.add_argument("--risk-posture", default="balanced", choices=["strict", "balanced", "commercial"])
    p_setup.add_argument("--preferred-jurisdictions", default="Austria")
    p_setup.add_argument("--survival-years", type=int, default=5)
    p_setup.add_argument("--ai-policy", default="guardrailed", choices=["restricted", "guardrailed", "permissive"])
    p_setup.add_argument("--retention-carveout", default="Allow limited backup/legal retention under continuing confidentiality obligations.")
    p_setup.add_argument("--ingest-files", nargs="+")
    p_setup.set_defaults(func=cmd_setup)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
