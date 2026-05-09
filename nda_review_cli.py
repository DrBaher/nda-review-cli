#!/usr/bin/env python3
import argparse
import html
import json
import re
import hashlib
import shutil
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from collections import Counter, defaultdict
import difflib
from typing import Optional
from xml.etree import ElementTree as ET
from rule_engine import clause_hit, red_flag_hits

NDA_PAT = re.compile(r"\b(nda|non[-\s]?disclosure|confidentiality agreement|confidential disclosure agreement|cda|mutual nda|geheimhaltungsvereinbarung|vertraulich|vertrauliche informationen)\b", re.I)

FALLBACK_CLAUSE_RULES = {
    "definition_of_confidential_information": {
        "keywords": [r"confidential information"],
        "preferred": "Use clear definition and objective boundaries.",
        "red_flags": ["no objective boundary"],
    }
}

FALLBACK_SIGNAL_PATTERNS = {
    "risk": [r"liability", r"indemn", r"unlimited", r"perpetual"],
}

MIN_POLICY_VERSION = "0.2.0"
SUPPORTED_TEMPLATES = {
    "saas": {
        "risk_posture": "balanced",
        "preferred_jurisdictions": "Delaware,New York,England",
        "survival_years": 3,
        "ai_policy": "guardrailed",
        "retention_carveout": "Allow encrypted backups, security logging, and legal retention under continuing confidentiality obligations.",
        "clause_preferences": {
            "term_and_survival": "Prefer 3-year confidentiality survival, with indefinite protection limited to trade secrets only.",
            "use_restrictions": "Use should stay limited to evaluating or delivering the SaaS relationship described by the parties.",
            "liability_and_remedies": "Accept targeted injunctive relief, but reject uncapped liability expansion hidden inside the NDA.",
        },
    },
    "healthcare": {
        "risk_posture": "strict",
        "preferred_jurisdictions": "Austria,Germany",
        "survival_years": 7,
        "ai_policy": "restricted",
        "retention_carveout": "Allow only minimum backup, audit, patient-safety, and legal retention required under strict continuing confidentiality controls.",
        "clause_preferences": {
            "definition_of_confidential_information": "Health and patient-adjacent data should be explicitly covered and treated as highly sensitive confidential information.",
            "term_and_survival": "Prefer at least 7-year confidentiality survival, with indefinite treatment limited to trade secrets and regulated health data as required by law.",
            "return_or_destroy": "Return or destroy on request, but preserve legally required medical, audit, and backup retention under strict access controls.",
        },
    },
    "enterprise": {
        "risk_posture": "strict",
        "preferred_jurisdictions": "New York,Delaware,England",
        "survival_years": 5,
        "ai_policy": "guardrailed",
        "retention_carveout": "Allow internal archive, backup, compliance, and litigation-hold retention under continuing confidentiality obligations.",
        "clause_preferences": {
            "governing_law_jurisdiction": "Prefer commercially standard enterprise venues with no exclusive foreign venue surprises.",
            "assignment_and_affiliates": "Allow controlled affiliate sharing under the same confidentiality obligations, but no unrestricted third-party assignment.",
            "residuals": "Reject broad residuals rights that dilute enterprise confidential information protections.",
        },
    },
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


def _parse_version_tuple(raw):
    if not isinstance(raw, str):
        return None
    parts = raw.strip().split(".")
    if len(parts) != 3:
        return None
    try:
        return tuple(int(x) for x in parts)
    except ValueError:
        return None


def _policy_validation_errors(data):
    errors = []
    if not isinstance(data, dict):
        return ["policy file must contain a top-level JSON object"]

    version = data.get("version")
    version_tuple = _parse_version_tuple(version)
    if version_tuple is None:
        errors.append("version must be a semantic version string like 0.2.0")
    elif version_tuple < _parse_version_tuple(MIN_POLICY_VERSION):
        errors.append(f"version {version} is not supported; minimum supported version is {MIN_POLICY_VERSION}")

    if not isinstance(data.get("org_name"), str) or not data.get("org_name", "").strip():
        errors.append("org_name is required and must be a non-empty string")

    clause_rules = data.get("clause_rules")
    if not isinstance(clause_rules, dict) or not clause_rules:
        errors.append("clause_rules is required and must be a non-empty object")
    else:
        for clause, cfg in clause_rules.items():
            if not isinstance(cfg, dict):
                errors.append(f"clause_rules.{clause} must be an object")
                continue
            keywords = cfg.get("keywords")
            if not isinstance(keywords, list) or not keywords or not all(isinstance(x, str) and x.strip() for x in keywords):
                errors.append(f"clause_rules.{clause}.keywords must be a non-empty list of strings")
            preferred = cfg.get("preferred")
            if not isinstance(preferred, str) or not preferred.strip():
                errors.append(f"clause_rules.{clause}.preferred must be a non-empty string")
            red_flags = cfg.get("red_flags")
            if not isinstance(red_flags, list) or not all(isinstance(x, str) and x.strip() for x in red_flags):
                errors.append(f"clause_rules.{clause}.red_flags must be a list of strings")

    signal_patterns = data.get("negotiation_signal_patterns")
    if not isinstance(signal_patterns, dict) or not signal_patterns:
        errors.append("negotiation_signal_patterns is required and must be a non-empty object")
    else:
        for name, patterns in signal_patterns.items():
            if not isinstance(patterns, list) or not patterns or not all(isinstance(x, str) and x.strip() for x in patterns):
                errors.append(f"negotiation_signal_patterns.{name} must be a non-empty list of strings")

    defaults = data.get("defaults")
    if defaults is not None and not isinstance(defaults, dict):
        errors.append("defaults must be an object when provided")

    weights = data.get("risk_weights")
    if weights is not None and not isinstance(weights, dict):
        errors.append("risk_weights must be an object when provided")

    jurisdictions = data.get("preferred_jurisdictions")
    if jurisdictions is not None and (not isinstance(jurisdictions, list) or not all(isinstance(x, str) and x.strip() for x in jurisdictions)):
        errors.append("preferred_jurisdictions must be a list of strings when provided")

    return errors


def validate_policy_data(data):
    errors = _policy_validation_errors(data)
    return {"ok": not errors, "errors": errors}


def validate_policy_file(path: Path):
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError:
        return {"ok": False, "errors": [f"policy file not found: {path}"], "path": str(path), "data": None}
    except json.JSONDecodeError as exc:
        return {"ok": False, "errors": [f"invalid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}"], "path": str(path), "data": None}
    except Exception as exc:
        return {"ok": False, "errors": [f"failed to read policy file: {exc}"], "path": str(path), "data": None}

    result = validate_policy_data(data)
    result["path"] = str(path)
    result["data"] = data
    return result


def _policy_candidate_paths(base: Path, policy_path: Optional[str] = None):
    candidates = []
    if policy_path:
        p = Path(policy_path)
        candidates.append(p if p.is_absolute() else (base / p))
    candidates.append(base / "config" / "org-policy.json")
    candidates.append(base / "config" / "default-policy.json")

    repo_default = Path(__file__).resolve().parent / "config" / "default-policy.json"
    if repo_default not in candidates:
        candidates.append(repo_default)

    uniq = []
    seen = set()
    for c in candidates:
        s = str(c)
        if s in seen:
            continue
        seen.add(s)
        uniq.append(c)
    return uniq


def load_policy_config(base: Path, policy_path: Optional[str] = None):
    candidates = _policy_candidate_paths(base, policy_path)

    default_data = {}
    for c in candidates:
        if not c.exists():
            continue
        validated = validate_policy_file(c)
        if validated["ok"]:
            default_data = validated["data"]
            break

    default_clause_rules = default_data.get("clause_rules") or FALLBACK_CLAUSE_RULES
    default_signal_patterns = default_data.get("negotiation_signal_patterns") or FALLBACK_SIGNAL_PATTERNS

    for c in candidates:
        if not c.exists():
            continue
        validated = validate_policy_file(c)
        if not validated["ok"]:
            raise ValueError(f"Invalid policy file {c}: " + "; ".join(validated["errors"]))
        data = validated["data"]
        clause_rules = data.get("clause_rules") or default_clause_rules
        signal_patterns = data.get("negotiation_signal_patterns") or default_signal_patterns
        org_name = data.get("org_name") or "Generic Org"
        return {
            "path": str(c),
            "org_name": org_name,
            "clause_rules": clause_rules,
            "negotiation_signal_patterns": signal_patterns,
            "raw": data,
        }

    return {
        "path": None,
        "org_name": "Generic Org",
        "clause_rules": default_clause_rules,
        "negotiation_signal_patterns": default_signal_patterns,
        "raw": {},
    }


def _run_text_command(cmd):
    exe = shutil.which(cmd[0])
    if not exe:
        return None, f"{cmd[0]} not available"
    try:
        proc = subprocess.run([exe] + cmd[1:], capture_output=True, check=False)
    except Exception as exc:
        return None, str(exc)
    if proc.returncode != 0:
        stderr = proc.stderr.decode("utf-8", errors="ignore").strip()
        return None, stderr or f"{cmd[0]} exited with code {proc.returncode}"
    text = proc.stdout.decode("utf-8", errors="ignore")
    return text, None


def _normalize_extracted_text(text):
    text = html.unescape(text or "")
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("\xa0", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _extract_docx_text(path: Path):
    attempts = []
    try:
        with zipfile.ZipFile(path) as zf:
            xml_bytes = zf.read("word/document.xml")
        root = ET.fromstring(xml_bytes)
        text = _normalize_extracted_text(" ".join(root.itertext()))
        attempts.append("zipfile:word/document.xml")
        if text:
            return text, "ok:docx-xml", attempts, None
        attempts.append("zipfile:empty-text")
    except KeyError:
        attempts.append("zipfile:missing-word/document.xml")
    except zipfile.BadZipFile:
        attempts.append("zipfile:bad-docx")
    except ET.ParseError:
        attempts.append("zipfile:xml-parse-error")
    except Exception as exc:
        attempts.append(f"zipfile:error:{exc}")

    text, err = _run_text_command(["textutil", "-convert", "txt", "-stdout", str(path)])
    attempts.append("textutil")
    text = _normalize_extracted_text(text or "")
    if text:
        return text, "ok:textutil", attempts, None
    return "", "failed", attempts, err or "docx extraction returned empty output"


def _extract_pdf_text(path: Path):
    attempts = []
    text, err = _run_text_command(["pdftotext", "-layout", str(path), "-"])
    attempts.append("pdftotext")
    text = _normalize_extracted_text(text or "")
    if text:
        return text, "ok:pdftotext", attempts, None

    fallback_text, fallback_err = _run_text_command(["textutil", "-convert", "txt", "-stdout", str(path)])
    attempts.append("textutil")
    fallback_text = _normalize_extracted_text(fallback_text or "")
    if fallback_text:
        return fallback_text, "ok:textutil", attempts, None
    error = fallback_err or err or "pdf extraction returned empty output"
    return "", "failed", attempts, error


def _extract_plain_text(path: Path):
    try:
        text = path.read_text(errors="ignore")
    except Exception as exc:
        return "", "failed", ["read_text"], str(exc)
    normalized = _normalize_extracted_text(text)
    if normalized:
        return normalized, "ok:read_text", ["read_text"], None
    return "", "empty", ["read_text"], "file read succeeded but contained no extractable text"


def _read_any_text(path: Path):
    suffix = path.suffix.lower()
    if suffix == ".docx":
        text, status, attempts, error = _extract_docx_text(path)
    elif suffix == ".pdf":
        text, status, attempts, error = _extract_pdf_text(path)
    else:
        text, status, attempts, error = _extract_plain_text(path)
    return {
        "text": text,
        "extraction_status": status,
        "extractors_tried": attempts,
        "error": error,
    }


def _build_ingest_roots(base: Path):
    return [
        base / "knowledge" / "inbox",
        base / "knowledge" / "contracts",
        base / "knowledge" / "redlines",
        base / "inbox",
        base / "input",
    ]


def _build_playbook_expected_paths(base: Path, gmail_paths, drive_paths):
    return {
        "gmail_paths": [base / p for p in gmail_paths],
        "drive_paths": [base / p for p in drive_paths],
    }


def _confirm_autodiscovered_files(files, prompt_label):
    print(prompt_label)
    for p in files:
        print(f"- {p}")
    raw = input("Use these files? [Y/n]: ").strip().lower()
    return raw in {"", "y", "yes"}


def _resolve_ingest_files(base: Path, explicit_files, yes=False, no_prompt=False, prompt_label="Discovered ingest files:"):
    files = list(explicit_files or [])
    autodiscovered = False
    approval_needed = False
    skipped_for_approval = False

    if not files:
        files = discover_ingest_files(base)
        autodiscovered = bool(files)
        approval_needed = autodiscovered

    if autodiscovered and not yes and not no_prompt:
        if sys.stdin.isatty():
            if not _confirm_autodiscovered_files(files, prompt_label):
                files = []
        else:
            skipped_for_approval = True
            files = []

    return {
        "files": files,
        "autodiscovered": autodiscovered,
        "approval_needed": approval_needed,
        "skipped_for_approval": skipped_for_approval,
    }


def _apply_template_defaults(args):
    template = getattr(args, "template", None)
    if not template:
        return
    cfg = SUPPORTED_TEMPLATES[template]
    if getattr(args, "risk_posture", None) == "balanced":
        args.risk_posture = cfg["risk_posture"]
    if getattr(args, "preferred_jurisdictions", None) == "Austria":
        args.preferred_jurisdictions = cfg["preferred_jurisdictions"]
    if getattr(args, "survival_years", None) == 5:
        args.survival_years = cfg["survival_years"]
    if getattr(args, "ai_policy", None) == "guardrailed":
        args.ai_policy = cfg["ai_policy"]
    if getattr(args, "retention_carveout", None) == "Allow limited backup/legal retention under continuing confidentiality obligations.":
        args.retention_carveout = cfg["retention_carveout"]


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
    context = f"Clause '{clause}' was detected in the agreement text and should be reviewed against the configured preferred position."
    if snippet:
        context = f"Detected clause text indicates '{clause}' is present. Validate whether this exact wording aligns with your configured policy standards."
    recommendation = f"Align this clause with configured position: {preferred_position}"
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
        return "Use configured default position."
    prefs = profile.get("clause_preferences", {})
    if clause in prefs:
        return f"Counterparty posture: {prefs[clause]}"
    fallback = profile.get("fallback_posture")
    if fallback:
        return f"Counterparty fallback posture: {fallback}"
    return "Use configured default position."


def build_playbook(messages, drive_items, clause_rules, signal_patterns, org_name="Generic Org"):
    clause_counts = Counter()
    evidence = defaultdict(list)
    signal_counts = Counter()

    for m in messages:
        txt = msg_text(m)
        ltxt = txt.lower()

        for sig, pats in signal_patterns.items():
            if any(re.search(p, ltxt, re.I) for p in pats):
                signal_counts[sig] += 1

        sents = extract_sentences(txt)
        for clause, cfg in clause_rules.items():
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
        "org_name": org_name,
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

    for clause, cfg in clause_rules.items():
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
                "concern": ", ".join(f.get("red_flags") or []) or "Clause deviates from configured preferred position.",
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
    _apply_template_defaults(args)

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

    default_policy = load_policy_config(base, args.default_policy)

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
        "clause_rules": default_policy["clause_rules"],
        "negotiation_signal_patterns": default_policy["negotiation_signal_patterns"],
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
    if getattr(args, "template", None):
        profile["template"] = args.template
        profile["clause_preferences"].update(SUPPORTED_TEMPLATES[args.template]["clause_preferences"])

    org_out = cfg_dir / "org-policy.json"
    prof_out = prof_dir / "default.json"
    org_out.write_text(json.dumps(org_policy, indent=2, ensure_ascii=False))
    prof_out.write_text(json.dumps(profile, indent=2, ensure_ascii=False))
    print(json.dumps({"ok": True, "org_policy": str(org_out), "default_profile": str(prof_out)}, ensure_ascii=False))


def discover_ingest_files(base: Path):
    roots = _build_ingest_roots(base)
    exts = {".txt", ".md", ".docx", ".pdf"}
    found = []
    for root in roots:
        if not root.exists() or not root.is_dir():
            continue
        for p in root.rglob("*"):
            if p.is_file() and p.suffix.lower() in exts and not p.name.startswith("."):
                found.append(str(p if p.is_absolute() else (base / p)))
    return sorted(set(found))


def parse_paths_input(raw: str):
    if not raw:
        return []
    if "," in raw:
        items = [x.strip() for x in raw.split(",")]
    else:
        items = [x.strip() for x in raw.split()]
    return [x for x in items if x]


def cmd_ingest(args):
    base = Path(args.base)
    policy_cfg = load_policy_config(base, args.policy)
    clause_rules = policy_cfg["clause_rules"]
    kdir = base / "knowledge"
    kdir.mkdir(parents=True, exist_ok=True)
    proposed_dir = kdir / "proposed"
    proposed_dir.mkdir(parents=True, exist_ok=True)

    resolution = _resolve_ingest_files(
        base,
        args.files,
        yes=getattr(args, "yes", False),
        no_prompt=getattr(args, "no_prompt", False),
        prompt_label="Auto-discovered ingest candidates:",
    )
    files = resolution["files"]
    if getattr(args, "autodiscovered", None) is not None:
        resolution["autodiscovered"] = bool(args.autodiscovered)
    if getattr(args, "skipped_for_approval", None) is not None:
        resolution["skipped_for_approval"] = bool(args.skipped_for_approval)
    if getattr(args, "approval_needed", None) is not None:
        resolution["approval_needed"] = bool(args.approval_needed)
    if not files and not getattr(args, "no_prompt", False) and sys.stdin.isatty():
        raw = input("No ingest files found. Enter file paths (comma/space-separated), or press Enter to skip: ").strip()
        files = parse_paths_input(raw)

    paths = [Path(p) for p in files]
    sources = []
    aggregate = {k: {"hits": 0, "examples": []} for k in clause_rules.keys()}

    for p in paths:
        rp = p if p.is_absolute() else (base / p)
        if not rp.exists():
            continue
        extraction = _read_any_text(rp)
        text = extraction["text"]
        ltxt = text.lower()
        matched = []
        for clause, cfg in clause_rules.items():
            hit, pats = clause_hit(ltxt, cfg.get("keywords", []))
            if not hit:
                continue
            matched.append(clause)
            aggregate[clause]["hits"] += 1
            if len(aggregate[clause]["examples"]) < 3:
                aggregate[clause]["examples"].append(pats[0])
        sources.append({
            "path": str(rp),
            "matched_clauses": matched,
            "sha256": sha256_file(rp),
            "extraction_status": extraction["extraction_status"],
            "extractors_tried": extraction["extractors_tried"],
            "extractable_text": bool(text),
            "error": extraction["error"],
        })

    suggestions = []
    for clause, data in aggregate.items():
        if data["hits"] == 0:
            continue
        suggestions.append({
            "clause": clause,
            "proposed_preference": clause_rules[clause]["preferred"],
            "confidence": "high" if data["hits"] >= 3 else "medium",
            "seen_count": data["hits"],
            "evidence": data["examples"],
            "status": "proposed",
        })

    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    out = proposed_dir / f"ingest-suggestions-{ts}.json"
    payload = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "policy_path": policy_cfg.get("path"),
        "autodiscovered": resolution["autodiscovered"],
        "approval_required": resolution["approval_needed"],
        "skipped_for_approval": resolution["skipped_for_approval"],
        "sources": sources,
        "suggestions": suggestions,
        "note": "Proposed-only. Review before promotion to active policy/profile.",
    }
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    print(json.dumps({
        "ok": True,
        "suggestions_file": str(out),
        "sources_ingested": len(sources),
        "autodiscovered": resolution["autodiscovered"],
        "skipped_for_approval": resolution["skipped_for_approval"],
    }, ensure_ascii=False))


def cmd_setup(args):
    # Combined flow: init + optional ingest.
    class Obj:
        pass

    init_args = Obj()
    init_args.base = args.base
    init_args.interactive = (args.interactive and not args.quick)
    init_args.org_name = args.org_name or ("Your Org" if args.quick else args.org_name)
    init_args.risk_posture = args.risk_posture
    init_args.preferred_jurisdictions = args.preferred_jurisdictions
    init_args.survival_years = args.survival_years
    init_args.ai_policy = args.ai_policy
    init_args.retention_carveout = args.retention_carveout
    init_args.default_policy = args.default_policy
    init_args.template = args.template
    cmd_init(init_args)

    base = Path(args.base)
    resolution = _resolve_ingest_files(
        base,
        args.ingest_files,
        yes=args.yes,
        no_prompt=args.no_prompt,
        prompt_label="Auto-discovered onboarding files:",
    )
    ingest_files = resolution["files"]
    if not ingest_files and not args.no_prompt and sys.stdin.isatty():
        raw = input("No onboarding files auto-found. Add files now? (paths comma/space-separated, Enter to skip): ").strip()
        ingest_files = parse_paths_input(raw)

    if ingest_files:
        ingest_args = Obj()
        ingest_args.base = args.base
        ingest_args.files = ingest_files
        ingest_args.policy = args.policy
        ingest_args.no_prompt = True
        ingest_args.yes = True
        ingest_args.autodiscovered = resolution["autodiscovered"]
        ingest_args.skipped_for_approval = resolution["skipped_for_approval"]
        ingest_args.approval_needed = resolution["approval_needed"]
        cmd_ingest(ingest_args)

    should_build = args.build
    if args.quick and not args.no_build and not args.build:
        should_build = True
    if args.no_build:
        should_build = False

    build_output = None
    if should_build:
        build_args = Obj()
        build_args.base = args.base
        build_args.policy = args.policy
        build_args.gmail_paths = ["data/raw_strict/gmail_primary.json", "data/raw_strict/gmail_secondary.json"]
        build_args.drive_paths = ["data/raw_strict/drive_primary.json", "data/raw_strict/drive_secondary.json"]
        build_args.out_json = "output/nda_playbook.json"
        build_args.out_md = "output/nda_playbook.md"
        cmd_build(build_args)
        build_output = str(base / build_args.out_json)

    print(json.dumps({
        "next_steps": [
            f"Build playbook: {base / 'nda_review_cli.py'} build-playbook --base {base}",
            f"Run review: {base / 'review_nda.sh'} /path/to/nda.txt",
            "Optional local override: edit config/org-policy.json",
        ],
        "ingest_files_used": len(ingest_files),
        "autodiscovered_files_confirmed": bool(ingest_files) and resolution["autodiscovered"],
        "skipped_for_approval": resolution["skipped_for_approval"],
        "build_ran": should_build,
        "playbook_json": build_output,
    }, ensure_ascii=False))


def cmd_policy_validate(args):
    target = Path(args.file)
    result = validate_policy_file(target)
    payload = {
        "ok": result["ok"],
        "file": str(target),
        "minimum_version": MIN_POLICY_VERSION,
        "errors": result["errors"],
    }
    if result["ok"]:
        payload["version"] = result["data"].get("version")
        payload["org_name"] = result["data"].get("org_name")
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    if not result["ok"]:
        raise SystemExit(2)


def cmd_doctor(args):
    base = Path(args.base)
    hard_failures = []
    warnings = []
    fixes = []
    checks = []

    policy_checks = []
    valid_policies = []
    for candidate in _policy_candidate_paths(base, args.policy):
        entry = {
            "path": str(candidate),
            "exists": candidate.exists(),
        }
        if candidate.exists():
            validation = validate_policy_file(candidate)
            entry["valid"] = validation["ok"]
            entry["errors"] = validation["errors"]
            if validation["ok"]:
                valid_policies.append(str(candidate))
            else:
                hard_failures.append(f"Invalid policy file: {candidate}")
                fixes.append(f"Run `./nda_review_cli.py policy-validate --file {candidate}` and fix the reported schema/version errors.")
        else:
            entry["valid"] = False
            entry["errors"] = ["file not present"]
        policy_checks.append(entry)

    if not valid_policies:
        hard_failures.append("No valid policy file found in discovery paths.")
        fixes.append("Create one with `./nda_review_cli.py init --base <path>` or place a valid `config/default-policy.json` / `config/org-policy.json` in the base directory.")
    checks.append({"name": "policy_files", "status": "ok" if valid_policies else "fail", "details": policy_checks})

    expected = _build_playbook_expected_paths(
        base,
        args.gmail_paths,
        args.drive_paths,
    )
    data_checks = []
    for group, paths in expected.items():
        for p in paths:
            exists = p.exists()
            item = {"path": str(p), "exists": exists, "group": group}
            if not exists:
                hard_failures.append(f"Missing build-playbook input: {p}")
                fixes.append(f"Create or point `{group}` to a JSON export file with `./nda_review_cli.py build-playbook --base {base} --{'gmail-paths' if group == 'gmail_paths' else 'drive-paths'} ...`.")
            data_checks.append(item)
    checks.append({"name": "build_playbook_paths", "status": "ok" if not [x for x in data_checks if not x["exists"]] else "fail", "details": data_checks})

    ingest_candidates = discover_ingest_files(base)
    ingest_checks = []
    for raw in ingest_candidates:
        path = Path(raw)
        readable = path.exists() and path.is_file()
        detail = {"path": str(path), "exists": path.exists(), "readable": readable}
        if readable:
            extraction = _read_any_text(path)
            detail["extraction_status"] = extraction["extraction_status"]
            detail["extractors_tried"] = extraction["extractors_tried"]
            detail["error"] = extraction["error"]
            detail["extractable_text"] = bool(extraction["text"])
            if extraction["extraction_status"] == "failed":
                hard_failures.append(f"Unreadable ingest candidate: {path}")
                fixes.append(f"Convert `{path}` to text/markdown or install a working extractor (`pdftotext` for PDFs, `textutil` on macOS for DOCX/PDF fallback).")
        else:
            hard_failures.append(f"Ingest candidate is not readable: {path}")
            fixes.append(f"Check file permissions or remove `{path}` from autodiscovery roots.")
        ingest_checks.append(detail)
    if not ingest_candidates:
        warnings.append("No ingest candidates found in autodiscovery roots.")
        fixes.append("Add documents under `knowledge/inbox`, `knowledge/contracts`, `knowledge/redlines`, `inbox`, or `input` if you want onboarding ingestion.")
    checks.append({"name": "ingest_candidates", "status": "ok" if ingest_checks and not [x for x in ingest_checks if x.get("extraction_status") == "failed" or not x["readable"]] else ("warn" if not ingest_checks else "fail"), "details": ingest_checks})

    payload = {
        "ok": not hard_failures,
        "base": str(base),
        "checks": checks,
        "hard_failures": sorted(set(hard_failures)),
        "warnings": sorted(set(warnings)),
        "suggested_fixes": sorted(set(fixes)),
    }
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    if hard_failures:
        raise SystemExit(2)


def cmd_build(args):
    base = Path(args.base)
    policy_cfg = load_policy_config(base, args.policy)
    gmail_paths = [base / p for p in args.gmail_paths]
    drive_paths = [base / p for p in args.drive_paths]

    messages = filter_nda(load_messages(gmail_paths))
    drive_items = load_messages(drive_paths)

    playbook = build_playbook(
        messages,
        drive_items,
        policy_cfg["clause_rules"],
        policy_cfg["negotiation_signal_patterns"],
        org_name=policy_cfg.get("org_name", "Generic Org"),
    )

    out_json = base / args.out_json
    out_md = base / args.out_md
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(playbook, indent=2, ensure_ascii=False))

    md = [
        f"# {playbook.get('org_name','Generic')} NDA Playbook",
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
    print(json.dumps({"ok": True, "policy_path": policy_cfg.get("path"), "playbook_json": str(out_json), "playbook_md": str(out_md)}, indent=2))


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
    parser = argparse.ArgumentParser(description="NDA Review CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_build = sub.add_parser("build-playbook", help="Build NDA playbook from extracted Gmail/Drive corpus")
    p_build.add_argument("--base", default=str(Path(__file__).resolve().parent))
    p_build.add_argument("--policy", help="Policy config path (defaults to config/org-policy.json then config/default-policy.json)")
    p_build.add_argument("--gmail-paths", nargs="+", default=["data/raw_strict/gmail_primary.json", "data/raw_strict/gmail_secondary.json"])
    p_build.add_argument("--drive-paths", nargs="+", default=["data/raw_strict/drive_primary.json", "data/raw_strict/drive_secondary.json"])
    p_build.add_argument("--out-json", default="output/nda_playbook.json")
    p_build.add_argument("--out-md", default="output/nda_playbook.md")
    p_build.set_defaults(func=cmd_build)

    p_review = sub.add_parser("review", help="Review NDA text against generated playbook")
    p_review.add_argument("--base", default=str(Path(__file__).resolve().parent))
    p_review.add_argument("--playbook", default=str(Path(__file__).resolve().parent / "output/nda_playbook.json"))
    p_review.add_argument("--counterparty", help="Counterparty profile name (loads profiles/<name>.json)")
    p_review.add_argument("--file")
    p_review.add_argument("--text")
    p_review.add_argument("--out-json")
    p_review.add_argument("--out-md")
    p_review.set_defaults(func=cmd_review)

    p_snap = sub.add_parser("playbook-snapshot", help="Snapshot current playbook version")
    p_snap.add_argument("--base", default=str(Path(__file__).resolve().parent))
    p_snap.add_argument("--playbook", default=str(Path(__file__).resolve().parent / "output/nda_playbook.json"))
    p_snap.set_defaults(func=cmd_playbook_snapshot)

    p_diff = sub.add_parser("playbook-diff", help="Diff two playbook snapshots")
    p_diff.add_argument("--a", required=True)
    p_diff.add_argument("--b", required=True)
    p_diff.add_argument("--out")
    p_diff.set_defaults(func=cmd_playbook_diff)

    p_lock = sub.add_parser("playbook-lock", help="Lock current playbook for a specific counterparty")
    p_lock.add_argument("--base", default=str(Path(__file__).resolve().parent))
    p_lock.add_argument("--playbook", default=str(Path(__file__).resolve().parent / "output/nda_playbook.json"))
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

    p_policy = sub.add_parser("policy-validate", help="Validate a policy JSON file against the supported schema and version")
    p_policy.add_argument("--file", required=True)
    p_policy.set_defaults(func=cmd_policy_validate)

    p_doctor = sub.add_parser("doctor", help="Validate first-run onboarding readiness and data discoverability")
    p_doctor.add_argument("--base", default=str(Path(__file__).resolve().parent))
    p_doctor.add_argument("--policy", help="Optional explicit policy file to validate first")
    p_doctor.add_argument("--gmail-paths", nargs="+", default=["data/raw_strict/gmail_primary.json", "data/raw_strict/gmail_secondary.json"])
    p_doctor.add_argument("--drive-paths", nargs="+", default=["data/raw_strict/drive_primary.json", "data/raw_strict/drive_secondary.json"])
    p_doctor.set_defaults(func=cmd_doctor)

    p_init = sub.add_parser("init", help="Onboarding wizard/questionnaire to generate org config + default profile")
    p_init.add_argument("--base", default=str(Path(__file__).resolve().parent))
    p_init.add_argument("--interactive", action="store_true")
    p_init.add_argument("--org-name")
    p_init.add_argument("--template", choices=sorted(SUPPORTED_TEMPLATES.keys()))
    p_init.add_argument("--risk-posture", default="balanced", choices=["strict", "balanced", "commercial"])
    p_init.add_argument("--preferred-jurisdictions", default="Austria")
    p_init.add_argument("--survival-years", type=int, default=5)
    p_init.add_argument("--ai-policy", default="guardrailed", choices=["restricted", "guardrailed", "permissive"])
    p_init.add_argument("--retention-carveout", default="Allow limited backup/legal retention under continuing confidentiality obligations.")
    p_init.add_argument("--default-policy", help="Default policy seed file path", default="config/default-policy.json")
    p_init.set_defaults(func=cmd_init)

    p_ingest = sub.add_parser("ingest", help="Ingest existing contracts/playbooks and propose policy/profile updates")
    p_ingest.add_argument("--base", default=str(Path(__file__).resolve().parent))
    p_ingest.add_argument("--policy", help="Policy config path", default="config/org-policy.json")
    p_ingest.add_argument("--files", nargs="*", help="Optional files to ingest. If omitted, auto-discovers from knowledge/inbox, knowledge/contracts, knowledge/redlines, inbox, input")
    p_ingest.add_argument("--yes", action="store_true", help="Approve auto-discovered files without confirmation")
    p_ingest.add_argument("--no-prompt", action="store_true", help="Do not prompt when no files are found")
    p_ingest.set_defaults(func=cmd_ingest)

    p_setup = sub.add_parser("setup", help="Combined setup: init plus optional ingest")
    p_setup.add_argument("--base", default=str(Path(__file__).resolve().parent))
    p_setup.add_argument("--interactive", action="store_true")
    p_setup.add_argument("--org-name")
    p_setup.add_argument("--template", choices=sorted(SUPPORTED_TEMPLATES.keys()))
    p_setup.add_argument("--risk-posture", default="balanced", choices=["strict", "balanced", "commercial"])
    p_setup.add_argument("--preferred-jurisdictions", default="Austria")
    p_setup.add_argument("--survival-years", type=int, default=5)
    p_setup.add_argument("--ai-policy", default="guardrailed", choices=["restricted", "guardrailed", "permissive"])
    p_setup.add_argument("--retention-carveout", default="Allow limited backup/legal retention under continuing confidentiality obligations.")
    p_setup.add_argument("--default-policy", help="Default policy seed file path", default="config/default-policy.json")
    p_setup.add_argument("--policy", help="Policy config path for ingest", default="config/org-policy.json")
    p_setup.add_argument("--ingest-files", nargs="+")
    p_setup.add_argument("--build", action="store_true", help="Run build-playbook at the end of setup")
    p_setup.add_argument("--no-build", action="store_true", help="Skip build-playbook at the end of setup, including quick mode default build")
    p_setup.add_argument("--quick", action="store_true", help="Zero-friction onboarding: defaults + auto-ingest discovery")
    p_setup.add_argument("--yes", action="store_true", help="Approve auto-discovered files without confirmation")
    p_setup.add_argument("--no-prompt", action="store_true", help="Do not prompt for file paths when none are found")
    p_setup.set_defaults(func=cmd_setup)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
