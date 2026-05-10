#!/usr/bin/env python3
import argparse
import html
import json
import os
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

__version__ = "0.5.0"

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
DEFAULT_SCORING_PROFILES = {
    "balanced": {
        "weights": {"legal": 1.2, "commercial": 1.0, "operational": 1.0, "severity_high": 3, "severity_low": 1},
        "decision_thresholds": {"approve_max": 4.99, "escalate_max": 9.99},
    },
    "strict": {
        "weights": {"legal": 1.4, "commercial": 1.1, "operational": 1.2, "severity_high": 4, "severity_low": 1},
        "decision_thresholds": {"approve_max": 3.99, "escalate_max": 8.99},
    },
    "commercial": {
        "weights": {"legal": 1.0, "commercial": 0.9, "operational": 0.9, "severity_high": 3, "severity_low": 1},
        "decision_thresholds": {"approve_max": 5.99, "escalate_max": 10.99},
    },
}
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
    p = base / "profiles" / f"{sanitize_slug(counterparty)}.json"
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            return {}
    return {}


def sanitize_slug(value: str):
    return re.sub(r"[^a-z0-9_]+", "_", value.strip().lower()).strip("_")


def scoring_profiles_path(base: Path, explicit: Optional[str] = None):
    if explicit:
        p = Path(explicit)
        return p if p.is_absolute() else (base / p)
    return base / "config" / "scoring-profiles.json"


def load_scoring_profiles(base: Path, explicit: Optional[str] = None):
    target = scoring_profiles_path(base, explicit)
    profiles = DEFAULT_SCORING_PROFILES
    if target.exists():
        try:
            data = json.loads(target.read_text())
            raw_profiles = data.get("profiles") if isinstance(data, dict) else None
            if isinstance(raw_profiles, dict) and raw_profiles:
                profiles = raw_profiles
        except Exception:
            profiles = DEFAULT_SCORING_PROFILES
    return {"path": str(target), "profiles": profiles}


def scoring_profile_details(base: Path, profile_name: Optional[str], scoring_profiles_file: Optional[str] = None):
    loaded = load_scoring_profiles(base, scoring_profiles_file)
    profiles = loaded["profiles"]
    chosen = profile_name or "balanced"
    if chosen not in profiles:
        chosen = "balanced"
    data = profiles[chosen]
    weights = {
        "legal": 1.0,
        "commercial": 1.0,
        "operational": 1.0,
        "severity_high": 3,
        "severity_low": 1,
    }
    weights.update(data.get("weights", {}))
    thresholds = {"approve_max": 4.99, "escalate_max": 9.99}
    thresholds.update(data.get("decision_thresholds", {}))
    return {"name": chosen, "weights": weights, "decision_thresholds": thresholds, "path": loaded["path"]}


def risk_weights(profile: Optional[dict] = None, scoring_profile: Optional[dict] = None):
    w = {
        "legal": 1.0,
        "commercial": 1.0,
        "operational": 1.0,
        "severity_high": 3,
        "severity_low": 1,
    }
    if scoring_profile and isinstance(scoring_profile.get("weights"), dict):
        w.update(scoring_profile["weights"])
    if profile and isinstance(profile.get("risk_weights"), dict):
        w.update(profile["risk_weights"])
    return w


def decision_thresholds(profile: Optional[dict] = None, scoring_profile: Optional[dict] = None):
    thresholds = {"approve_max": 4.99, "escalate_max": 9.99}
    if scoring_profile and isinstance(scoring_profile.get("decision_thresholds"), dict):
        thresholds.update(scoring_profile["decision_thresholds"])
    if profile and isinstance(profile.get("decision_thresholds"), dict):
        thresholds.update(profile["decision_thresholds"])
    return thresholds


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


def summarize_rule_patterns(keyword_hits, red_flag_trigger_hits):
    patterns = []
    for pat in keyword_hits:
        patterns.append(pat)
    for hit in red_flag_trigger_hits:
        pat = hit.get("pattern")
        if pat and pat not in patterns:
            patterns.append(pat)
    return patterns


def confidence_score(keyword_hits, red_flag_trigger_hits, snippet):
    score = 0.45
    if keyword_hits:
        score += min(0.25, 0.05 * len(keyword_hits))
    if red_flag_trigger_hits:
        score += min(0.2, 0.1 * len(red_flag_trigger_hits))
    if snippet:
        score += 0.1
    return round(min(score, 0.99), 2)


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


def learning_fields_for_finding(finding):
    return {
        "severity": finding.get("severity"),
        "preferred_position": finding.get("preferred_position"),
        "recommendation": finding.get("recommendation"),
        "confidence_score": finding.get("confidence_score"),
        "last_clause_heading": finding.get("clause_heading"),
        "last_paragraph_index": finding.get("paragraph_index"),
    }


def learn_profile_from_review(base: Path, counterparty: str, review_data: dict, source_review_file: str):
    slug = sanitize_slug(counterparty)
    profile_path = base / "profiles" / f"{slug}.json"
    if profile_path.exists():
        try:
            profile = json.loads(profile_path.read_text())
        except Exception:
            profile = {}
    else:
        profile = {}

    profile.setdefault("profile_name", counterparty)
    profile.setdefault("fallback_posture", "Use configured default position.")
    profile.setdefault("clause_preferences", {})
    profile.setdefault("review_memory", {"total_reviews": 0, "decision_counts": {}, "clause_hit_counts": {}, "review_history": []})

    changed_fields = []
    review_memory = profile["review_memory"]
    review_memory["total_reviews"] = int(review_memory.get("total_reviews", 0)) + 1
    changed_fields.append("review_memory.total_reviews")

    decision = review_data.get("decision", "unknown")
    counts = review_memory.setdefault("decision_counts", {})
    counts[decision] = int(counts.get(decision, 0)) + 1
    changed_fields.append(f"review_memory.decision_counts.{decision}")

    last_review = {
        "reviewed_at": datetime.now(timezone.utc).isoformat(),
        "source_review_file": source_review_file,
        "input_file": review_data.get("input_file"),
        "decision": review_data.get("decision"),
        "risk_score": review_data.get("risk_score"),
        "changed_fields": [],
    }

    clause_counts = review_memory.setdefault("clause_hit_counts", {})
    learned_positions = profile.setdefault("learned_clause_positions", {})
    for finding in review_data.get("findings", []):
        clause = finding.get("clause")
        if not clause:
            continue
        clause_counts[clause] = int(clause_counts.get(clause, 0)) + 1
        changed_fields.append(f"review_memory.clause_hit_counts.{clause}")
        profile["clause_preferences"][clause] = finding.get("preferred_position") or profile["clause_preferences"].get(clause, "")
        changed_fields.append(f"clause_preferences.{clause}")
        learned_positions[clause] = learning_fields_for_finding(finding)
        learned_positions[clause]["source_review_file"] = source_review_file
        learned_positions[clause]["updated_at"] = last_review["reviewed_at"]
        changed_fields.append(f"learned_clause_positions.{clause}")

    unique_changes = sorted(set(changed_fields))
    last_review["changed_fields"] = unique_changes
    review_memory["last_review"] = last_review
    review_memory["review_history"].append(last_review)
    review_memory["review_history"] = review_memory["review_history"][-20:]

    profile_path.parent.mkdir(parents=True, exist_ok=True)
    profile_path.write_text(json.dumps(profile, indent=2, ensure_ascii=False))
    return {"profile_path": str(profile_path), "changed_fields": unique_changes, "reviewed_at": last_review["reviewed_at"]}


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


def review_text(text, playbook, profile=None, scoring_profile=None, explainability=False):
    findings = []
    risk_score = 0
    breakdown = {"legal": 0.0, "commercial": 0.0, "operational": 0.0}
    weights = risk_weights(profile, scoring_profile=scoring_profile)
    thresholds = decision_thresholds(profile, scoring_profile=scoring_profile)
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
        evidence = {
            "triggered_phrases": [d.get("match") for d in hit_details if d.get("match")],
            "paragraph_index": paragraph_index,
            "heading": clause_heading,
            "rule_patterns": summarize_rule_patterns(keyword_hits, rf_trigger_hits),
            "confidence_score": confidence_score(keyword_hits, rf_trigger_hits, snippet),
        }

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
            "confidence_score": evidence["confidence_score"],
            "evidence": evidence,
            "posture_suggestion": suggest_posture(clause, profile),
        })

    decision = "approve"
    if risk_score > float(thresholds.get("escalate_max", 9.99)):
        decision = "block"
    elif risk_score > float(thresholds.get("approve_max", 4.99)):
        decision = "escalate"

    result = {
        "decision": decision,
        "risk_score": round(risk_score, 2),
        "risk_breakdown": {k: round(v, 2) for k, v in breakdown.items()},
        "risk_weights": weights,
        "decision_thresholds": thresholds,
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
                "confidence_score": f.get("confidence_score"),
                "evidence": f.get("evidence", {}),
                "posture_suggestion": f.get("posture_suggestion", ""),
            }
            for i, f in enumerate(findings)
        ],
    }
    if scoring_profile:
        result["scoring_profile"] = {
            "name": scoring_profile.get("name"),
            "path": scoring_profile.get("path"),
        }
    if explainability:
        result["explainability_mode"] = True
    return result


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


REDLINE_TEMPLATES = {
    "term_and_survival": "Replace the survival language with a finite confidentiality term and limit any indefinite protection to trade secrets only.",
    "exceptions": "Insert the standard confidentiality carve-outs for public information, prior knowledge, independent development, lawful third-party receipt, and legal compulsion.",
    "liability_and_remedies": "Narrow remedies to targeted injunctive relief and remove any uncapped indemnity or liability expansion embedded in the NDA.",
}


def exact_replacement_suggestion(concern):
    snippet = (concern.get("clause_snippet") or "").strip()
    clause = concern.get("clause")
    if snippet and len(snippet) <= 500:
        return {
            "replace_this": snippet,
            "with_text": concern.get("recommended_amendment") or concern.get("recommendation") or "",
        }
    return {
        "replace_this": "",
        "with_text": REDLINE_TEMPLATES.get(clause, concern.get("recommended_amendment") or concern.get("recommendation") or ""),
    }


def redline_rationale(concern):
    rationale = concern.get("concern") or "Clause deviates from the configured preferred position."
    confidence = concern.get("confidence_score")
    if confidence is not None:
        return f"{rationale} Confidence {confidence}."
    return rationale


def build_redline_v2(review):
    lines = ["# Clause-specific Redline Draft v2", ""]
    items = review.get("concerns_summary", [])
    for c in items:
        decision = str(c.get("pass2_decision", "")).upper()
        if decision and decision not in {"CONFIRM", "DOWNGRADE"}:
            continue
        replacement = exact_replacement_suggestion(c)
        clause = c.get("clause")
        template = REDLINE_TEMPLATES.get(clause, c.get("recommended_amendment") or c.get("recommendation") or "")
        lines.append(f"## {c.get('point')}. {clause}")
        lines.append(f"- Severity: {c.get('severity')}")
        lines.append(f"- Rationale: {redline_rationale(c)}")
        if c.get("clause_heading") or c.get("paragraph_index") is not None:
            loc = []
            if c.get("clause_heading"):
                loc.append(f"heading={c.get('clause_heading')}")
            if c.get("paragraph_index") is not None:
                loc.append(f"paragraph={c.get('paragraph_index')}")
            lines.append(f"- Location: {', '.join(loc)}")
        if replacement.get("replace_this"):
            lines.append(f"- Exact replacement target: \"{replacement['replace_this']}\"")
        lines.append("- Suggested replacement text block:")
        lines.append("")
        lines.append(replacement["with_text"] or template or "Review clause manually and replace with the configured preferred position.")
        lines.append("")
        lines.append("- Reusable amendment template:")
        lines.append(f"  {template or 'Align this clause with the configured preferred position and keep the edit clause-local.'}")
        lines.append("")
    return "\n".join(lines)


def cmd_generate_redlines(args):
    review = json.loads(Path(args.review_json).read_text())
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    if args.mode == "v2":
        out.write_text(build_redline_v2(review))
        print(out)
        return
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


def _print_friendly(title, lines, next_steps=None):
    """Write a human-readable summary to stderr.

    Stdout stays machine-parseable (JSON). Suppressed when NDA_CLI_QUIET=1
    or stderr is not a tty and NDA_CLI_FORCE_FRIENDLY is unset.
    """
    if os.environ.get("NDA_CLI_QUIET") == "1":
        return
    if not sys.stderr.isatty() and os.environ.get("NDA_CLI_FORCE_FRIENDLY") != "1":
        return
    bar = "─" * max(len(title) + 4, 32)
    print(f"\n  {title}", file=sys.stderr)
    print(f"  {bar}", file=sys.stderr)
    for line in lines:
        print(f"  {line}", file=sys.stderr)
    if next_steps:
        print(f"\n  Next steps:", file=sys.stderr)
        for i, step in enumerate(next_steps, 1):
            print(f"    {i}. {step}", file=sys.stderr)
    print("", file=sys.stderr)


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
    scoring_profiles_out = cfg_dir / "scoring-profiles.json"
    if not scoring_profiles_out.exists():
        scoring_profiles_out.write_text(json.dumps({"profiles": DEFAULT_SCORING_PROFILES}, indent=2, ensure_ascii=False))

    scoring_profile = scoring_profile_details(base, args.scoring_profile or posture, args.scoring_profiles)
    weights = scoring_profile["weights"]

    org_policy = {
        "version": "0.2.0",
        "org_name": org_name,
        "risk_posture": posture,
        "scoring_profile": scoring_profile["name"],
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
        "decision_thresholds": scoring_profile["decision_thresholds"],
        "scoring_profile": scoring_profile["name"],
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
    _print_friendly(
        title=f"Initialized NDA Review CLI for {org_name}",
        lines=[
            f"Org policy:      {org_out}",
            f"Default profile: {prof_out}",
            f"Risk posture:    {posture}   |   Scoring profile: {scoring_profile['name']}",
            f"Template:        {getattr(args, 'template', None) or '(none)'}",
        ],
        next_steps=[
            "Edit `config/org-policy.json` to refine clause rules for your house style.",
            "Run `./nda_review_cli.py ingest` to feed in past contracts (or use `--contracts-dir`).",
            "Run `./nda_review_cli.py build-playbook` to compile your playbook.",
            "Run `./nda_review_cli.py doctor` if anything seems off.",
        ],
    )


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


def discover_files_from_root(root: Path):
    exts = {".txt", ".md", ".docx", ".pdf"}
    found = []
    if not root.exists() or not root.is_dir():
        return found
    for p in root.rglob("*"):
        if p.is_file() and p.suffix.lower() in exts and not p.name.startswith("."):
            found.append(str(p))
    return sorted(set(found))


def discover_drive_export_files(root: Path):
    candidates = []
    for name in ["My Drive", "Shared drives", "Takeout", "Google Drive"]:
        target = root / name
        candidates.extend(discover_files_from_root(target if target.exists() else root))
        if candidates:
            break
    return sorted(set(candidates))


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

    files = list(args.files or [])
    if args.contracts_dir:
        files.extend(discover_files_from_root(Path(args.contracts_dir)))
    if args.drive_export_dir:
        files.extend(discover_drive_export_files(Path(args.drive_export_dir)))

    resolution = _resolve_ingest_files(
        base,
        files,
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
        "connector_inputs": {
            "contracts_dir": args.contracts_dir,
            "drive_export_dir": args.drive_export_dir,
        },
        "note": "Proposed-only. Review before promotion to active policy/profile.",
    }
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    print(json.dumps({
        "ok": True,
        "suggestions_file": str(out),
        "sources_ingested": len(sources),
        "autodiscovered": resolution["autodiscovered"],
        "skipped_for_approval": resolution["skipped_for_approval"],
        "connector_inputs": payload["connector_inputs"],
    }, ensure_ascii=False))
    failed = [s for s in sources if s.get("extraction_status") == "failed"]
    summary_lines = [
        f"Sources ingested: {len(sources)}",
        f"Clause suggestions emitted: {len(suggestions)}",
        f"Suggestions file: {out}",
    ]
    if resolution["skipped_for_approval"]:
        summary_lines.append("Some auto-discovered files were skipped pending approval (re-run with --yes to accept).")
    if failed:
        summary_lines.append(f"Extraction failed for {len(failed)} file(s) — install pdftotext or convert to .txt/.md.")
    next_steps = ["Review suggestions in `knowledge/proposed/` before promoting them to active policy."]
    if not sources:
        next_steps = [
            "Drop contracts into `knowledge/inbox/` or pass `--contracts-dir <dir>` and re-run ingest.",
            "Or run `./nda_review_cli.py doctor` to see what was discovered.",
        ]
    else:
        next_steps.append("Run `./nda_review_cli.py build-playbook` to compile a fresh playbook.")
    _print_friendly(
        title="Ingest complete",
        lines=summary_lines,
        next_steps=next_steps,
    )


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
    init_args.scoring_profile = args.scoring_profile
    init_args.scoring_profiles = args.scoring_profiles
    cmd_init(init_args)

    base = Path(args.base)
    explicit_ingest = list(args.ingest_files or [])
    if getattr(args, "contracts_dir", None):
        explicit_ingest.extend(discover_files_from_root(Path(args.contracts_dir)))
    if getattr(args, "drive_export_dir", None):
        explicit_ingest.extend(discover_drive_export_files(Path(args.drive_export_dir)))
    resolution = _resolve_ingest_files(
        base,
        explicit_ingest,
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
        ingest_args.contracts_dir = None
        ingest_args.drive_export_dir = None
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
    summary = [
        f"Base directory:  {base}",
        f"Org policy:      {base / 'config' / 'org-policy.json'}",
        f"Default profile: {base / 'profiles' / 'default.json'}",
        f"Ingest files:    {len(ingest_files)}",
        f"Build ran:       {'yes — ' + str(build_output) if build_output else 'no (rerun with --build to compile)'}",
    ]
    next_steps = [
        "Review a sample NDA: `./nda_review_cli.py review --file tests/fixtures/sample_nda.txt --why`",
        "Customize defaults: edit `config/org-policy.json`",
    ]
    if not ingest_files:
        next_steps.insert(
            0,
            "Add past contracts via `./nda_review_cli.py ingest --contracts-dir <dir>` for richer playbook signals.",
        )
    if not build_output:
        next_steps.append("Run `./nda_review_cli.py build-playbook` once you have ingest data.")
    next_steps.append("Run `./nda_review_cli.py doctor` to validate first-run readiness.")
    _print_friendly(
        title="Setup complete — you're ready to review",
        lines=summary,
        next_steps=next_steps,
    )


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
    # Corpus-free flow: if data/raw_strict doesn't exist at all, the user is on
    # Scenario A (no historical corpus). Don't flag missing gmail/drive inputs
    # as hard failures — those paths are only meaningful when corpus exists.
    raw_strict = base / "data" / "raw_strict"
    corpus_mode = raw_strict.exists() and any(raw_strict.iterdir()) if raw_strict.exists() else False
    data_checks = []
    for group, paths in expected.items():
        for p in paths:
            exists = p.exists()
            item = {"path": str(p), "exists": exists, "group": group, "corpus_mode": corpus_mode}
            if not exists:
                if corpus_mode:
                    hard_failures.append(f"Missing build-playbook input: {p}")
                    fixes.append(f"Create or point `{group}` to a JSON export file with `./nda_review_cli.py build-playbook --base {base} --{'gmail-paths' if group == 'gmail_paths' else 'drive-paths'} ...`.")
                # else: corpus-free flow — silent, no warning needed
            data_checks.append(item)
    if corpus_mode:
        check_status = "ok" if not [x for x in data_checks if not x["exists"]] else "fail"
    else:
        check_status = "skip"
    checks.append({
        "name": "build_playbook_paths",
        "status": check_status,
        "corpus_mode": corpus_mode,
        "details": data_checks,
        "note": None if corpus_mode else "Corpus-free setup detected (no data/raw_strict). Skipping gmail/drive path checks — review still works against config/org-policy.json clause rules.",
    })

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
    status_lines = []
    status_label = {"ok": "OK", "warn": "WARN", "fail": "FAIL", "skip": "SKIP"}
    for check in checks:
        label = status_label.get(check["status"], check["status"].upper())
        suffix = ""
        if check["status"] == "skip" and check.get("note"):
            suffix = f" — {check['note']}"
        status_lines.append(f"[{label:4}] {check['name']}{suffix}")
    if not hard_failures and not warnings:
        status_lines.append("All onboarding checks passed.")
    next_steps = []
    if hard_failures:
        next_steps.extend(payload["suggested_fixes"][:5])
    elif warnings:
        next_steps.extend(payload["suggested_fixes"][:3])
        next_steps.append("Optional: drop contracts into `knowledge/inbox/` to enrich the playbook.")
    else:
        next_steps = [
            "Run `./nda_review_cli.py review --file tests/fixtures/sample_nda.txt --why` to verify the review pipeline.",
            "Run `./nda_review_cli.py build-playbook` whenever you change policy or ingest new contracts.",
        ]
    _print_friendly(
        title="Doctor report" + ("" if not hard_failures else " (issues found)"),
        lines=status_lines,
        next_steps=next_steps if next_steps else None,
    )
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


# ----------------------------------------------------------------------------
# Optional LLM augmentation (opt-in via --llm).
# Adapters use stdlib urllib only — no anthropic/openai SDK dependency.
# ----------------------------------------------------------------------------

import urllib.request
import urllib.error

LLM_PROVIDER_PRESETS = {
    "anthropic": {
        "base_url": "https://api.anthropic.com/v1",
        "default_model": "claude-sonnet-4-6",
    },
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "default_model": "gpt-4o-mini",
    },
    "ollama": {
        "base_url": "http://localhost:11434/v1",
        "default_model": "qwen2.5:14b",
        "default_api_key": "ollama",
    },
    "openai-compatible": {
        "base_url": None,
        "default_model": None,
    },
}


def load_llm_config(base: Path, args) -> dict:
    """Resolution order: CLI args > env vars > config/llm.json > preset defaults."""
    cfg = {"provider": None, "model": None, "base_url": None, "api_key": None}
    cfg_path = base / "config" / "llm.json"
    if cfg_path.exists():
        try:
            file_cfg = json.loads(cfg_path.read_text())
            for k in ("provider", "model", "base_url", "api_key"):
                if file_cfg.get(k):
                    cfg[k] = file_cfg[k]
        except Exception as e:
            raise SystemExit(f"Could not parse {cfg_path}: {e}")

    env_map = {
        "provider": "NDA_LLM_PROVIDER",
        "model": "NDA_LLM_MODEL",
        "base_url": "NDA_LLM_BASE_URL",
        "api_key": "NDA_LLM_API_KEY",
    }
    for cfg_key, env_key in env_map.items():
        v = os.environ.get(env_key)
        if v:
            cfg[cfg_key] = v

    cli_provider = getattr(args, "llm", None)
    if cli_provider and cli_provider != "auto":
        if cfg.get("provider") and cfg["provider"] != cli_provider:
            # Switching provider on the CLI: clear provider-specific fields so
            # the new provider's preset fills them in (avoids sending an
            # Anthropic model name to Ollama, etc.).
            cfg["model"] = None
            cfg["base_url"] = None
        cfg["provider"] = cli_provider
    if getattr(args, "llm_model", None):
        cfg["model"] = args.llm_model
    if getattr(args, "llm_base_url", None):
        cfg["base_url"] = args.llm_base_url

    provider = cfg.get("provider")
    if provider in LLM_PROVIDER_PRESETS:
        preset = LLM_PROVIDER_PRESETS[provider]
        if not cfg["base_url"] and preset.get("base_url"):
            cfg["base_url"] = preset["base_url"]
        if not cfg["model"] and preset.get("default_model"):
            cfg["model"] = preset["default_model"]
        if not cfg["api_key"] and preset.get("default_api_key"):
            cfg["api_key"] = preset["default_api_key"]

    return cfg


def _llm_http_post(url: str, body: dict, headers: dict, timeout: int = 120) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body_txt = e.read().decode("utf-8", errors="replace")[:500]
        raise SystemExit(f"LLM HTTP {e.code}: {body_txt}")
    except urllib.error.URLError as e:
        raise SystemExit(f"LLM transport error: {e.reason}")


def llm_call_anthropic(cfg: dict, system: str, user: str, max_tokens: int = 4096) -> dict:
    if not cfg.get("api_key"):
        raise SystemExit("Anthropic provider requires api_key (env NDA_LLM_API_KEY or config/llm.json).")
    url = cfg["base_url"].rstrip("/") + "/messages"
    headers = {
        "x-api-key": cfg["api_key"],
        "anthropic-version": "2023-06-01",
    }
    body = {
        "model": cfg["model"],
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }
    raw = _llm_http_post(url, body, headers)
    text = "".join(part.get("text", "") for part in raw.get("content", []) if part.get("type") == "text")
    usage = raw.get("usage", {}) or {}
    return {
        "text": text,
        "model": raw.get("model"),
        "usage": {
            "input_tokens": usage.get("input_tokens"),
            "output_tokens": usage.get("output_tokens"),
        },
    }


def llm_call_openai_compatible(cfg: dict, system: str, user: str, max_tokens: int = 4096) -> dict:
    if not cfg.get("base_url"):
        raise SystemExit("OpenAI-compatible providers require base_url (env NDA_LLM_BASE_URL or config/llm.json).")
    url = cfg["base_url"].rstrip("/") + "/chat/completions"
    headers = {}
    if cfg.get("api_key"):
        headers["Authorization"] = f"Bearer {cfg['api_key']}"
    body = {
        "model": cfg["model"],
        "max_tokens": max_tokens,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "response_format": {"type": "json_object"},
    }
    raw = _llm_http_post(url, body, headers)
    choices = raw.get("choices") or []
    text = ""
    if choices:
        msg = choices[0].get("message") or {}
        text = msg.get("content") or ""
    usage = raw.get("usage", {}) or {}
    return {
        "text": text,
        "model": raw.get("model"),
        "usage": {
            "input_tokens": usage.get("prompt_tokens"),
            "output_tokens": usage.get("completion_tokens"),
        },
    }


def llm_call(cfg: dict, system: str, user: str, max_tokens: int = 4096) -> dict:
    provider = (cfg.get("provider") or "").lower()
    if provider == "anthropic":
        return llm_call_anthropic(cfg, system, user, max_tokens)
    if provider in ("openai", "ollama", "openai-compatible"):
        return llm_call_openai_compatible(cfg, system, user, max_tokens)
    raise SystemExit(
        f"Unknown LLM provider: {provider!r}. "
        "Supported: anthropic, openai, ollama, openai-compatible."
    )


LLM_REVIEW_SYSTEM_PROMPT = (
    "You are an experienced NDA reviewer assisting a deterministic rule engine. "
    "Your job is to (1) vote on each rule-engine finding, (2) add findings the rules missed, "
    "and (3) suggest replacement clause language for high-severity issues. "
    "Reply ONLY with a single JSON object matching this schema:\n"
    "{\n"
    '  "votes": [{"finding_index": int, "vote": "agree"|"soften"|"escalate"|"drop", "rationale": str}],\n'
    '  "additional_findings": [{"clause": str, "severity": "high"|"low", "concern": str, "evidence": str}],\n'
    '  "clause_suggestions": [{"clause": str, "suggested_text": str, "reason": str}]\n'
    "}\n"
    "Do not include any commentary outside the JSON object."
)


def _build_llm_review_user_prompt(text: str, result: dict, max_chars: int = 50000) -> str:
    nda = text if len(text) <= max_chars else (text[:max_chars] + "\n[...truncated for length...]")
    findings_summary = []
    for i, f in enumerate(result.get("findings", [])):
        findings_summary.append({
            "finding_index": i,
            "clause": f.get("clause"),
            "severity": f.get("severity"),
            "concern": f.get("concern") or f.get("preferred_position"),
            "snippet": (f.get("clause_snippet") or "")[:300],
        })
    return (
        "## NDA TEXT\n\n"
        f"{nda}\n\n"
        "## DETERMINISTIC FINDINGS (from rule engine)\n\n"
        f"{json.dumps(findings_summary, ensure_ascii=False, indent=2)}\n\n"
        "Reply with the JSON schema described in the system prompt."
    )


def _parse_llm_review_response(text: str) -> dict:
    """Defensive JSON parse: tolerate code fences and surrounding prose."""
    if not text:
        return {"votes": [], "additional_findings": [], "clause_suggestions": [], "_parse_error": "empty response"}
    candidate = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", candidate, re.S)
    if fence:
        candidate = fence.group(1)
    else:
        first = candidate.find("{")
        last = candidate.rfind("}")
        if first != -1 and last != -1 and last > first:
            candidate = candidate[first : last + 1]
    try:
        parsed = json.loads(candidate)
    except Exception as e:
        return {
            "votes": [],
            "additional_findings": [],
            "clause_suggestions": [],
            "_parse_error": f"json decode failed: {e}",
            "_raw_text": text[:1000],
        }
    return {
        "votes": parsed.get("votes") or [],
        "additional_findings": parsed.get("additional_findings") or [],
        "clause_suggestions": parsed.get("clause_suggestions") or [],
    }


def _confirm_llm_send(cfg: dict, yes: bool) -> None:
    if yes or os.environ.get("NDA_LLM_NO_CONFIRM") == "1":
        return
    if not sys.stderr.isatty() or not sys.stdin.isatty():
        # Non-interactive without explicit consent — fail closed.
        raise SystemExit(
            "Refusing to send NDA text to an LLM in a non-interactive context "
            "without explicit consent. Pass --yes-llm-send or set NDA_LLM_NO_CONFIRM=1."
        )
    print(
        f"\n  About to send NDA text to provider={cfg.get('provider')} "
        f"base_url={cfg.get('base_url')} model={cfg.get('model')}.",
        file=sys.stderr,
    )
    print("  Press Enter to continue, or Ctrl-C to abort.", file=sys.stderr)
    try:
        input()
    except (EOFError, KeyboardInterrupt):
        raise SystemExit("Aborted by user.")


def llm_augment_review(result: dict, source_text: str, cfg: dict, yes_send: bool) -> dict:
    if not cfg.get("provider"):
        raise SystemExit(
            "--llm requires a provider. Set it in config/llm.json, env NDA_LLM_PROVIDER, "
            "or pass --llm <anthropic|openai|ollama|openai-compatible>."
        )
    if not cfg.get("model"):
        raise SystemExit("LLM model not set. Provide via config/llm.json, NDA_LLM_MODEL, or --llm-model.")
    _confirm_llm_send(cfg, yes_send)
    user_prompt = _build_llm_review_user_prompt(source_text, result)
    raw = llm_call(cfg, LLM_REVIEW_SYSTEM_PROMPT, user_prompt)
    parsed = _parse_llm_review_response(raw["text"])
    return {
        "provider": cfg.get("provider"),
        "model": raw.get("model") or cfg.get("model"),
        "base_url": cfg.get("base_url"),
        "usage": raw.get("usage", {}),
        "votes": parsed.get("votes", []),
        "additional_findings": parsed.get("additional_findings", []),
        "clause_suggestions": parsed.get("clause_suggestions", []),
        "parse_error": parsed.get("_parse_error"),
    }


def cmd_review(args):
    playbook = json.loads(Path(args.playbook).read_text())
    base = Path(args.base)
    profile = load_counterparty_profile(base, args.counterparty)
    scoring_profile = scoring_profile_details(base, args.scoring_profile or (profile.get("scoring_profile") if profile else None), args.scoring_profiles)
    if args.file:
        text = Path(args.file).read_text(errors="ignore")
    else:
        text = args.text or ""
    result = review_text(text, playbook, profile=profile, scoring_profile=scoring_profile, explainability=args.why)
    if args.file:
        result["input_file"] = args.file
    if args.counterparty:
        result["counterparty"] = args.counterparty
    if profile:
        result["counterparty_profile_loaded"] = True

    learning_result = None
    if args.learn_profile:
        if not args.counterparty:
            raise SystemExit("--learn-profile requires --counterparty")
        source_review_file = args.out_json or "(stdout-only-review)"
        learning_result = learn_profile_from_review(base, args.counterparty, result, source_review_file)
        result["profile_learning"] = learning_result

    if getattr(args, "llm", None):
        llm_cfg = load_llm_config(base, args)
        result["llm_annotations"] = llm_augment_review(result, text, llm_cfg, getattr(args, "yes_llm_send", False))
        result["llm_used"] = True

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
            lines.append(f"- Confidence: {f.get('confidence_score',0)}")
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
            if args.why and f.get("evidence"):
                ev = f["evidence"]
                triggers = ", ".join(ev.get("triggered_phrases", [])[:3]) or "n/a"
                patterns = ", ".join(ev.get("rule_patterns", [])[:4]) or "n/a"
                lines.append(f"- Evidence: triggers={triggers}; patterns={patterns}; heading={ev.get('heading') or 'n/a'}; paragraph={ev.get('paragraph_index')}; confidence={ev.get('confidence_score')}")
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
            if args.why and c.get("evidence"):
                ev = c["evidence"]
                lines.append(f"- Evidence: phrases={', '.join(ev.get('triggered_phrases', [])[:3]) or 'n/a'}; patterns={', '.join(ev.get('rule_patterns', [])[:4]) or 'n/a'}; confidence={ev.get('confidence_score')}")
            lines.append(f"- Recommended amendment: {c.get('recommended_amendment')}")
            lines.append("")
        if learning_result:
            lines += ["", "## Profile Learning", "", f"- Profile updated: `{learning_result['profile_path']}`", f"- Changed fields: {', '.join(learning_result['changed_fields'])}"]
        if result.get("llm_annotations"):
            ann = result["llm_annotations"]
            lines += [
                "",
                "## LLM Annotations (opt-in, second-pass)",
                "",
                f"- Provider: `{ann.get('provider')}` · model: `{ann.get('model')}`",
                f"- Tokens: in={ann.get('usage', {}).get('input_tokens')} / out={ann.get('usage', {}).get('output_tokens')}",
            ]
            if ann.get("parse_error"):
                lines.append(f"- _Parse error_: {ann['parse_error']} (raw text preserved in JSON)")
            if ann.get("votes"):
                lines += ["", "### Votes on rule-engine findings", ""]
                for v in ann["votes"]:
                    idx = v.get("finding_index")
                    clause = ""
                    if isinstance(idx, int) and 0 <= idx < len(result.get("findings", [])):
                        clause = result["findings"][idx].get("clause", "")
                    lines.append(f"- _(LLM)_ finding #{idx} `{clause}` → **{v.get('vote')}** — {v.get('rationale','')}")
            if ann.get("additional_findings"):
                lines += ["", "### Additional findings (LLM)", ""]
                for af in ann["additional_findings"]:
                    lines.append(f"- _(LLM)_ **{af.get('clause','')}** ({af.get('severity','')}): {af.get('concern','')}")
                    if af.get("evidence"):
                        lines.append(f"  - Evidence: {af['evidence']}")
            if ann.get("clause_suggestions"):
                lines += ["", "### Suggested replacement clause language (LLM)", ""]
                for cs in ann["clause_suggestions"]:
                    lines.append(f"- _(LLM)_ **{cs.get('clause','')}** — {cs.get('reason','')}")
                    if cs.get("suggested_text"):
                        lines.append(f"  ```")
                        lines.append(f"  {cs['suggested_text']}")
                        lines.append(f"  ```")
        out_md.write_text("\n".join(lines))

    print(json.dumps(result, indent=2, ensure_ascii=False))


def cmd_profile_learn(args):
    base = Path(args.base)
    review_data = json.loads(Path(args.review_json).read_text())
    counterparty = args.counterparty or review_data.get("counterparty")
    if not counterparty:
        raise SystemExit("Counterparty required via --counterparty or embedded in review JSON.")
    learning_result = learn_profile_from_review(base, counterparty, review_data, args.review_json)
    print(json.dumps({"ok": True, **learning_result}, indent=2, ensure_ascii=False))


def normalize_decision_bucket(value):
    value = (value or "").strip().lower()
    return value if value in {"approve", "escalate", "block"} else "unknown"


def cmd_calibrate_scoring(args):
    base = Path(args.base)
    playbook = json.loads(Path(args.playbook).read_text())
    scoring_profile = scoring_profile_details(base, args.scoring_profile, args.scoring_profiles)
    cases = json.loads(Path(args.validation_set).read_text())
    confusion = {actual: {"approve": 0, "escalate": 0, "block": 0, "unknown": 0} for actual in ["approve", "escalate", "block", "unknown"]}
    predicted_counts = {"approve": 0, "escalate": 0, "block": 0, "unknown": 0}
    actual_counts = {"approve": 0, "escalate": 0, "block": 0, "unknown": 0}
    correct = 0
    for case in cases:
        if case.get("file"):
            text = Path(case["file"]).read_text(errors="ignore")
        else:
            text = case.get("text", "")
        actual = normalize_decision_bucket(case.get("expected_decision"))
        result = review_text(text, playbook, scoring_profile=scoring_profile, explainability=False)
        predicted = normalize_decision_bucket(result.get("decision"))
        actual_counts[actual] += 1
        predicted_counts[predicted] += 1
        confusion[actual][predicted] += 1
        if actual == predicted:
            correct += 1

    total = max(len(cases), 1)
    precision = {}
    recall = {}
    for bucket in ["approve", "escalate", "block"]:
        tp = confusion[bucket][bucket]
        pred = predicted_counts[bucket]
        act = actual_counts[bucket]
        precision[bucket] = round(tp / pred, 3) if pred else None
        recall[bucket] = round(tp / act, 3) if act else None

    payload = {
        "validation_set": args.validation_set,
        "scoring_profile": scoring_profile["name"],
        "scoring_profiles_path": scoring_profile["path"],
        "cases": len(cases),
        "accuracy": round(correct / total, 3),
        "decision_precision": precision,
        "decision_recall": recall,
        "actual_counts": actual_counts,
        "predicted_counts": predicted_counts,
        "confusion_by_decision_bucket": confusion,
    }
    if args.out_json:
        out = Path(args.out_json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    print(json.dumps(payload, indent=2, ensure_ascii=False))


def cmd_release_helper(args):
    changelog = Path(args.changelog)
    text = changelog.read_text() if changelog.exists() else ""
    version = args.version.strip()
    section = ""
    if version:
        m = re.search(rf"^##\s+\[{re.escape(version)}\].*?(?=^##\s+\[|\Z)", text, re.M | re.S)
        if m:
            section = m.group(0).strip()
    if not section:
        m = re.search(r"^##\s+\[[^\]]+\].*?(?=^##\s+\[|\Z)", text, re.M | re.S)
        section = m.group(0).strip() if m else "No changelog section found."
    payload = {
        "version": version,
        "changelog": str(changelog),
        "release_notes": section,
        "suggested_tag": f"git tag -a {version} -m \"Release {version}\"" if version else "",
    }
    if args.out:
        Path(args.out).write_text(section + "\n")
    print(json.dumps(payload, indent=2, ensure_ascii=False))


def _prompt_yes_no(label, default=True):
    marker = "Y/n" if default else "y/N"
    raw = input(f"{label} [{marker}]: ").strip().lower()
    if not raw:
        return default
    return raw in {"y", "yes"}


QUICKSTART_DEFAULTS = {
    "org_name": "Your Org",
    "org_type": "other",
    "org_type_label": "",
    "role": "in-house",
    "nda_direction": "mutual",
    "risk_posture": "balanced",
    "preferred_jurisdictions": "Austria",
    "survival_years": 5,
    "ai_policy": "guardrailed",
    "nda_term_years": 2,
    "return_or_destroy_pref": "either",
    "residual_knowledge": "reject",
    "trade_secret_indefinite": True,
    "affiliate_disclosure": "advisors_only",
    "negotiation_stance": "middleground",
    "clause_priorities": [],
}

QUICKSTART_ORG_TYPES = ["saas", "healthcare", "enterprise", "other"]
QUICKSTART_ROLES = ["in-house", "founder", "engineer", "ops", "external", "other"]
QUICKSTART_DIRECTIONS = ["mutual", "disclosing", "receiving", "mixed"]
QUICKSTART_POSTURES = ["strict", "balanced", "commercial"]
QUICKSTART_AI = ["restricted", "guardrailed", "permissive"]
QUICKSTART_RND = ["return", "destroy", "either", "either_with_certification"]
QUICKSTART_RESIDUAL = ["accept", "reject"]
QUICKSTART_AFFILIATE = ["advisors_only", "advisors_and_affiliates", "case_by_case"]
QUICKSTART_STANCES = ["conservative", "middleground", "compromising"]


def _apply_quickstart_to_clause_rules(clause_rules: dict, ans: dict) -> dict:
    """Mutate a copy of clause_rules based on quickstart answers. Each branch
    must change observable review output — no decorative metadata."""
    out = json.loads(json.dumps(clause_rules))

    # term_and_survival: term length + trade-secret indefinite carve-out
    term_years = int(ans.get("nda_term_years", 2))
    survival_years = int(ans.get("survival_years", 5))
    trade_secret = bool(ans.get("trade_secret_indefinite", True))
    if "term_and_survival" in out:
        carve = " Trade-secret protection extends indefinitely." if trade_secret else " No indefinite carve-out for trade secrets."
        out["term_and_survival"]["preferred"] = (
            f"NDA term {term_years} year(s) with confidentiality survival of {survival_years} year(s)."
            f"{carve}"
        )
        flags = list(out["term_and_survival"].get("red_flags", []))
        if not trade_secret and "indefinite trade-secret protection" not in flags:
            flags.append("indefinite trade-secret protection")
        if "perpetual for all info" not in flags:
            flags.append("perpetual for all info")
        out["term_and_survival"]["red_flags"] = flags

    # return_or_destroy: preference text drives review messaging
    rnd = ans.get("return_or_destroy_pref", "either")
    if "return_or_destroy" in out:
        rnd_text = {
            "return": "Prefer return of confidential materials on request, with limited backup/legal retention carve-out.",
            "destroy": "Prefer destruction of confidential materials on request, with limited backup/legal retention carve-out.",
            "either": "Allow return or destruction on request, with limited backup/legal retention carve-out.",
            "either_with_certification": "Allow return or destruction on request, with written certification of destruction and limited backup/legal retention carve-out.",
        }.get(rnd, out["return_or_destroy"]["preferred"])
        out["return_or_destroy"]["preferred"] = rnd_text
        flags = list(out["return_or_destroy"].get("red_flags", []))
        if rnd == "either_with_certification" and "no destruction certification" not in flags:
            flags.append("no destruction certification")
        out["return_or_destroy"]["red_flags"] = flags

    # residuals: stance flips red flags directly
    residual = ans.get("residual_knowledge", "reject")
    if "residuals" in out:
        if residual == "reject":
            out["residuals"]["preferred"] = "Reject residual-knowledge clauses; mental impressions used to compete still constitute a breach."
            flags = list(out["residuals"].get("red_flags", []))
            for trigger in ["residual knowledge", "retained in unaided memory", "residuals clause"]:
                if trigger not in flags:
                    flags.append(trigger)
            out["residuals"]["red_flags"] = flags
        else:
            out["residuals"]["preferred"] = "Accept narrowly-scoped residual knowledge limited to information unintentionally retained in unaided memory."
            out["residuals"]["red_flags"] = []

    # assignment_and_affiliates: scope of permitted disclosure
    affiliate = ans.get("affiliate_disclosure", "advisors_only")
    if "assignment_and_affiliates" in out:
        text = {
            "advisors_only": "Permitted disclosure limited to advisors (legal, accounting, financial) bound by equivalent confidentiality.",
            "advisors_and_affiliates": "Permitted disclosure to advisors and affiliates under equivalent confidentiality, with disclosing party remaining responsible.",
            "case_by_case": "Permitted disclosure only with disclosing party's prior written consent on a case-by-case basis.",
        }.get(affiliate, out["assignment_and_affiliates"]["preferred"])
        out["assignment_and_affiliates"]["preferred"] = text
        flags = list(out["assignment_and_affiliates"].get("red_flags", []))
        if affiliate == "advisors_only" and "broad affiliate disclosure" not in flags:
            flags.append("broad affiliate disclosure")
        out["assignment_and_affiliates"]["red_flags"] = flags

    return out


def _quickstart_summary_lines(ans: dict) -> list:
    org_label = ans["org_type"] if ans["org_type"] != "other" else f"other ({ans.get('org_type_label') or 'unspecified'})"
    return [
        f"Org name:                {ans['org_name']}",
        f"Org type:                {org_label}",
        f"Your role:               {ans['role']}",
        f"NDA direction:           {ans['nda_direction']}",
        f"Risk posture:            {ans['risk_posture']}",
        f"Preferred jurisdictions: {ans['preferred_jurisdictions']}",
        f"Confidentiality survival:{ans['survival_years']} year(s)",
        f"AI/data stance:          {ans['ai_policy']}",
        f"NDA term length:         {ans['nda_term_years']} year(s)",
        f"Return-or-destroy pref:  {ans['return_or_destroy_pref']}",
        f"Residual knowledge:      {ans['residual_knowledge']}",
        f"Trade-secret indefinite: {'yes' if ans['trade_secret_indefinite'] else 'no'}",
        f"Affiliate disclosure:    {ans['affiliate_disclosure']}",
        f"Negotiation stance:      {ans['negotiation_stance']}",
    ]


def cmd_quickstart(args):
    base = Path(args.base)
    cfg_dir = base / "config"
    prof_dir = base / "profiles"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    prof_dir.mkdir(parents=True, exist_ok=True)

    interactive = sys.stdin.isatty() and not args.no_prompt

    ans = dict(QUICKSTART_DEFAULTS)
    if args.answers_file:
        try:
            ans.update(json.loads(Path(args.answers_file).read_text()))
        except Exception as e:
            print(f"Could not load answers file: {e}", file=sys.stderr)
            raise SystemExit(2)

    if interactive:
        total = 16
        def step(n, label):
            print(f"\n  ({n}/{total}) {label}", file=sys.stderr)

        step(1, "Organization name — appears in playbook + profile metadata.")
        ans["org_name"] = _prompt_with_default("Org name", ans["org_name"])

        step(2, "Org type — picks template clause_preferences (saas/healthcare/enterprise) or annotation only (other).")
        ans["org_type"] = _prompt_choice("Org type", QUICKSTART_ORG_TYPES, ans["org_type"])
        if ans["org_type"] == "other":
            ans["org_type_label"] = _prompt_with_default("Describe your org type (free text, e.g. developer, agency)", ans.get("org_type_label", ""))

        step(3, "Your role — surfaced in review headers so readers know the lens.")
        ans["role"] = _prompt_choice("Role", QUICKSTART_ROLES, ans["role"])

        step(4, "Typical NDA direction — review summary frames findings from your perspective.")
        ans["nda_direction"] = _prompt_choice("NDA direction", QUICKSTART_DIRECTIONS, ans["nda_direction"])

        step(5, "Risk posture — sets scoring profile + decision thresholds.")
        ans["risk_posture"] = _prompt_choice("Risk posture", QUICKSTART_POSTURES, ans["risk_posture"])

        step(6, "Preferred jurisdictions — comma-separated; review scores jurisdiction findings against this list.")
        ans["preferred_jurisdictions"] = _prompt_with_default("Jurisdictions (comma-separated)", ans["preferred_jurisdictions"])

        step(7, "Default confidentiality survival (years) — used in profile + term_and_survival preferred text.")
        ans["survival_years"] = int(_prompt_with_default("Survival years", str(ans["survival_years"])))

        step(8, "AI/data usage stance — weights AI-clause red flags during review.")
        ans["ai_policy"] = _prompt_choice("AI policy", QUICKSTART_AI, ans["ai_policy"])

        step(9, "NDA term length (years) — distinct from survival; how long the agreement itself runs.")
        ans["nda_term_years"] = int(_prompt_with_default("NDA term years", str(ans["nda_term_years"])))

        step(10, "Return vs destroy preference — drives return_or_destroy clause preferred text.")
        ans["return_or_destroy_pref"] = _prompt_choice("Return/destroy", QUICKSTART_RND, ans["return_or_destroy_pref"])

        step(11, "Residual knowledge stance — accept narrows the residuals clause; reject flips on red flags.")
        ans["residual_knowledge"] = _prompt_choice("Residual knowledge", QUICKSTART_RESIDUAL, ans["residual_knowledge"])

        step(12, "Trade-secret indefinite carve-out — adjusts term_and_survival preferred text.")
        ans["trade_secret_indefinite"] = _prompt_yes_no("Indefinite carve-out for trade secrets?", ans["trade_secret_indefinite"])

        step(13, "Affiliate/advisor disclosure scope — drives assignment_and_affiliates clause text + red flags.")
        ans["affiliate_disclosure"] = _prompt_choice("Affiliate disclosure", QUICKSTART_AFFILIATE, ans["affiliate_disclosure"])

        step(14, "Negotiation stance — shapes how your agent counters in `negotiate counter --agent` (and --auto deterministic mode).")
        ans["negotiation_stance"] = _prompt_choice("Negotiation stance", QUICKSTART_STANCES, ans["negotiation_stance"])

        step(15, "Clause priorities — list clauses from most-important (top) to least-important (bottom). Drives logrolling in negotiation: your agent will concede on bottom-K clauses by priority based on your stance.")
        # Default ordering: load default-policy.json clause keys.
        from pathlib import Path as _P
        seed = load_policy_config(base, args.default_policy)
        default_clauses = list((seed.get("clause_rules") or {}).keys())
        print(f"  Default order ({len(default_clauses)} clauses): " + ", ".join(default_clauses))
        raw = _prompt_with_default(
            "Priority order (comma-separated, top-down) or blank to keep default",
            "",
        )
        if raw.strip():
            user_order = [c.strip() for c in raw.split(",") if c.strip()]
            # Validate every entry is a known clause; preserve user order; append any missing in default order.
            unknown = [c for c in user_order if c not in default_clauses]
            if unknown:
                print(f"  Warning: unknown clause(s) {unknown}; ignoring those.")
                user_order = [c for c in user_order if c in default_clauses]
            for c in default_clauses:
                if c not in user_order:
                    user_order.append(c)
            ans["clause_priorities"] = user_order
        else:
            ans["clause_priorities"] = default_clauses

        step(16, "Past contracts to ingest now? Enter a directory path or leave blank to skip. We can also run a sample review at the end.")
        ans["contracts_dir"] = _prompt_with_default("Contracts dir (blank to skip)", ans.get("contracts_dir", "") or "").strip() or None
        ans["run_sample"] = _prompt_yes_no("Run a sample review on the bundled NDA fixture?", True)
    else:
        ans.setdefault("contracts_dir", None)
        ans.setdefault("run_sample", False)

    print("\n  ━━ Summary ━━", file=sys.stderr)
    for line in _quickstart_summary_lines(ans):
        print(f"  {line}", file=sys.stderr)
    if ans.get("contracts_dir"):
        print(f"  Ingest from:             {ans['contracts_dir']}", file=sys.stderr)
    if ans.get("run_sample"):
        print(f"  Sample review:           yes (tests/fixtures/sample_nda.txt)", file=sys.stderr)

    if interactive and not args.yes:
        if not _prompt_yes_no("\n  Apply this configuration?", True):
            print("Aborted. No files written.", file=sys.stderr)
            raise SystemExit(0)

    # Persist answers for reproducibility / CI replay.
    ans_out = cfg_dir / "quickstart-answers.json"
    ans_out.write_text(json.dumps(ans, indent=2, sort_keys=True, ensure_ascii=False))

    # Build org-policy from seed clause rules + quickstart augmentations.
    seed = load_policy_config(base, args.default_policy)
    augmented_rules = _apply_quickstart_to_clause_rules(seed["clause_rules"], ans)

    scoring_profiles_out = cfg_dir / "scoring-profiles.json"
    if not scoring_profiles_out.exists():
        scoring_profiles_out.write_text(json.dumps({"profiles": DEFAULT_SCORING_PROFILES}, indent=2, ensure_ascii=False))
    scoring_profile = scoring_profile_details(base, ans["risk_posture"], None)
    weights = scoring_profile["weights"]

    template_used = ans["org_type"] if ans["org_type"] in SUPPORTED_TEMPLATES else None
    org_type_label = ans.get("org_type_label") or ans["org_type"]

    org_policy = {
        "version": "0.2.0",
        "org_name": ans["org_name"],
        "risk_posture": ans["risk_posture"],
        "scoring_profile": scoring_profile["name"],
        "preferred_jurisdictions": _parse_csv(ans["preferred_jurisdictions"]),
        "defaults": {
            "survival_years": int(ans["survival_years"]),
            "ai_policy": ans["ai_policy"],
            "retention_carveout": "Allow limited backup/legal retention under continuing confidentiality obligations.",
            "nda_term_years": int(ans["nda_term_years"]),
            "return_or_destroy_pref": ans["return_or_destroy_pref"],
            "residual_knowledge": ans["residual_knowledge"],
            "trade_secret_indefinite": bool(ans["trade_secret_indefinite"]),
            "affiliate_disclosure": ans["affiliate_disclosure"],
            "negotiation_stance": ans["negotiation_stance"],
        },
        "risk_weights": weights,
        "clause_rules": augmented_rules,
        "clause_priorities": ans["clause_priorities"] or list(augmented_rules.keys()),
        "negotiation_signal_patterns": seed["negotiation_signal_patterns"],
        "org_type": org_type_label,
        "template": template_used,
    }

    profile = {
        "profile_name": "default",
        "fallback_posture": f"{ans['org_name']} prefers {ans['risk_posture']} posture as {ans['nda_direction']} party.",
        "role": ans["role"],
        "nda_direction": ans["nda_direction"],
        "org_type": org_type_label,
        "risk_weights": weights,
        "decision_thresholds": scoring_profile["decision_thresholds"],
        "scoring_profile": scoring_profile["name"],
        "clause_preferences": {
            "term_and_survival": augmented_rules.get("term_and_survival", {}).get("preferred", ""),
            "return_or_destroy": augmented_rules.get("return_or_destroy", {}).get("preferred", ""),
            "residuals": augmented_rules.get("residuals", {}).get("preferred", ""),
            "assignment_and_affiliates": augmented_rules.get("assignment_and_affiliates", {}).get("preferred", ""),
            "governing_law_jurisdiction": f"Prefer jurisdictions: {', '.join(_parse_csv(ans['preferred_jurisdictions'])) or 'neutral/favorable'}.",
        },
    }
    if template_used:
        profile["template"] = template_used
        profile["clause_preferences"].update(SUPPORTED_TEMPLATES[template_used]["clause_preferences"])

    org_out = cfg_dir / "org-policy.json"
    prof_out = prof_dir / "default.json"
    org_out.write_text(json.dumps(org_policy, indent=2, ensure_ascii=False))
    prof_out.write_text(json.dumps(profile, indent=2, ensure_ascii=False))

    result = {
        "ok": True,
        "org_policy": str(org_out),
        "default_profile": str(prof_out),
        "answers_file": str(ans_out),
        "ingest_ran": False,
        "sample_review_ran": False,
    }

    # Optional ingest from a directory.
    if ans.get("contracts_dir"):
        class Obj:
            pass
        ingest_args = Obj()
        ingest_args.base = str(base)
        ingest_args.policy = "config/org-policy.json"
        ingest_args.files = []
        ingest_args.contracts_dir = ans["contracts_dir"]
        ingest_args.drive_export_dir = None
        ingest_args.no_prompt = True
        ingest_args.yes = True
        cmd_ingest(ingest_args)
        result["ingest_ran"] = True

    # Optional sample review.
    if ans.get("run_sample"):
        sample = Path(__file__).resolve().parent / "tests" / "fixtures" / "sample_nda.txt"
        if sample.exists():
            # Need a playbook first. Build one from any seeded data; falls back
            # to clause_rules-only behavior when corpus is absent.
            class Obj:
                pass
            build_args = Obj()
            build_args.base = str(base)
            build_args.policy = None
            build_args.gmail_paths = ["data/raw_strict/gmail_primary.json", "data/raw_strict/gmail_secondary.json"]
            build_args.drive_paths = ["data/raw_strict/drive_primary.json", "data/raw_strict/drive_secondary.json"]
            build_args.out_json = "output/nda_playbook.json"
            build_args.out_md = "output/nda_playbook.md"
            cmd_build(build_args)

            review_out_json = base / "output" / "reviews" / "quickstart-review.json"
            review_out_md = base / "output" / "reviews" / "quickstart-review.md"
            review_out_json.parent.mkdir(parents=True, exist_ok=True)
            review_args = Obj()
            review_args.base = str(base)
            review_args.playbook = str(base / "output" / "nda_playbook.json")
            review_args.counterparty = None
            review_args.file = str(sample)
            review_args.text = None
            review_args.out_json = str(review_out_json)
            review_args.out_md = str(review_out_md)
            review_args.why = True
            review_args.learn_profile = False
            review_args.scoring_profile = None
            review_args.scoring_profiles = None
            cmd_review(review_args)
            result["sample_review_ran"] = True
            result["sample_review_json"] = str(review_out_json)
            result["sample_review_md"] = str(review_out_md)

    print(json.dumps(result, ensure_ascii=False))
    _print_friendly(
        title=f"Quickstart complete for {ans['org_name']}",
        lines=[
            f"Org policy:      {org_out}",
            f"Default profile: {prof_out}",
            f"Answers replay:  {ans_out}",
            f"Ingest ran:      {'yes' if result['ingest_ran'] else 'no'}",
            f"Sample review:   {'yes — ' + str(result.get('sample_review_md', '')) if result['sample_review_ran'] else 'no'}",
        ],
        next_steps=[
            "Open `config/org-policy.json` to fine-tune any clause rule.",
            "Replay non-interactively: `./nda_review_cli.py quickstart --no-prompt --yes --answers-file config/quickstart-answers.json`",
            "Run `./nda_review_cli.py review --file /path/to/nda.txt --why` on a real NDA.",
            "Run `./nda_review_cli.py doctor` to validate readiness.",
        ],
    )


TUTORIAL_STEPS = [
    {
        "title": "Welcome",
        "body": [
            "NDA Review CLI helps you review and draft NDAs against your own",
            "house policy. Deterministic by default; an opt-in second-pass LLM",
            "(--llm) can vote on findings, add missed ones, and suggest clause",
            "language. Without --llm, no contract text leaves the box.",
            "",
            "We'll walk through the three core artifacts and run a sample review.",
        ],
    },
    {
        "title": "Concept 1 — Policy",
        "body": [
            "The policy is your house rules: clause keywords, preferred language,",
            "red flags, risk weights. It lives in:",
            "  • config/default-policy.json   (committed seed, generic defaults)",
            "  • config/org-policy.json       (your local override, gitignored)",
            "",
            "You edit the policy. The CLI never silently rewrites it.",
        ],
    },
    {
        "title": "Concept 2 — Profile",
        "body": [
            "A profile is per-counterparty memory under profiles/<name>.json.",
            "It records typical positions, concessions, and escalation history.",
            "",
            "Pass `--counterparty \"Acme Corp\" --learn-profile` on review and the",
            "CLI updates profiles/Acme Corp.json deterministically.",
        ],
    },
    {
        "title": "Concept 3 — Playbook",
        "body": [
            "The playbook is the compiled artifact at output/nda_playbook.json.",
            "It's regenerated on demand from policy + corpus signals.",
            "",
            "Rule of thumb: edit the policy, let the profile learn,",
            "regenerate the playbook.",
        ],
    },
    {
        "title": "Hands-on — Sample review",
        "body": [
            "We'll set up a fresh workspace under a temp directory and run a",
            "review against the bundled sample NDA at:",
            "  tests/fixtures/sample_nda.txt",
            "",
            "This won't touch your existing config/ or profiles/.",
        ],
    },
    {
        "title": "Concept 4 — Two-party negotiation",
        "body": [
            "If both sides have this CLI, you can co-negotiate an NDA without",
            "any third-party service. The protocol is file-based: a single",
            "JSON state file bounces between parties (email/Drive/Git — your",
            "choice). Each round is signed by exactly one party.",
            "",
            "Each agent uses three policy fields to decide what to counter:",
            "  • negotiation_stance: conservative / middleground / compromising",
            "  • clause_priorities:  ranked top-to-bottom",
            "  • non_negotiable_clauses: hard floor — never conceded",
            "",
            "Stuck clauses are auto-resolved by `fatigue concession` after K",
            "bounces; truly non-negotiable conflicts surface as `blocked` for",
            "human escalation. Sign-off step gives you the key-points review",
            "before the agreed text is finalized.",
        ],
    },
    {
        "title": "What's next",
        "body": [
            "After this primer, the commands you'll use most:",
            "  • quickstart  — 16-question guided setup (stance, priorities, etc.).",
            "  • review      — score an NDA; --why for evidence, --llm for LLM pass.",
            "  • draft       — generate outgoing NDAs in .md + .docx.",
            "  • negotiate   — two-party turn-taking flow:",
            "                    init → counter [--auto/--agent/--dry-run] → accept",
            "                    → diff → sign-off → finalize  (or  withdraw)",
            "                    + simulate / analyze for game-theoretic dashboards.",
            "  • doctor      — sanity-check first-run readiness.",
            "",
            "Read GETTING_STARTED.md (Scenarios A-H) for the path that matches you.",
            "Read examples/negotiate-cheatsheet.md for a one-page negotiate reference.",
        ],
    },
]


def _md_to_docx_xml(md_text: str) -> str:
    """Convert a small subset of markdown (h1, h2, paragraphs, **bold**, --- rule)
    to a Word `word/document.xml` body. No third-party deps."""
    def esc(s: str) -> str:
        return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    def runs(text: str) -> str:
        parts = re.split(r"(\*\*[^*]+\*\*)", text)
        chunks = []
        for part in parts:
            if not part:
                continue
            if part.startswith("**") and part.endswith("**"):
                inner = esc(part[2:-2])
                chunks.append(f'<w:r><w:rPr><w:b/></w:rPr><w:t xml:space="preserve">{inner}</w:t></w:r>')
            else:
                chunks.append(f'<w:r><w:t xml:space="preserve">{esc(part)}</w:t></w:r>')
        return "".join(chunks)

    body_parts = []
    for raw_line in md_text.splitlines():
        line = raw_line.rstrip()
        if not line.strip():
            body_parts.append("<w:p/>")
            continue
        if line.strip() == "---":
            body_parts.append('<w:p><w:pPr><w:pBdr><w:bottom w:val="single" w:sz="6" w:space="1" w:color="auto"/></w:pBdr></w:pPr></w:p>')
            continue
        if line.startswith("# "):
            body_parts.append(f'<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr>{runs(line[2:])}</w:p>')
        elif line.startswith("## "):
            body_parts.append(f'<w:p><w:pPr><w:pStyle w:val="Heading2"/></w:pPr>{runs(line[3:])}</w:p>')
        else:
            body_parts.append(f"<w:p>{runs(line)}</w:p>")

    body = "".join(body_parts)
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body}<w:sectPr/></w:body></w:document>"
    )


_DOCX_CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
    '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
    "</Types>"
)
_DOCX_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
    "</Relationships>"
)
_DOCX_DOC_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
    "</Relationships>"
)
_DOCX_STYLES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
    '<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/>'
    '<w:rPr><w:b/><w:sz w:val="32"/></w:rPr></w:style>'
    '<w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/>'
    '<w:rPr><w:b/><w:sz w:val="26"/></w:rPr></w:style>'
    "</w:styles>"
)


def md_to_docx(md_text: str, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc_xml = _md_to_docx_xml(md_text)
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", _DOCX_CONTENT_TYPES)
        zf.writestr("_rels/.rels", _DOCX_RELS)
        zf.writestr("word/_rels/document.xml.rels", _DOCX_DOC_RELS)
        zf.writestr("word/document.xml", doc_xml)
        zf.writestr("word/styles.xml", _DOCX_STYLES)


DRAFT_TEMPLATES = {
    "mutual": "templates/mutual_nda.md",
    "one-way-out": "templates/one_way_out_nda.md",
    "common-paper-mutual": "templates/common_paper_mutual_nda.md",
}

DRAFT_DISCLAIMER_MD = (
    "> **DRAFT — generated by nda-review-cli.** "
    "This is a starting point based on your house policy, not legal advice. "
    "Have qualified counsel review before signing.\n"
)


def _suggest_draft_template(profile: dict) -> str:
    direction = (profile.get("nda_direction") or "").lower()
    if direction in ("disclosing", "one-way-out", "out"):
        return "one-way-out"
    return "mutual"


def _draft_clause_text(clause_rules: dict, key: str) -> str:
    rule = clause_rules.get(key) or {}
    text = (rule.get("preferred") or "").strip()
    return text or f"[Insert preferred {key} language here.]"


def _build_draft_substitutions(args, org_policy: dict, profile: dict) -> dict:
    rules = org_policy.get("clause_rules", {}) or {}
    governing = (
        args.governing_law
        or (org_policy.get("preferred_jurisdictions") or [None])[0]
        or "the parties' chosen jurisdiction"
    )
    effective = args.effective_date or datetime.now(timezone.utc).strftime("%Y-%m-%d")

    subs = {
        "effective_date": effective,
        "purpose": args.purpose,
        "governing_law": governing,
        "clause_definition_of_confidential_information": _draft_clause_text(rules, "definition_of_confidential_information"),
        "clause_exceptions": _draft_clause_text(rules, "exceptions"),
        "clause_use_restrictions": _draft_clause_text(rules, "use_restrictions"),
        "clause_term_and_survival": _draft_clause_text(rules, "term_and_survival"),
        "clause_return_or_destroy": _draft_clause_text(rules, "return_or_destroy"),
        "clause_residuals": _draft_clause_text(rules, "residuals"),
        "clause_assignment_and_affiliates": _draft_clause_text(rules, "assignment_and_affiliates"),
        "clause_liability_and_remedies": _draft_clause_text(rules, "liability_and_remedies"),
        "clause_non_solicit_non_compete": _draft_clause_text(rules, "non_solicit_non_compete"),
    }

    if args.template in ("mutual", "common-paper-mutual"):
        subs.update({
            "party_a": args.party_a or "",
            "party_a_address": args.party_a_address or "",
            "party_b": args.party_b or "",
            "party_b_address": args.party_b_address or "",
        })
    else:
        subs.update({
            "disclosing_party": args.disclosing_party or args.party_a or org_policy.get("org_name") or "",
            "disclosing_party_address": args.disclosing_party_address or args.party_a_address or "",
            "receiving_party": args.receiving_party or args.party_b or "",
            "receiving_party_address": args.receiving_party_address or args.party_b_address or "",
        })
    return subs


def _fill_template(template_text: str, subs: dict):
    template_text = re.sub(r"<!--.*?-->\s*", "", template_text, flags=re.DOTALL)
    placeholders = sorted(set(re.findall(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}", template_text)))
    missing = [p for p in placeholders if not str(subs.get(p, "")).strip()]
    def repl(m):
        key = m.group(1).strip()
        val = subs.get(key, "")
        return str(val) if val is not None else ""
    filled = re.sub(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}", repl, template_text)
    return filled, missing


def cmd_draft(args):
    repo = Path(__file__).resolve().parent
    base = Path(args.base)

    org_policy_path = base / "config" / "org-policy.json"
    if not org_policy_path.exists():
        print(json.dumps({
            "ok": False,
            "error": "No org-policy.json found. Run `./nda_review_cli.py quickstart` or `setup --quick --yes` first.",
        }, ensure_ascii=False))
        raise SystemExit(2)
    org_policy = json.loads(org_policy_path.read_text())

    profile_path = base / "profiles" / "default.json"
    profile = json.loads(profile_path.read_text()) if profile_path.exists() else {}

    if not args.template:
        args.template = _suggest_draft_template(profile)

    if args.template not in DRAFT_TEMPLATES and not args.template_file:
        print(json.dumps({
            "ok": False,
            "error": f"Unknown template '{args.template}'. Pick {sorted(DRAFT_TEMPLATES.keys())} or pass --template-file.",
        }, ensure_ascii=False))
        raise SystemExit(2)

    if args.template_file:
        template_path = Path(args.template_file)
        if not template_path.exists():
            print(json.dumps({"ok": False, "error": f"Template file not found: {template_path}"}, ensure_ascii=False))
            raise SystemExit(2)
        template_text = template_path.read_text()
    else:
        template_text = (repo / DRAFT_TEMPLATES[args.template]).read_text()

    subs = _build_draft_substitutions(args, org_policy, profile)
    filled, missing = _fill_template(template_text, subs)

    if missing:
        print(json.dumps({
            "ok": False,
            "missing_placeholders": missing,
            "hint": "Pass corresponding flags (e.g. --party-a, --purpose) or extend --template-file values.",
        }, indent=2, ensure_ascii=False))
        raise SystemExit(2)

    if not args.no_disclaimer:
        filled = DRAFT_DISCLAIMER_MD + "\n" + filled

    out_md = Path(args.out)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text(filled)

    out_docx = None
    if args.out_docx:
        out_docx = Path(args.out_docx)
        md_to_docx(filled, out_docx)

    review_summary = None
    if args.review_after:
        class Obj:
            pass
        review_args = Obj()
        review_args.base = str(base)
        review_args.playbook = str(base / "output" / "nda_playbook.json")
        review_args.counterparty = args.counterparty
        review_args.file = str(out_md)
        review_args.text = None
        review_args.out_json = str(out_md.with_suffix(".review.json"))
        review_args.out_md = str(out_md.with_suffix(".review.md"))
        review_args.why = True
        review_args.learn_profile = False
        review_args.scoring_profile = None
        review_args.scoring_profiles = None
        if not Path(review_args.playbook).exists():
            review_summary = {"skipped": True, "reason": "no playbook found; run build-playbook first"}
        else:
            cmd_review(review_args)
            review_summary = {
                "review_json": review_args.out_json,
                "review_md": review_args.out_md,
            }

    payload = {
        "ok": True,
        "template": args.template,
        "template_file": args.template_file,
        "out_md": str(out_md),
        "out_docx": str(out_docx) if out_docx else None,
        "review": review_summary,
        "placeholders_used": sorted(set(re.findall(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}", template_text))),
        "policy_path": str(org_policy_path),
    }
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    _print_friendly(
        title=f"Draft generated ({args.template})",
        lines=[
            f"Markdown: {out_md}",
            f"Word doc: {out_docx if out_docx else '(skipped)'}",
            f"Review-after: {review_summary.get('review_md') if review_summary and not review_summary.get('skipped') else '(skipped)'}",
        ],
        next_steps=[
            "Open the .docx in Word, fill in signatures, and run a final human review.",
            "Re-run `./nda_review_cli.py review --file <out_md>` after any manual edits to sanity-check.",
            "Tweak `config/org-policy.json` clause `preferred` text to change drafted language org-wide.",
        ],
    )


# ----------------------------------------------------------------------------
# Negotiate: two-party turn-taking NDA negotiation with LLM agent assistance.
# File-based protocol — no networking. Each round is signed by exactly one
# party. Tamper evidence via a per-round SHA-256 chain.
# ----------------------------------------------------------------------------

NEGOTIATE_SCHEMA_VERSION = "0.1"


def _negotiate_hash(round_text: str, prev_hash: str) -> str:
    h = hashlib.sha256()
    h.update(prev_hash.encode("utf-8"))
    h.update(b"\x00")
    h.update(round_text.encode("utf-8"))
    return h.hexdigest()


def _negotiate_load(path: Path) -> dict:
    if not path.exists():
        raise SystemExit(f"Negotiation state file not found: {path}")
    state = json.loads(path.read_text())
    if state.get("schema_version") != NEGOTIATE_SCHEMA_VERSION:
        raise SystemExit(
            f"Unsupported negotiation schema version: {state.get('schema_version')!r}. "
            f"Expected {NEGOTIATE_SCHEMA_VERSION}."
        )
    # Validate hash chain end-to-end.
    prev = ""
    for r in state.get("rounds", []):
        expected = _negotiate_hash(r["text"], prev)
        if r.get("text_hash") != expected:
            raise SystemExit(
                f"Hash-chain mismatch at round {r.get('round')}. "
                "State file may have been tampered with."
            )
        prev = expected
    return state


def _negotiate_save(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True, ensure_ascii=False))


def _negotiate_resolve_party(state: dict, override: Optional[str], base: Path) -> str:
    """Returns 'a' or 'b'. Override wins; otherwise auto-match org_name from local policy."""
    if override in ("a", "b"):
        return override
    org_policy_path = base / "config" / "org-policy.json"
    if org_policy_path.exists():
        org = json.loads(org_policy_path.read_text()).get("org_name", "")
        for key in ("a", "b"):
            party_name = (state["parties"].get(key) or {}).get("name", "")
            if party_name and org.strip().lower() == party_name.strip().lower():
                return key
    raise SystemExit(
        "Could not auto-detect which party you are. Pass `--as a` or `--as b`, "
        "or set `org_name` in config/org-policy.json to match one of the negotiation parties."
    )


def _negotiate_other(party: str) -> str:
    return "b" if party == "a" else "a"


def _negotiate_clause_status_init(rules: dict) -> dict:
    return {clause: {"status": "pending", "agreed_in_round": None, "last_proposer": None} for clause in rules}


def _negotiate_apply_amendments(text: str, amendments: list) -> str:
    """Replace clause text in the markdown body. Each amendment has clause / old_text / new_text.
    Falls back to appending a clarifying note if old_text not found."""
    out = text
    for a in amendments:
        old = a.get("old_text", "")
        new = a.get("new_text", "")
        if old and old in out:
            out = out.replace(old, new, 1)
        else:
            # Couldn't find exact text — append a note so the change isn't silently lost.
            out += f"\n\n<!-- amendment to {a.get('clause','?')}: {new} -->\n"
    return out


def _negotiate_extract_clause_text(full_text: str, clause: str) -> str:
    """Best-effort extraction: find a ## section whose heading mentions clause keywords.
    Returns the paragraph after the heading, or empty string."""
    keyword_map = {
        "definition_of_confidential_information": "Definition of Confidential",
        "exceptions": "Exceptions",
        "use_restrictions": "Use Restrictions",
        "term_and_survival": "Term and Survival",
        "return_or_destroy": "Return or Destruction",
        "residuals": "Residual Knowledge",
        "assignment_and_affiliates": "Permitted Disclosures",
        "governing_law_jurisdiction": "Governing Law",
        "liability_and_remedies": "Liability and Remedies",
        "non_solicit_non_compete": "Non-Solicitation",
    }
    needle = keyword_map.get(clause)
    if not needle:
        return ""
    pat = re.compile(rf"^##\s+\d*\.?\s*{re.escape(needle)}.*?$\n+(.*?)(?=^##\s|\Z)", re.M | re.S)
    m = pat.search(full_text)
    return m.group(1).strip() if m else ""


NEGOTIATE_STANCE_DESCRIPTORS = {
    "conservative": (
        "STANCE: conservative. Hold firm to your party's preferred clause language. "
        "Counter any amendment that materially differs from your preferred position. "
        "Accept only when the other party's text is functionally equivalent to your preferred."
    ),
    "middleground": (
        "STANCE: middleground. Compromise on low-severity items where the other party's text "
        "is reasonable. Hold firm on high-severity items and clauses that trigger your red-flag "
        "patterns (e.g. perpetual survival, broad residuals, uncapped liability)."
    ),
    "compromising": (
        "STANCE: compromising. Accept most amendments unless they trigger one of your red-flag "
        "patterns. Push back only on genuine dealbreakers (red flags, missing standard carve-outs, "
        "broad residuals)."
    ),
}


NEGOTIATE_LLM_AGENT_SYSTEM = (
    "You are an NDA negotiation agent representing one party. You receive: "
    "(1) the current full NDA text, "
    "(2) your party's house policy (clause-by-clause preferred language and red flags), "
    "(3) your party's negotiation stance, "
    "(4) your party's clause priority ranking (1=most important), "
    "(5) any clauses your party has marked as NON-NEGOTIABLE (hard floor — never accept differing text on these), "
    "(6) per-clause bounce counts (clauses that have been amended in N consecutive alternating-proposer rounds), "
    "(7) the latest round of amendments proposed by the other party. "
    "Your job is to propose your party's response: which other-party amendments to accept, which to counter, "
    "and any new amendments your policy requires. "
    "RULES (in order of strictness):\n"
    " 1. NON-NEGOTIABLE clauses: counter unless the current text exactly matches your preferred. Never accept a differing text on these.\n"
    " 2. Stance: apply literally. Conservative = hold firm; middleground = compromise on non-red-flag items; compromising = accept most things, push back on red flags only.\n"
    " 3. Priority + concession zone: bottom 30% (conservative), 60% (middleground), or 85% (compromising) by priority can be conceded. Inside your insistence zone, follow stance logic.\n"
    " 4. Bounce count: if a clause has bounced >= 4 rounds, consider conceding it to break the deadlock — UNLESS it's non-negotiable.\n"
    "Reply ONLY with a JSON object matching this schema:\n"
    "{\n"
    '  "accept_clauses": [string],            # clauses where the other side\'s proposal is acceptable\n'
    '  "counter_amendments": [{"clause": str, "old_text": str, "new_text": str, "rationale": str}],\n'
    '  "summary": string                      # one-paragraph negotiation note\n'
    "}\n"
    "Do not include any commentary outside the JSON object."
)


def _negotiate_auto_propose(state: dict, party: str, org_policy: dict, stance: str) -> dict:
    """Deterministic stance-driven amendment generator. No LLM.

    For each clause known to the policy:
      - Compare the *clause's currently visible text* in the latest round to
        the policy's preferred text.
      - If red flags fire on the visible text, treat that clause as a dealbreaker.
      - Per stance:
          conservative — counter every clause where current != preferred.
          middleground — counter only red-flag-firing clauses + accept other-party
                         amendments that don't change preferred-aligned clauses.
          compromising — counter only clauses where red flags currently fire.

    Other-party amendments from the previous round:
      conservative — reject all (do not list in accept_clauses).
      middleground — accept amendments to clauses with no red-flag triggers
                     in the new text.
      compromising — accept everything that doesn't trigger a red flag.
    """
    rules = org_policy.get("clause_rules", {}) or {}
    last_round = state["rounds"][-1]
    text = last_round["text"]
    last_amendments = last_round.get("amendments") or []
    last_amendment_clauses = {a.get("clause") for a in last_amendments if a.get("clause")}

    priorities = org_policy.get("clause_priorities") or list(rules.keys())
    concession_zone = _negotiate_concession_zone(rules, priorities, stance)
    non_negotiable = set(org_policy.get("non_negotiable_clauses") or [])

    counter_amendments = []
    accept_clauses = []
    countered_clauses = set()
    rationale_prefix = f"[auto:{stance}] "

    # Pass 1: per-clause decision over the *current* text.
    # If the clause is in this agent's concession zone (their bottom K% by
    # priority), accept current text — this is the logrolling mechanism that
    # breaks conservative × conservative deadlocks when priorities differ.
    # Otherwise apply stance-driven counter logic.
    for clause, cfg in rules.items():
        current_block = _negotiate_extract_clause_text(text, clause)
        preferred = (cfg.get("preferred") or "").strip()
        if not current_block or not preferred:
            continue

        differs = preferred not in current_block

        # Non-negotiable hard floor: always counter if text differs, regardless
        # of stance, priority, or concession zone. These are the user's
        # declared absolute redlines.
        if clause in non_negotiable and differs:
            counter_amendments.append({
                "clause": clause,
                "old_text": current_block,
                "new_text": preferred,
                "rationale": rationale_prefix + "non-negotiable clause — text must match preferred.",
            })
            countered_clauses.add(clause)
            continue

        if clause in concession_zone:
            accept_clauses.append(clause)
            continue

        red_flags_fired = bool(red_flag_hits(current_block.lower(), clause))

        will_counter = False
        rationale = ""
        if stance == "conservative" and differs:
            will_counter = True
            rationale = f"top-priority clause (rank {_negotiate_priority_rank(priorities, clause)}); text differs from preferred."
        elif stance == "middleground" and red_flags_fired:
            will_counter = True
            rationale = "red-flag pattern fired on this clause; replacing with preferred language."
        elif stance == "compromising" and red_flags_fired:
            will_counter = True
            rationale = "dealbreaker red flag in top-priority clause."

        if will_counter:
            counter_amendments.append({
                "clause": clause,
                "old_text": current_block,
                "new_text": preferred,
                "rationale": rationale_prefix + rationale,
            })
            countered_clauses.add(clause)
        else:
            accept_clauses.append(clause)

    # Pass 2: explicit acceptance of other-party amendments from the previous
    # round. Only adds clauses that aren't already in the lists from pass 1.
    # Conservative still rejects all proposals.
    if stance != "conservative":
        for am in last_amendments:
            clause = am.get("clause")
            if not clause or clause in countered_clauses or clause in accept_clauses:
                continue
            proposed = (am.get("new_text") or "")
            proposed_red_flags = bool(red_flag_hits(proposed.lower(), clause)) if clause in rules else False
            if stance == "middleground" and not proposed_red_flags:
                accept_clauses.append(clause)
            elif stance == "compromising" and not proposed_red_flags:
                accept_clauses.append(clause)

    accept_clauses = sorted(set(accept_clauses))
    return {
        "accept_clauses": accept_clauses,
        "counter_amendments": counter_amendments,
        "summary": f"Deterministic auto-counter applied with stance={stance!r}. "
                   f"{len(counter_amendments)} amendment(s) proposed, {len(accept_clauses)} clause(s) accepted.",
    }


def last_amendment_clauses_in_counters(counter_amendments: list) -> set:
    return {a.get("clause") for a in counter_amendments if a.get("clause")}


def _negotiate_resolve_stance(org_policy: dict, override: Optional[str]) -> str:
    if override and override in NEGOTIATE_STANCE_DESCRIPTORS:
        return override
    stance = ((org_policy.get("defaults") or {}).get("negotiation_stance")) or org_policy.get("negotiation_stance") or "middleground"
    return stance if stance in NEGOTIATE_STANCE_DESCRIPTORS else "middleground"


def _negotiate_agent_propose(state: dict, party: str, org_policy: dict, llm_cfg: dict, stance: str) -> dict:
    """Run the LLM agent against the current state to propose the next round's amendments."""
    rules = org_policy.get("clause_rules", {}) or {}
    priorities = org_policy.get("clause_priorities") or list(rules.keys())
    non_negotiable = org_policy.get("non_negotiable_clauses") or []

    # Build a rich per-clause context: rank, red flags, bounce count.
    pref_lines = []
    for clause, cfg in rules.items():
        rank = _negotiate_priority_rank(priorities, clause)
        bounces = _negotiate_clause_bounce_count(state, clause)
        markers = []
        if clause in non_negotiable:
            markers.append("NON_NEGOTIABLE")
        if bounces >= 1:
            markers.append(f"bounce_count={bounces}")
        marker_str = f" [{', '.join(markers)}]" if markers else ""
        pref_lines.append(
            f"- {clause} (rank {rank}){marker_str}: {cfg.get('preferred','')}"
        )

    last_round = state["rounds"][-1]
    last_amendments = last_round.get("amendments") or []
    stance_text = NEGOTIATE_STANCE_DESCRIPTORS.get(stance, NEGOTIATE_STANCE_DESCRIPTORS["middleground"])

    user_prompt = (
        f"## Your party: {state['parties'][party]['name']} ({state['parties'][party].get('role','')})\n\n"
        f"## {stance_text}\n\n"
        f"## Your priority order (top = most important): {', '.join(priorities) or '(default policy order)'}\n\n"
        f"## Your non-negotiable clauses (hard floor): {non_negotiable or '(none)'}\n\n"
        f"## Your house policy by clause (rank, markers, preferred language)\n\n"
        f"{chr(10).join(pref_lines)}\n\n"
        f"## Current full NDA text\n\n{last_round['text']}\n\n"
        f"## Latest amendments proposed by the other party (round {last_round['round']})\n\n"
        f"{json.dumps(last_amendments, ensure_ascii=False, indent=2)}\n\n"
        f"Reply with the JSON schema described in the system prompt."
    )
    raw = llm_call(llm_cfg, NEGOTIATE_LLM_AGENT_SYSTEM, user_prompt)
    parsed = _parse_llm_review_response(raw["text"])
    # Reuse the same defensive parser; it tolerates extra keys.
    try:
        obj = json.loads(raw["text"]) if not parsed.get("_parse_error") else None
    except Exception:
        obj = None
    if obj and isinstance(obj, dict):
        return {
            "accept_clauses": obj.get("accept_clauses") or [],
            "counter_amendments": obj.get("counter_amendments") or [],
            "summary": obj.get("summary") or "",
            "model": raw.get("model") or llm_cfg.get("model"),
            "usage": raw.get("usage", {}),
        }
    return {
        "accept_clauses": [],
        "counter_amendments": [],
        "summary": "",
        "parse_error": parsed.get("_parse_error"),
        "raw_text": (raw.get("text") or "")[:1000],
    }


def _negotiate_recompute_clause_status(state: dict, rules: dict) -> dict:
    """Walk all rounds, derive per-clause status.

    Round 1 by Party A is the initial draft — all clauses are implicitly proposed by A.
    Subsequent rounds may amend a clause (sets last_proposer to that round's proposer)
    or accept it (counter-party agreement → status becomes 'agreed')."""
    status = _negotiate_clause_status_init(rules)
    if state.get("rounds"):
        initial = state["rounds"][0]
        for clause in status:
            status[clause]["last_proposer"] = initial["proposer"]
            status[clause]["status"] = "proposed"
    for r in state["rounds"][1:]:
        proposer = r["proposer"]
        for clause in r.get("accept_clauses", []) or []:
            if clause in status and status[clause]["last_proposer"] != proposer:
                status[clause]["status"] = "agreed"
                status[clause]["agreed_in_round"] = r["round"]
        for am in r.get("amendments", []) or []:
            clause = am.get("clause")
            if clause and clause in status:
                status[clause]["status"] = "disputed"
                status[clause]["last_proposer"] = proposer
                status[clause]["agreed_in_round"] = None
    return status


DEFAULT_STALEMATE_THRESHOLD = 4
DEFAULT_MAX_CLAUSE_BOUNCES = 4

# Per-stance concession-zone size: fraction of clauses (by priority rank,
# bottom-up) that the agent is willing to concede when not stance-required to
# push back. Conservative still concedes its 30% lowest-priority clauses;
# compromising concedes 85% and only insists on its 15% top priorities.
NEGOTIATE_CONCESSION_PCT = {
    "conservative": 0.30,
    "middleground": 0.60,
    "compromising": 0.85,
}


def _negotiate_clause_bounce_count(state: dict, clause: str) -> int:
    """Count consecutive most-recent rounds in which `clause` was amended,
    with proposers strictly alternating each round. A clause that was amended
    only once has bounce count 1. Two parties going back and forth on the
    same clause increments the count every round; if either party amends
    something else (or the same party amends twice in a row), the streak
    breaks. Used by the fatigue-concession rule to detect deadlocks."""
    rounds = state.get("rounds", [])
    count = 0
    last_proposer = None
    for r in reversed(rounds):
        amended = {a.get("clause") for a in (r.get("amendments") or []) if a.get("clause")}
        if clause not in amended:
            break
        proposer = r["proposer"]
        if last_proposer is None:
            count = 1
            last_proposer = proposer
        elif proposer != last_proposer:
            count += 1
            last_proposer = proposer
        else:
            break
    return count


def _apply_fatigue(proposal: dict, state: dict, max_bounces: int, non_negotiable: list = None) -> dict:
    """Apply fatigue concession: any clause that's bouncing >= max_bounces
    times consecutively is force-conceded by the current proposer regardless
    of stance / priority / red flags. Mutates proposal in place: clauses move
    from counter_amendments → accept_clauses, and a `fatigue_concessions`
    list is added so the round can be tagged auto:<stance>+fatigue.

    Clauses listed in `non_negotiable` are NEVER fatigue-conceded — they're
    hard floors the user has declared as absolute redlines. If a non-negotiable
    clause keeps bouncing, the stalemate detector will eventually block the
    negotiation rather than force-concede a redline."""
    non_negotiable_set = set(non_negotiable or [])
    if max_bounces <= 0:
        proposal["fatigue_concessions"] = []
        return proposal

    fatigued = []
    for am in list(proposal.get("counter_amendments") or []):
        clause = am.get("clause")
        if not clause:
            continue
        if clause in non_negotiable_set:
            continue  # Hard floor — never fatigue-concede a non-negotiable clause.
        if _negotiate_clause_bounce_count(state, clause) >= max_bounces:
            fatigued.append(clause)

    if fatigued:
        fatigued_set = set(fatigued)
        proposal["counter_amendments"] = [
            am for am in (proposal.get("counter_amendments") or [])
            if am.get("clause") not in fatigued_set
        ]
        proposal["accept_clauses"] = sorted(set(
            list(proposal.get("accept_clauses") or []) + fatigued
        ))
        # Annotate the rationale on each fatigue concession so the audit trail
        # explains why this party gave ground on a clause they were countering.
        existing_summary = proposal.get("summary", "")
        proposal["summary"] = (existing_summary + " " if existing_summary else "") + (
            f"Fatigue: conceding {len(fatigued)} clause(s) after >= {max_bounces} consecutive amendment rounds: "
            f"{', '.join(fatigued)}."
        )
    proposal["fatigue_concessions"] = fatigued
    return proposal


def _negotiate_priority_rank(priorities: list, clause: str) -> int:
    """Return the 1-based rank of `clause` in the priority list. Clauses
    not explicitly ranked are placed at the end (lowest priority)."""
    if clause in priorities:
        return priorities.index(clause) + 1
    return len(priorities) + 1


def _negotiate_concession_zone(rules: dict, priorities: list, stance: str) -> set:
    """Return the set of clauses the agent should concede on (accept current
    text rather than push back), based on the agent's own priority list and
    stance. Concession zone = the bottom K clauses by priority where K is a
    stance-driven percentage of |rules|."""
    clauses = list(rules.keys())
    if not clauses:
        return set()
    # Build ranking — explicit priorities first (in order), then any
    # un-ranked policy clauses appended in their original order.
    ranked = [c for c in priorities if c in rules]
    for c in clauses:
        if c not in ranked:
            ranked.append(c)
    pct = NEGOTIATE_CONCESSION_PCT.get(stance, NEGOTIATE_CONCESSION_PCT["middleground"])
    n_concede = max(0, round(len(ranked) * pct))
    return set(ranked[len(ranked) - n_concede:]) if n_concede else set()


def _negotiate_agreed_count_at_round(state: dict, round_index: int, rules: dict) -> int:
    """How many clauses are 'agreed' if we look at state up to and including round_index."""
    truncated = {**state, "rounds": state["rounds"][: round_index + 1]}
    cs = _negotiate_recompute_clause_status(truncated, rules)
    return sum(1 for s in cs.values() if s.get("status") == "agreed")


def _negotiate_rounds_without_progress(state: dict, rules: dict) -> int:
    """Number of consecutive most-recent rounds during which the count of
    `agreed` clauses did not strictly increase. Used by the stalemate detector.

    Round 1 is the initial draft and has no "previous" agreed count, so we
    measure progress starting from round 2."""
    rounds = state.get("rounds", [])
    if len(rounds) < 2:
        return 0
    # Pre-compute agreed_count at each round.
    counts = [_negotiate_agreed_count_at_round(state, i, rules) for i in range(len(rounds))]
    # Walk backwards from the most recent round, counting "no increase" rounds.
    no_progress = 0
    for i in range(len(counts) - 1, 0, -1):
        if counts[i] > counts[i - 1]:
            break
        no_progress += 1
    return no_progress


def _negotiate_check_blocked(state: dict, rules: dict, threshold: int = DEFAULT_STALEMATE_THRESHOLD) -> dict:
    """If `rounds_without_progress >= threshold`, flips status to `blocked` and
    attaches a diagnostic. Idempotent — once blocked, leaves the state alone.
    Returns the (possibly-modified) state."""
    if state.get("status") in ("converged", "signed_off", "finalized", "blocked"):
        return state
    no_progress = _negotiate_rounds_without_progress(state, rules)
    if no_progress >= threshold:
        cs = state.get("clause_status", {})
        stuck = sorted(c for c, s in cs.items() if s.get("status") == "disputed")
        # Non-negotiable conflicts get a more specific diagnosis: these clauses
        # were intentionally protected from fatigue concession by one or both
        # parties, so the deadlock is by design and needs human escalation,
        # not a stance change.
        state["status"] = "blocked"
        state["block_diagnosis"] = {
            "rounds_without_progress": no_progress,
            "threshold": threshold,
            "stuck_clauses": stuck,
            "note": (
                "No new clause has reached `agreed` status for several consecutive "
                "rounds. Likely cause: both parties' stances are too rigid for the "
                "rules-engine to resolve, or one or both sides marked some of the "
                "stuck clauses as non-negotiable. Try `--stance compromising` for "
                "one side, switch to `--agent --llm`, escalate to humans, or — if "
                "stuck on non-negotiable clauses — accept that this deal cannot "
                "close on those terms."
            ),
        }
    return state


def _negotiate_is_converged(state: dict) -> bool:
    """Converged when no clause is currently `disputed` and the latest two rounds were
    signed by alternating parties (one drafted/countered, the other accepted)."""
    cs = state.get("clause_status", {})
    if not cs:
        return False
    if any(s.get("status") == "disputed" for s in cs.values()):
        return False
    if not any(s.get("status") == "agreed" for s in cs.values()):
        return False  # No clauses have been agreed by both sides yet.
    last_two = state["rounds"][-2:]
    if len(last_two) < 2:
        return False
    return last_two[0]["proposer"] != last_two[1]["proposer"]


def _negotiate_load_org_policy(base: Path) -> dict:
    p = base / "config" / "org-policy.json"
    if not p.exists():
        raise SystemExit(
            "config/org-policy.json not found. Run `nda-review-cli quickstart` or `setup --quick --yes` first."
        )
    return json.loads(p.read_text())


def cmd_negotiate_init(args):
    base = Path(args.base)
    org_policy = _negotiate_load_org_policy(base)
    rules = org_policy.get("clause_rules", {}) or {}

    # Build the initial draft using the existing draft pipeline.
    repo = Path(__file__).resolve().parent
    template_text = (repo / DRAFT_TEMPLATES[args.template]).read_text()
    profile_path = base / "profiles" / "default.json"
    profile = json.loads(profile_path.read_text()) if profile_path.exists() else {}

    class _A: pass
    da = _A()
    da.template = args.template
    da.party_a = args.party_a_name if args.template == "mutual" else args.disclosing_party
    da.party_a_address = args.party_a_address if args.template == "mutual" else args.disclosing_party_address
    da.party_b = args.party_b_name if args.template == "mutual" else args.receiving_party
    da.party_b_address = args.party_b_address if args.template == "mutual" else args.receiving_party_address
    da.disclosing_party = args.disclosing_party
    da.disclosing_party_address = args.disclosing_party_address
    da.receiving_party = args.receiving_party
    da.receiving_party_address = args.receiving_party_address
    da.purpose = args.purpose
    da.effective_date = args.effective_date
    da.governing_law = args.governing_law
    subs = _build_draft_substitutions(da, org_policy, profile)
    text, missing = _fill_template(template_text, subs)
    if missing:
        raise SystemExit(f"Missing template values: {missing}. Pass corresponding flags.")

    party_a_name = da.party_a or da.disclosing_party or org_policy.get("org_name") or "Party A"
    party_b_name = da.party_b or da.receiving_party or "Party B"
    parties = {
        "a": {
            "name": party_a_name,
            "address": da.party_a_address or da.disclosing_party_address or "",
            "role": "mutual" if args.template == "mutual" else "disclosing",
        },
        "b": {
            "name": party_b_name,
            "address": da.party_b_address or da.receiving_party_address or "",
            "role": "mutual" if args.template == "mutual" else "receiving",
        },
    }

    initial_hash = _negotiate_hash(text, "")
    state = {
        "schema_version": NEGOTIATE_SCHEMA_VERSION,
        "negotiation_id": hashlib.sha256(f"{datetime.now(timezone.utc).isoformat()}::{party_a_name}::{party_b_name}".encode()).hexdigest()[:16],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "parties": parties,
        "purpose": da.purpose,
        "template": args.template,
        "rounds": [
            {
                "round": 1,
                "proposer": "a",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "text": text,
                "text_hash": initial_hash,
                "amendments": [],
                "accept_clauses": [],
                "summary": "Initial draft.",
                "signature": {"signer": "a", "signed_at": datetime.now(timezone.utc).isoformat(), "method": "json_flag"},
                "amendment_source": "initial",
            }
        ],
        "clause_status": _negotiate_clause_status_init(rules),
        "status": "in_progress",
        "finalized": None,
    }
    out = Path(args.out)
    _negotiate_save(out, state)
    print(json.dumps({"ok": True, "negotiation_id": state["negotiation_id"], "state_file": str(out), "round": 1, "proposer": "a"}, ensure_ascii=False))
    _print_friendly(
        title=f"Negotiation initialized ({args.template})",
        lines=[
            f"State file: {out}",
            f"Party A: {party_a_name}",
            f"Party B: {party_b_name}",
            f"Round 1 signed by Party A.",
        ],
        next_steps=[
            f"Send {out.name} to the other party (email, Drive, Git — any channel).",
            "Other party runs `negotiate review --state <file>` to see findings against their policy.",
            "Other party runs `negotiate counter [--agent --llm] --state <file> --as b` to propose round 2.",
        ],
    )


def cmd_negotiate_review(args):
    base = Path(args.base)
    state = _negotiate_load(Path(args.state))
    org_policy = _negotiate_load_org_policy(base)
    party = _negotiate_resolve_party(state, args.as_party, base)

    last = state["rounds"][-1]
    # Run a deterministic review of the current text against your policy.
    playbook_path = base / "output" / "nda_playbook.json"
    playbook = json.loads(playbook_path.read_text()) if playbook_path.exists() else {"policy": [
        {"clause": k, "preferred": v.get("preferred", ""), "red_flags": v.get("red_flags", [])}
        for k, v in (org_policy.get("clause_rules") or {}).items()
    ]}
    profile = load_counterparty_profile(base, None)
    scoring = scoring_profile_details(base, None, None)
    review = review_text(last["text"], playbook, profile=profile, scoring_profile=scoring, explainability=True)

    payload = {
        "ok": True,
        "negotiation_id": state["negotiation_id"],
        "you_are": party,
        "current_round": last["round"],
        "current_round_proposer": last["proposer"],
        "your_turn": last["proposer"] != party,
        "review": {
            "decision": review.get("decision"),
            "risk_score": review.get("risk_score"),
            "findings_count": len(review.get("findings", [])),
            "high_severity_clauses": [f["clause"] for f in review.get("findings", []) if f.get("severity") == "high"],
        },
        "clause_status": state.get("clause_status", {}),
    }
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    _print_friendly(
        title=f"Negotiation review (round {last['round']})",
        lines=[
            f"Your role: Party {party.upper()} ({state['parties'][party]['name']})",
            f"Current round proposed by: Party {last['proposer'].upper()}",
            f"Your turn? {'yes' if last['proposer'] != party else 'no — you proposed the latest round'}",
            f"Decision (your policy): {review.get('decision','?').upper()}  risk={review.get('risk_score')}",
            f"High-severity clauses: {', '.join(payload['review']['high_severity_clauses']) or '(none)'}",
        ],
        next_steps=[
            "Run `negotiate counter --agent --llm` to draft amendments via your LLM agent.",
            "Or `negotiate counter --amendments-file <my-edits.json>` to hand-write amendments.",
            "Or `negotiate accept` to accept the current text and trigger convergence.",
        ],
    )


def cmd_negotiate_counter(args):
    base = Path(args.base)
    state = _negotiate_load(Path(args.state))
    org_policy = _negotiate_load_org_policy(base)
    rules = org_policy.get("clause_rules", {}) or {}
    party = _negotiate_resolve_party(state, args.as_party, base)
    if state.get("status") in ("withdrawn", "finalized"):
        raise SystemExit(f"Negotiation is in terminal state `{state.get('status')}`; further counters are not allowed.")
    if state.get("status") == "blocked" and not getattr(args, "force_unblock", False):
        diag = state.get("block_diagnosis", {})
        raise SystemExit(
            f"Negotiation is `blocked` (no progress for {diag.get('rounds_without_progress','?')} rounds). "
            f"Stuck clauses: {diag.get('stuck_clauses')}. "
            "Pass `--force-unblock` to keep going at your own risk, or change stance / switch to --agent."
        )
    last = state["rounds"][-1]
    if last["proposer"] == party:
        raise SystemExit(
            f"Round {last['round']} was proposed by you (Party {party.upper()}). "
            "It's the other party's turn — wait for their counter, or use `negotiate accept` to converge on the current text."
        )

    stance = _negotiate_resolve_stance(org_policy, getattr(args, "stance", None))

    max_bounces = int((org_policy.get("defaults") or {}).get("max_clause_bounces", DEFAULT_MAX_CLAUSE_BOUNCES))

    if args.amendments_file:
        proposal = json.loads(Path(args.amendments_file).read_text())
        proposal_source = "manual"
    elif args.auto:
        proposal = _negotiate_auto_propose(state, party, org_policy, stance)
        proposal_source = f"auto:{stance}"
    elif args.agent:
        llm_cfg = load_llm_config(base, args)
        if not llm_cfg.get("provider") or not llm_cfg.get("model"):
            raise SystemExit("--agent requires LLM provider/model. Configure config/llm.json or pass --llm/--llm-model.")
        if not args.yes_llm_send and not (sys.stderr.isatty() and sys.stdin.isatty()):
            raise SystemExit("Refusing non-interactive LLM call without --yes-llm-send.")
        if not args.yes_llm_send:
            print(f"\n  Agent ({stance}) will call provider={llm_cfg['provider']} model={llm_cfg['model']}.", file=sys.stderr)
            print("  Press Enter to continue, or Ctrl-C to abort.", file=sys.stderr)
            try:
                input()
            except (EOFError, KeyboardInterrupt):
                raise SystemExit("Aborted by user.")
        proposal = _negotiate_agent_propose(state, party, org_policy, llm_cfg, stance)
        proposal_source = f"agent:{stance}"
    else:
        raise SystemExit(
            "Pass one of: --amendments-file <path> (manual), --auto (deterministic stance-driven), "
            "or --agent --llm <provider> (LLM-driven)."
        )

    # Apply fatigue concession uniformly across all proposal modes — clauses
    # bouncing >= max_bounces times consecutively get force-conceded by the
    # current proposer regardless of how the proposal was generated.
    non_negotiable = org_policy.get("non_negotiable_clauses") or []
    proposal = _apply_fatigue(proposal, state, max_bounces, non_negotiable=non_negotiable)
    fatigue_concessions = proposal.get("fatigue_concessions") or []
    if fatigue_concessions:
        proposal_source = proposal_source + "+fatigue"

    # Dry-run: preview the proposal without writing the round to the state file.
    if getattr(args, "dry_run", False):
        preview = {
            "dry_run": True,
            "would_be_round": last["round"] + 1,
            "proposer": party,
            "stance": stance,
            "amendment_source": proposal_source,
            "amendments": proposal.get("counter_amendments") or [],
            "accept_clauses": proposal.get("accept_clauses") or [],
            "fatigue_concessions": fatigue_concessions,
            "summary": proposal.get("summary") or "",
            "state_unchanged": True,
        }
        print(json.dumps(preview, indent=2, ensure_ascii=False))
        return

    accept_clauses = proposal.get("accept_clauses") or []
    counter_amendments = proposal.get("counter_amendments") or []
    summary = proposal.get("summary") or ""
    new_text = _negotiate_apply_amendments(last["text"], counter_amendments)
    new_round = {
        "round": last["round"] + 1,
        "proposer": party,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "text": new_text,
        "text_hash": _negotiate_hash(new_text, last["text_hash"]),
        "amendments": counter_amendments,
        "accept_clauses": accept_clauses,
        "summary": summary,
        "signature": {"signer": party, "signed_at": datetime.now(timezone.utc).isoformat(), "method": "json_flag"},
        "amendment_source": proposal_source,
        "stance": stance,
        "fatigue_concessions": fatigue_concessions,
    }
    if proposal.get("parse_error"):
        new_round["agent_parse_error"] = proposal["parse_error"]
    state["rounds"].append(new_round)
    state["clause_status"] = _negotiate_recompute_clause_status(state, rules)
    if _negotiate_is_converged(state):
        state["status"] = "converged"
    else:
        state = _negotiate_check_blocked(state, rules)

    out = Path(args.out or args.state)
    _negotiate_save(out, state)
    print(json.dumps({
        "ok": True,
        "round": new_round["round"],
        "amendments_count": len(counter_amendments),
        "accepted_clauses": accept_clauses,
        "status": state["status"],
        "state_file": str(out),
    }, ensure_ascii=False))
    _print_friendly(
        title=f"Round {new_round['round']} signed (Party {party.upper()})",
        lines=[
            f"Amendments proposed: {len(counter_amendments)}",
            f"Other-side clauses you accepted: {len(accept_clauses)}",
            f"Negotiation status: {state['status']}",
        ],
        next_steps=(
            ["Run `negotiate finalize` to emit the agreed .md/.docx and (optionally) hand off to docx2pdf + sign-CLI."]
            if state["status"] == "converged" else
            [f"Send {out.name} to the other party for round {new_round['round'] + 1}."]
        ),
    )


def cmd_negotiate_accept(args):
    base = Path(args.base)
    state = _negotiate_load(Path(args.state))
    org_policy = _negotiate_load_org_policy(base)
    rules = org_policy.get("clause_rules", {}) or {}
    party = _negotiate_resolve_party(state, args.as_party, base)
    last = state["rounds"][-1]
    if last["proposer"] == party:
        raise SystemExit("You proposed the latest round; the other party must accept or counter, not you.")

    # Accept the entire current text — every clause we know about gets agreed by us.
    touched_clauses = sorted(rules.keys())
    new_round = {
        "round": last["round"] + 1,
        "proposer": party,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "text": last["text"],
        "text_hash": _negotiate_hash(last["text"], last["text_hash"]),
        "amendments": [],
        "accept_clauses": touched_clauses,
        "summary": "Accept current text as final.",
        "signature": {"signer": party, "signed_at": datetime.now(timezone.utc).isoformat(), "method": "json_flag"},
        "amendment_source": "accept",
    }
    state["rounds"].append(new_round)
    state["clause_status"] = _negotiate_recompute_clause_status(state, rules)
    if _negotiate_is_converged(state):
        state["status"] = "converged"
    else:
        state = _negotiate_check_blocked(state, rules)
    out = Path(args.out or args.state)
    _negotiate_save(out, state)
    print(json.dumps({
        "ok": True,
        "round": new_round["round"],
        "status": state["status"],
        "accepted_clauses": touched_clauses,
        "state_file": str(out),
    }, ensure_ascii=False))


def cmd_negotiate_status(args):
    state = _negotiate_load(Path(args.state))
    rounds = state.get("rounds", [])
    summary = {
        "negotiation_id": state["negotiation_id"],
        "status": state.get("status"),
        "round_count": len(rounds),
        "parties": {k: v.get("name") for k, v in state["parties"].items()},
        "latest_round_proposer": rounds[-1]["proposer"] if rounds else None,
        "clause_status": state.get("clause_status", {}),
        "rounds": [
            {
                "round": r["round"],
                "proposer": r["proposer"],
                "amendments_count": len(r.get("amendments", [])),
                "accepted_clauses_count": len(r.get("accept_clauses", [])),
                "amendment_source": r.get("amendment_source"),
                "summary": r.get("summary", "")[:140],
            }
            for r in rounds
        ],
        "finalized": state.get("finalized"),
    }
    print(json.dumps(summary, indent=2, ensure_ascii=False))


def _negotiate_run_hook(cmd_template: str, vars: dict) -> tuple:
    """Run a configured external command. Returns (returncode, stdout, stderr)."""
    cmd = cmd_template
    for k, v in vars.items():
        cmd = cmd.replace("{" + k + "}", str(v))
    try:
        proc = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=300)
        return proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired:
        return 124, "", "hook timed out after 300s"


def _negotiate_simulate_one_round(state_path: Path, party_base: Path, party: str, mode: str, stance: Optional[str]) -> dict:
    """Simulate-only helper: run a single counter round in-process."""
    class _A: pass
    a = _A()
    a.base = str(party_base)
    a.state = str(state_path)
    a.out = None
    a.as_party = party
    a.amendments_file = None
    a.auto = (mode == "auto")
    a.agent = (mode == "agent")
    a.stance = stance
    a.llm = "auto"
    a.llm_model = None
    a.llm_base_url = None
    a.yes_llm_send = True
    a.force_unblock = False
    # Capture stdout JSON of cmd_negotiate_counter via redirect.
    import io as _io
    saved_stdout = sys.stdout
    sys.stdout = _io.StringIO()
    try:
        cmd_negotiate_counter(a)
        return json.loads(sys.stdout.getvalue() or "{}")
    finally:
        sys.stdout = saved_stdout


def _negotiate_simulate_init(state_path: Path, party_a_base: Path, party_b_name: str, party_b_address: str) -> None:
    org_policy = _negotiate_load_org_policy(party_a_base)
    party_a_name = org_policy.get("org_name", "Party A")
    class _A: pass
    a = _A()
    a.base = str(party_a_base)
    a.template = "mutual"
    a.out = str(state_path)
    a.purpose = "(simulation)"
    a.effective_date = "2026-01-01"
    a.governing_law = None
    a.party_a_name = party_a_name
    a.party_a_address = "(simulated)"
    a.party_b_name = party_b_name
    a.party_b_address = "(simulated)"
    a.disclosing_party = None
    a.disclosing_party_address = None
    a.receiving_party = None
    a.receiving_party_address = None
    import io as _io
    saved_stdout = sys.stdout
    sys.stdout = _io.StringIO()
    try:
        cmd_negotiate_init(a)
    finally:
        sys.stdout = saved_stdout


def cmd_negotiate_simulate(args):
    """Run both sides of a negotiation on one machine and report the outcome.

    Use case: empirical validation of stance × stance predictions, regression
    testing of the convergence/blocking logic, and exploring the negotiation
    dynamics before committing to a real exchange."""
    party_a_base = Path(args.party_a_base)
    party_b_base = Path(args.party_b_base)
    org_a = _negotiate_load_org_policy(party_a_base)
    org_b = _negotiate_load_org_policy(party_b_base)
    rules = org_a.get("clause_rules", {}) or {}

    state_path = Path(args.state) if args.state else party_a_base / "_simulate_state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    if state_path.exists():
        state_path.unlink()

    _negotiate_simulate_init(
        state_path,
        party_a_base,
        party_b_name=org_b.get("org_name", "Party B"),
        party_b_address="(simulated)",
    )

    # Optionally override stance per side without mutating the user's policy.
    # We do this by writing a temporary stance into the policy file just for
    # the duration of each call; restore at the end.
    def _maybe_apply_stance_override(base: Path, override: Optional[str]):
        if not override:
            return None
        p = base / "config" / "org-policy.json"
        original = p.read_text()
        cfg = json.loads(original)
        cfg.setdefault("defaults", {})["negotiation_stance"] = override
        p.write_text(json.dumps(cfg))
        return original

    def _restore(base: Path, original: Optional[str]):
        if original is None:
            return
        (base / "config" / "org-policy.json").write_text(original)

    saved_a = _maybe_apply_stance_override(party_a_base, args.stance_a)
    saved_b = _maybe_apply_stance_override(party_b_base, args.stance_b)

    trajectory = []

    def _record(round_idx: int):
        s = _negotiate_load(state_path)
        cs = s.get("clause_status", {})
        agreed = sum(1 for v in cs.values() if v.get("status") == "agreed")
        disputed = sum(1 for v in cs.values() if v.get("status") == "disputed")
        proposed = sum(1 for v in cs.values() if v.get("status") == "proposed")
        trajectory.append({
            "round": round_idx,
            "proposer": s["rounds"][round_idx - 1]["proposer"],
            "agreed": agreed,
            "disputed": disputed,
            "proposed": proposed,
            "status": s.get("status"),
        })
        return s

    state = _record(1)  # initial round 1

    outcome = "max_rounds_exceeded"
    rounds_used = 1
    error: Optional[str] = None

    try:
        for round_idx in range(2, args.max_rounds + 1):
            current = _negotiate_load(state_path)
            if current.get("status") in ("converged", "blocked"):
                break
            next_proposer = "b" if current["rounds"][-1]["proposer"] == "a" else "a"
            base = party_b_base if next_proposer == "b" else party_a_base
            stance = args.stance_b if next_proposer == "b" else args.stance_a
            try:
                _negotiate_simulate_one_round(state_path, base, next_proposer, args.mode, stance)
            except SystemExit as e:
                error = f"Round {round_idx} ({next_proposer}) raised: {e}"
                break
            state = _record(round_idx)
            rounds_used = round_idx
            if state.get("status") == "converged":
                outcome = "converged"
                break
            if state.get("status") == "blocked":
                outcome = "blocked"
                break
        else:
            outcome = "max_rounds_exceeded"
    finally:
        _restore(party_a_base, saved_a)
        _restore(party_b_base, saved_b)

    # Build winner-per-clause for converged outcomes: who proposed the
    # final-accepted text for each clause? (last_proposer wins.)
    winner_per_clause = {}
    final = _negotiate_load(state_path)
    for clause, st in final.get("clause_status", {}).items():
        if st.get("status") == "agreed":
            winner_per_clause[clause] = st.get("last_proposer")

    report = {
        "outcome": outcome,
        "rounds_used": rounds_used,
        "max_rounds": args.max_rounds,
        "stances": {
            "a": args.stance_a or _negotiate_resolve_stance(org_a, None),
            "b": args.stance_b or _negotiate_resolve_stance(org_b, None),
        },
        "mode": args.mode,
        "final_status": final.get("status"),
        "final_clause_status": {k: v.get("status") for k, v in final.get("clause_status", {}).items()},
        "winner_per_clause": winner_per_clause,
        "trajectory": trajectory,
        "block_diagnosis": final.get("block_diagnosis"),
        "error": error,
        "state_file": str(state_path),
    }
    if args.out:
        Path(args.out).write_text(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False))

    print(json.dumps(report, indent=2, ensure_ascii=False))
    _print_friendly(
        title=f"Simulation: {report['stances']['a']} × {report['stances']['b']} ({args.mode})",
        lines=[
            f"Outcome: {outcome}  ({rounds_used}/{args.max_rounds} rounds)",
            f"Final status: {final.get('status')}",
            f"Agreed: {sum(1 for v in final.get('clause_status', {}).values() if v.get('status') == 'agreed')}",
            f"Disputed: {sum(1 for v in final.get('clause_status', {}).values() if v.get('status') == 'disputed')}",
        ],
        next_steps=(
            ["Stalemate diagnosed. Try the same simulation with one side compromising, or with --mode agent."]
            if outcome == "blocked" else
            (["Converged. Trajectory shows how concessions accumulated."] if outcome == "converged" else
             ["Did not converge within max-rounds. Either bump --max-rounds or investigate the trajectory."])
        ),
    )


def _negotiate_key_points(state: dict, org_policy: dict) -> list:
    """Build the focused key-points list used by sign-off.

    Includes:
      - Clauses whose final text differs from round 1's text
      - Clauses with active red-flag patterns in the final text
      - Every amendment that was applied in the converged outcome
        (especially `agent:` and `auto:` sourced ones)
    """
    rules = org_policy.get("clause_rules", {}) or {}
    rounds = state["rounds"]
    initial_text = rounds[0]["text"]
    final_text = rounds[-1]["text"]
    key_points = []

    for clause in sorted(rules.keys()):
        initial_block = _negotiate_extract_clause_text(initial_text, clause)
        final_block = _negotiate_extract_clause_text(final_text, clause)
        if not initial_block and not final_block:
            continue
        changed = (initial_block.strip() != final_block.strip()) and bool(initial_block) and bool(final_block)
        red_flags = red_flag_hits(final_block.lower(), clause) if final_block else []
        if changed or red_flags:
            key_points.append({
                "clause": clause,
                "changed_from_initial": changed,
                "red_flags_active": [r for r in red_flags] if red_flags else [],
                "initial_text_excerpt": (initial_block[:200] + "...") if len(initial_block) > 200 else initial_block,
                "final_text_excerpt": (final_block[:200] + "...") if len(final_block) > 200 else final_block,
            })

    applied_amendments = []
    for r in rounds[1:]:
        for am in r.get("amendments", []) or []:
            applied_amendments.append({
                "round": r["round"],
                "proposed_by": r["proposer"],
                "source": r.get("amendment_source", "?"),
                "stance": r.get("stance"),
                "clause": am.get("clause"),
                "rationale": am.get("rationale", ""),
                "new_text_excerpt": (am.get("new_text", "")[:200] + "...") if len(am.get("new_text", "")) > 200 else am.get("new_text", ""),
            })

    # Fatigue concessions deserve explicit human attention — these are
    # clauses where one party gave ground after a long bounce streak rather
    # than because of stance/priority logic. Reviewers should sanity-check
    # them before signing off.
    fatigue_concessions = []
    for r in rounds[1:]:
        for clause in r.get("fatigue_concessions") or []:
            fatigue_concessions.append({
                "round": r["round"],
                "conceded_by": r["proposer"],
                "clause": clause,
                "note": "Force-conceded after the clause bounced past the max-bounces threshold.",
            })

    return [
        {"kind": "clause_evolution", "items": key_points},
        {"kind": "applied_amendments", "items": applied_amendments},
        {"kind": "fatigue_concessions", "items": fatigue_concessions},
    ]


def cmd_negotiate_diff(args):
    """Show clause-by-clause changes between two rounds. Defaults to comparing
    the most recent two rounds; pass --from N --to M for a specific range.
    Output mirrors review/draft style: JSON to stdout, optional --md for human-friendly markdown."""
    state = _negotiate_load(Path(args.state))
    rounds = state["rounds"]
    if len(rounds) < 2:
        raise SystemExit("Need at least 2 rounds for a diff.")

    to_idx = (args.to_round - 1) if args.to_round is not None else len(rounds) - 1
    from_idx = (args.from_round - 1) if args.from_round is not None else to_idx - 1
    if from_idx < 0 or to_idx >= len(rounds) or from_idx >= to_idx:
        raise SystemExit(f"Invalid round range: from={from_idx + 1} to={to_idx + 1}.")
    from_r, to_r = rounds[from_idx], rounds[to_idx]

    # Walk policy clauses (or any extracted clause) to find per-clause diffs.
    repo_base = Path(args.base)
    org_policy = _negotiate_load_org_policy(repo_base) if (repo_base / "config" / "org-policy.json").exists() else {}
    rules = (org_policy.get("clause_rules") or {}) or {}
    clauses = list(rules.keys()) if rules else sorted(set(
        am.get("clause") for r in rounds for am in (r.get("amendments") or []) if am.get("clause")
    ))

    diff_items = []
    for clause in clauses:
        old_block = _negotiate_extract_clause_text(from_r["text"], clause)
        new_block = _negotiate_extract_clause_text(to_r["text"], clause)
        if old_block != new_block and (old_block or new_block):
            # Locate the amendment that introduced this change, if any.
            amendment = next(
                (am for am in (to_r.get("amendments") or []) if am.get("clause") == clause),
                None,
            )
            diff_items.append({
                "clause": clause,
                "old_text": old_block,
                "new_text": new_block,
                "proposed_by": to_r["proposer"] if amendment else None,
                "rationale": (amendment or {}).get("rationale", "(text changed without explicit amendment record)"),
            })

    accepted_in_to = to_r.get("accept_clauses") or []

    payload = {
        "ok": True,
        "from_round": from_r["round"],
        "to_round": to_r["round"],
        "to_round_proposer": to_r["proposer"],
        "to_round_source": to_r.get("amendment_source"),
        "to_round_stance": to_r.get("stance"),
        "changes": diff_items,
        "accepted_clauses_in_to_round": accepted_in_to,
        "fatigue_concessions_in_to_round": to_r.get("fatigue_concessions") or [],
    }
    print(json.dumps(payload, indent=2, ensure_ascii=False))

    if args.out_md:
        lines = [
            f"# Negotiation diff — round {from_r['round']} → round {to_r['round']}",
            "",
            f"- Round {to_r['round']} proposer: **Party {to_r['proposer'].upper()}**",
            f"- Source: `{to_r.get('amendment_source')}` (stance: {to_r.get('stance')})",
            "",
        ]
        if diff_items:
            lines.append(f"## Clause changes ({len(diff_items)})")
            lines.append("")
            for d in diff_items:
                lines.append(f"### {d['clause']}")
                lines.append(f"- _Proposed by_: Party {(d['proposed_by'] or '?').upper()}")
                lines.append(f"- _Rationale_: {d['rationale']}")
                lines.append("```diff")
                for old_line in (d["old_text"] or "").splitlines():
                    lines.append(f"- {old_line}")
                for new_line in (d["new_text"] or "").splitlines():
                    lines.append(f"+ {new_line}")
                lines.append("```")
                lines.append("")
        else:
            lines.append("_No clause-text changes detected between these rounds._")
            lines.append("")
        if accepted_in_to:
            lines.append(f"## Clauses accepted in round {to_r['round']}")
            lines.append("")
            lines.extend(f"- {c}" for c in accepted_in_to)
        if to_r.get("fatigue_concessions"):
            lines.append("")
            lines.append("## Fatigue concessions")
            lines.append("")
            lines.extend(f"- {c}" for c in to_r["fatigue_concessions"])
        Path(args.out_md).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out_md).write_text("\n".join(lines))


def cmd_negotiate_withdraw(args):
    """Mark the negotiation as withdrawn by one party. Blocks further commands."""
    base = Path(args.base)
    state = _negotiate_load(Path(args.state))
    if state.get("status") in ("withdrawn", "finalized"):
        raise SystemExit(f"Negotiation already in terminal state: {state.get('status')!r}.")
    party = _negotiate_resolve_party(state, args.as_party, base)
    state["status"] = "withdrawn"
    state["withdrawal"] = {
        "withdrawn_by": party,
        "withdrawn_at": datetime.now(timezone.utc).isoformat(),
        "reason": args.reason or "(no reason given)",
    }
    _negotiate_save(Path(args.state), state)
    print(json.dumps({"ok": True, "status": "withdrawn", "withdrawn_by": party, "reason": state["withdrawal"]["reason"]}, ensure_ascii=False))
    _print_friendly(
        title=f"Negotiation withdrawn (Party {party.upper()})",
        lines=[
            f"Reason: {state['withdrawal']['reason']}",
            f"State file marked terminal — no further counter/sign-off/finalize allowed.",
        ],
        next_steps=["Notify the other party out-of-band that you've withdrawn."],
    )


def cmd_negotiate_analyze(args):
    """Read-only post-hoc dashboard for any state file. Shows the negotiation's
    trajectory, source breakdown (manual / auto / agent / fatigue), per-clause
    winner, fatigue summary, and a lightweight game-theoretic interpretation
    of the outcome."""
    state = _negotiate_load(Path(args.state))
    rounds = state.get("rounds", [])
    if not rounds:
        raise SystemExit("State file has no rounds — nothing to analyze.")

    # Trajectory: per-round agreed/disputed/proposed counts. We recompute
    # from policy if available, else from amendment history.
    base = Path(args.base)
    org_policy = _negotiate_load_org_policy(base) if (base / "config" / "org-policy.json").exists() else {}
    rules = (org_policy.get("clause_rules") or {}) or {}

    trajectory = []
    for i in range(len(rounds)):
        truncated = {**state, "rounds": rounds[: i + 1]}
        cs = _negotiate_recompute_clause_status(truncated, rules)
        trajectory.append({
            "round": rounds[i]["round"],
            "proposer": rounds[i]["proposer"],
            "amendment_source": rounds[i].get("amendment_source", "?"),
            "stance": rounds[i].get("stance"),
            "agreed": sum(1 for v in cs.values() if v.get("status") == "agreed"),
            "disputed": sum(1 for v in cs.values() if v.get("status") == "disputed"),
            "proposed": sum(1 for v in cs.values() if v.get("status") == "proposed"),
        })

    # Source breakdown across all rounds
    src_counter = Counter(r.get("amendment_source", "?") for r in rounds)

    # Winner per agreed clause
    final_status = state.get("clause_status") or _negotiate_recompute_clause_status(state, rules)
    winners = {c: s.get("last_proposer") for c, s in final_status.items() if s.get("status") == "agreed"}
    winner_count = Counter(winners.values())

    # Fatigue / non-negotiable summary
    fatigue_clauses = []
    for r in rounds:
        for c in r.get("fatigue_concessions") or []:
            fatigue_clauses.append({"round": r["round"], "conceded_by": r["proposer"], "clause": c})

    # Concession trajectory: per-round count of accept_clauses by proposer
    concession_trajectory = [
        {"round": r["round"], "proposer": r["proposer"], "accepted": len(r.get("accept_clauses") or [])}
        for r in rounds
    ]

    # Game-theoretic interpretation
    outcome_interpretation = _negotiate_interpret_outcome(state, trajectory, src_counter)

    payload = {
        "negotiation_id": state.get("negotiation_id"),
        "status": state.get("status"),
        "rounds_total": len(rounds),
        "stances": {
            "a_initial": rounds[0].get("stance"),  # round 1 is the initial draft (stance may be None)
            "observed_per_round": [{"round": r["round"], "proposer": r["proposer"], "stance": r.get("stance")} for r in rounds],
        },
        "trajectory": trajectory,
        "source_breakdown": dict(src_counter),
        "winner_per_clause": winners,
        "wins_by_party": {"a": winner_count.get("a", 0), "b": winner_count.get("b", 0)},
        "fatigue_concessions": fatigue_clauses,
        "concession_trajectory": concession_trajectory,
        "outcome_interpretation": outcome_interpretation,
        "block_diagnosis": state.get("block_diagnosis"),
    }
    print(json.dumps(payload, indent=2, ensure_ascii=False))

    if args.out_md:
        lines = [
            f"# Negotiation analysis — `{state.get('negotiation_id', '?')}`",
            "",
            f"- **Status**: `{state.get('status')}`",
            f"- **Outcome**: {outcome_interpretation['label']}",
            f"- **Rounds**: {len(rounds)}",
            f"- **Parties**: A = {state['parties'].get('a', {}).get('name')}, B = {state['parties'].get('b', {}).get('name')}",
            "",
        ]
        if outcome_interpretation.get("notes"):
            lines.append("**Notes:**")
            lines.append("")
            for n in outcome_interpretation["notes"]:
                lines.append(f"- {n}")
            lines.append("")

        lines.append("## Trajectory")
        lines.append("")
        lines.append("| Round | Proposer | Source | Stance | Agreed | Disputed | Proposed |")
        lines.append("|---|---|---|---|---|---|---|")
        for t in trajectory:
            lines.append(
                f"| {t['round']} | {t['proposer'].upper()} | `{t['amendment_source']}` "
                f"| {t.get('stance') or '—'} | {t['agreed']} | {t['disputed']} | {t['proposed']} |"
            )
        lines.append("")

        lines.append("## Wins by party")
        lines.append("")
        lines.append(f"- **Party A** ({state['parties'].get('a', {}).get('name')}): {payload['wins_by_party']['a']} clauses")
        lines.append(f"- **Party B** ({state['parties'].get('b', {}).get('name')}): {payload['wins_by_party']['b']} clauses")
        lines.append("")

        if winners:
            lines.append("## Winner per agreed clause")
            lines.append("")
            for clause, who in sorted(winners.items()):
                lines.append(f"- `{clause}` → Party {(who or '?').upper()}")
            lines.append("")

        lines.append("## Amendment-source breakdown")
        lines.append("")
        for src, n in sorted(src_counter.items()):
            lines.append(f"- `{src}`: {n} round(s)")
        lines.append("")

        if fatigue_clauses:
            lines.append("## Fatigue concessions (force-resolved)")
            lines.append("")
            lines.append("These clauses were force-conceded after bouncing past the threshold. **Review carefully** — they were not agreed organically.")
            lines.append("")
            for f in fatigue_clauses:
                lines.append(f"- Round {f['round']}, conceded by Party {f['conceded_by'].upper()}: `{f['clause']}`")
            lines.append("")

        if state.get("block_diagnosis"):
            diag = state["block_diagnosis"]
            lines.append("## Block diagnosis")
            lines.append("")
            lines.append(f"- Rounds without progress: {diag.get('rounds_without_progress')}")
            lines.append(f"- Threshold: {diag.get('threshold')}")
            lines.append(f"- Stuck clauses: `{', '.join(diag.get('stuck_clauses', []))}`")
            lines.append("")
            lines.append(f"> {diag.get('note', '')}")
            lines.append("")

        Path(args.out_md).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out_md).write_text("\n".join(lines))


def _negotiate_interpret_outcome(state: dict, trajectory: list, src_counter: Counter) -> dict:
    """Lightweight game-theoretic interpretation of the negotiation."""
    status = state.get("status", "unknown")
    rounds = state.get("rounds", [])
    if not rounds:
        return {"label": "empty", "note": "No rounds."}

    fatigue_used = any("+fatigue" in s for s in src_counter)
    agent_used = any(s.startswith("agent") for s in src_counter)
    auto_used = any(s.startswith("auto") for s in src_counter)
    final_agreed = trajectory[-1]["agreed"] if trajectory else 0
    final_disputed = trajectory[-1]["disputed"] if trajectory else 0

    if status == "finalized":
        label = "finalized"
    elif status == "signed_off":
        label = "signed-off, awaiting finalize"
    elif status == "converged":
        label = "converged via fatigue" if fatigue_used else "converged organically"
    elif status == "blocked":
        label = "blocked (deadlock detected)"
    elif status == "withdrawn":
        label = "withdrawn by one party"
    else:
        label = "in progress"

    notes = []
    if fatigue_used:
        notes.append("Fatigue concession was triggered — at least one clause was force-resolved after bouncing past the threshold. Review carefully.")
    if agent_used and auto_used:
        notes.append("Mixed counter modes (LLM agent and deterministic auto). Source breakdown has details.")
    elif agent_used:
        notes.append("LLM agent drove the negotiation. Verify rationales in the round-by-round amendments.")
    elif auto_used:
        notes.append("Pure deterministic mode — no LLM was used.")
    if final_disputed > 0:
        notes.append(f"{final_disputed} clause(s) remain disputed in the final state.")
    if final_agreed == 0 and rounds:
        notes.append("No clauses ever reached `agreed` status — the negotiation never produced shared ground.")

    return {"label": label, "notes": notes}


def cmd_negotiate_validate(args):
    """Standalone integrity check on a negotiation state file. Verifies the
    schema version, the per-round SHA-256 hash chain end-to-end, and the
    structural shape of every round. Exits 0 on success, 2 on any failure.

    Useful when you receive a state file from another party and want to
    confirm it hasn't been tampered with before processing it, or after
    hand-editing the JSON to catch mistakes."""
    state_path = Path(args.state)
    try:
        state = _negotiate_load(state_path)
    except SystemExit as e:
        print(json.dumps({
            "ok": False,
            "state_file": str(state_path),
            "error": str(e),
        }, indent=2, ensure_ascii=False))
        raise SystemExit(2)

    rounds = state.get("rounds", [])
    issues = []
    expected_alternation = None
    for i, r in enumerate(rounds):
        if not isinstance(r.get("round"), int) or r["round"] != i + 1:
            issues.append(f"Round {i}: 'round' field {r.get('round')!r} doesn't match index {i + 1}")
        if r.get("proposer") not in ("a", "b"):
            issues.append(f"Round {r.get('round')}: proposer {r.get('proposer')!r} not in ('a', 'b')")
        if expected_alternation is not None and r.get("proposer") == expected_alternation:
            issues.append(f"Round {r.get('round')}: same proposer as previous round (no alternation)")
        expected_alternation = r.get("proposer")
        sig = r.get("signature") or {}
        if sig.get("signer") != r.get("proposer"):
            issues.append(f"Round {r.get('round')}: signature.signer {sig.get('signer')!r} != proposer {r.get('proposer')!r}")
        if "text" not in r or "text_hash" not in r:
            issues.append(f"Round {r.get('round')}: missing text or text_hash")

    payload = {
        "ok": not issues,
        "state_file": str(state_path),
        "schema_version": state.get("schema_version"),
        "negotiation_id": state.get("negotiation_id"),
        "status": state.get("status"),
        "rounds_total": len(rounds),
        "hash_chain_verified": True,  # _negotiate_load already verified or raised
        "structural_issues": issues,
    }
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    if issues:
        raise SystemExit(2)


def cmd_negotiate_signoff(args):
    base = Path(args.base)
    state = _negotiate_load(Path(args.state))
    if state.get("status") not in ("converged", "signed_off"):
        raise SystemExit(
            f"Negotiation status is {state.get('status')!r}. "
            "Sign-off requires the negotiation to be `converged` first."
        )
    org_policy = _negotiate_load_org_policy(base)
    party = _negotiate_resolve_party(state, args.as_party, base)
    key_points = _negotiate_key_points(state, org_policy)

    print(json.dumps({
        "ok": True,
        "negotiation_id": state["negotiation_id"],
        "you_are": party,
        "key_points": key_points,
    }, indent=2, ensure_ascii=False))

    # Friendly summary to stderr.
    clause_changes = key_points[0]["items"]
    amendments = key_points[1]["items"]
    fatigue = key_points[2]["items"] if len(key_points) > 2 else []
    summary_lines = [
        f"Clauses changed from initial draft: {sum(1 for c in clause_changes if c['changed_from_initial'])}",
        f"Clauses with active red flags in final text: {sum(1 for c in clause_changes if c['red_flags_active'])}",
        f"Total amendments applied across rounds: {len(amendments)}",
        f"Fatigue-conceded clauses (force-resolved after bouncing): {len(fatigue)}",
        "",
        "Sources:",
    ]
    src_counts = Counter(a["source"] for a in amendments)
    for src, n in sorted(src_counts.items()):
        summary_lines.append(f"  - {src}: {n}")
    if fatigue:
        summary_lines.append("")
        summary_lines.append("Fatigue concessions (review carefully):")
        for f in fatigue:
            summary_lines.append(f"  - round {f['round']}, conceded by Party {f['conceded_by'].upper()}: {f['clause']}")

    interactive = sys.stdin.isatty() and not args.yes
    if interactive:
        print("", file=sys.stderr)
        print("  ━━ Sign-off review ━━", file=sys.stderr)
        for line in summary_lines:
            print(f"  {line}", file=sys.stderr)
        if clause_changes:
            print("\n  Key clause changes:", file=sys.stderr)
            for c in clause_changes:
                marker = "[red flags]" if c["red_flags_active"] else "[changed]"
                print(f"    {marker} {c['clause']}", file=sys.stderr)
                if c['red_flags_active']:
                    print(f"        → {', '.join(c['red_flags_active'][:3])}", file=sys.stderr)
        try:
            ok = input("\n  Sign off all key points and approve final text? [Y/n]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            raise SystemExit("Aborted.")
        if ok not in ("", "y", "yes"):
            raise SystemExit("Sign-off declined. State unchanged.")

    # Record sign-off.
    state.setdefault("signoffs", {})
    state["signoffs"][party] = {
        "signed_at": datetime.now(timezone.utc).isoformat(),
        "key_points_count": len(clause_changes) + len(amendments),
        "method": "json_flag",
    }
    if all(p in state["signoffs"] for p in ("a", "b")):
        state["status"] = "signed_off"

    _negotiate_save(Path(args.state), state)

    next_step = (
        "Run `negotiate finalize` to emit the agreed .md/.docx (and optionally hand off to docx2pdf + sign-CLI)."
        if state["status"] == "signed_off"
        else f"Other party still needs to run `negotiate sign-off`."
    )
    _print_friendly(
        title=f"Sign-off recorded (Party {party.upper()})",
        lines=summary_lines,
        next_steps=[next_step],
    )


def cmd_negotiate_finalize(args):
    base = Path(args.base)
    state = _negotiate_load(Path(args.state))
    if state.get("status") not in ("converged", "signed_off"):
        raise SystemExit(
            f"Negotiation status is {state.get('status')!r}, not 'converged' or 'signed_off'. "
            "Both parties must alternate-sign to a state with no disputed clauses."
        )
    signoffs = state.get("signoffs") or {}
    missing = [p for p in ("a", "b") if p not in signoffs]
    if missing and not args.skip_signoff:
        raise SystemExit(
            f"Sign-off missing from party/parties: {missing}. "
            "Each party must run `negotiate sign-off` before finalize. "
            "Pass --skip-signoff only for testing or non-binding finalizations."
        )

    last = state["rounds"][-1]
    final_text = last["text"]
    out_md = Path(args.out_md)
    out_docx = Path(args.out_docx)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_docx.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text(final_text)
    md_to_docx(final_text, out_docx)

    pdf_path = None
    signed_pdf_path = None
    integrations = {}
    integ_path = base / "config" / "integrations.json"
    if integ_path.exists():
        try:
            integrations = json.loads(integ_path.read_text())
        except Exception as e:
            print(f"Warning: could not parse {integ_path}: {e}", file=sys.stderr)

    hook_log = []
    if args.to_pdf:
        cmd_t = integrations.get("docx2pdf_cmd")
        if not cmd_t:
            raise SystemExit("--to-pdf requested but `docx2pdf_cmd` not configured in config/integrations.json.")
        pdf_path = str(out_docx.with_suffix(".pdf"))
        rc, so, se = _negotiate_run_hook(cmd_t, {
            "input_docx": str(out_docx),
            "output_pdf": pdf_path,
        })
        hook_log.append({"hook": "docx2pdf", "returncode": rc, "stdout": so[-500:], "stderr": se[-500:]})
        if rc != 0:
            raise SystemExit(f"docx2pdf hook failed (rc={rc}): {se[-300:]}")

    if args.sign:
        cmd_t = integrations.get("sign_cli_cmd")
        if not cmd_t:
            raise SystemExit("--sign requested but `sign_cli_cmd` not configured in config/integrations.json.")
        if not pdf_path:
            raise SystemExit("--sign requires --to-pdf (sign-CLI operates on the PDF).")
        signed_pdf_path = str(Path(pdf_path).with_name(Path(pdf_path).stem + ".signed.pdf"))
        rc, so, se = _negotiate_run_hook(cmd_t, {
            "input_pdf": pdf_path,
            "output_pdf": signed_pdf_path,
            "party_a_name": state["parties"]["a"]["name"],
            "party_b_name": state["parties"]["b"]["name"],
            "negotiation_id": state["negotiation_id"],
        })
        hook_log.append({"hook": "sign_cli", "returncode": rc, "stdout": so[-500:], "stderr": se[-500:]})
        if rc != 0:
            raise SystemExit(f"sign-CLI hook failed (rc={rc}): {se[-300:]}")

    state["finalized"] = {
        "finalized_at": datetime.now(timezone.utc).isoformat(),
        "final_text_hash": last["text_hash"],
        "out_md": str(out_md),
        "out_docx": str(out_docx),
        "out_pdf": pdf_path,
        "signed_pdf": signed_pdf_path,
        "hooks": hook_log,
    }
    state["status"] = "finalized"
    _negotiate_save(Path(args.state), state)
    print(json.dumps({"ok": True, "finalized": state["finalized"], "state_file": args.state}, indent=2, ensure_ascii=False))
    _print_friendly(
        title="Negotiation finalized",
        lines=[
            f"Markdown: {out_md}",
            f"Word doc: {out_docx}",
            f"PDF:      {pdf_path or '(skipped)'}",
            f"Signed:   {signed_pdf_path or '(skipped)'}",
            f"Hash:     {last['text_hash'][:16]}...",
        ],
        next_steps=(
            ["Distribute the signed PDF to both parties for archival."]
            if signed_pdf_path else
            ["Sign and distribute the .docx via your usual channel.",
             "Or wire up `docx2pdf_cmd` and `sign_cli_cmd` in config/integrations.json and rerun with --to-pdf --sign."]
        ),
    )


def cmd_tutorial(args):
    repo = Path(__file__).resolve().parent
    interactive = sys.stdin.isatty() and not args.no_prompt

    def pause(prompt="Press Enter to continue, or Ctrl-C to quit. "):
        if interactive:
            try:
                input(prompt)
            except (EOFError, KeyboardInterrupt):
                print("\nTutorial aborted.")
                raise SystemExit(0)

    for step in TUTORIAL_STEPS:
        print()
        print(f"  ━━ {step['title']} ━━")
        for line in step["body"]:
            print(f"  {line}")
        pause()

    if not args.run_sample and interactive:
        try:
            choice = input("\n  Run a sample setup + review now? [Y/n]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            choice = "n"
        run_sample = choice in ("", "y", "yes")
    else:
        run_sample = args.run_sample

    if not run_sample:
        print("\n  Skipped sample run. When you're ready:")
        print("    1. ./nda_review_cli.py setup --quick --yes")
        print("    2. ./nda_review_cli.py review --file tests/fixtures/sample_nda.txt --why")
        print("    3. ./nda_review_cli.py doctor")
        return

    import tempfile

    workdir = Path(args.base) if args.base else Path(tempfile.mkdtemp(prefix="nda-tutorial-"))
    workdir.mkdir(parents=True, exist_ok=True)
    print(f"\n  Sandbox: {workdir}")
    print("  Running: setup --quick --yes --no-prompt")

    class Obj:
        pass

    setup_args = Obj()
    setup_args.base = str(workdir)
    setup_args.interactive = False
    setup_args.org_name = "Tutorial Org"
    setup_args.template = None
    setup_args.risk_posture = "balanced"
    setup_args.preferred_jurisdictions = "Austria"
    setup_args.survival_years = 5
    setup_args.ai_policy = "guardrailed"
    setup_args.retention_carveout = "Allow limited backup/legal retention under continuing confidentiality obligations."
    setup_args.default_policy = "config/default-policy.json"
    setup_args.policy = None
    setup_args.ingest_files = None
    setup_args.contracts_dir = None
    setup_args.drive_export_dir = None
    setup_args.build = False
    setup_args.no_build = True
    setup_args.quick = True
    setup_args.yes = True
    setup_args.no_prompt = True
    setup_args.scoring_profile = None
    setup_args.scoring_profiles = None
    cmd_setup(setup_args)

    # Seed minimal corpus stubs so build-playbook produces a usable artifact.
    raw = workdir / "data" / "raw_strict"
    raw.mkdir(parents=True, exist_ok=True)
    sample_gmail = [
        {"id": "1", "subject": "NDA review (mutual)", "body": "Mutual NDA, return or destroy on termination, governing law Austria.", "from": "legal@example.com"},
        {"id": "2", "subject": "Re: NDA accepted", "body": "Term 3 years, trade secret survival indefinite, looks good.", "from": "ops@example.com"},
    ]
    sample_drive = [{"id": "d1", "name": "NDA playbook notes", "body": "Use restrictions limited to evaluation purposes."}]
    for name in ("gmail_primary.json", "gmail_secondary.json"):
        (raw / name).write_text(json.dumps(sample_gmail))
    for name in ("drive_primary.json", "drive_secondary.json"):
        (raw / name).write_text(json.dumps(sample_drive))

    print("\n  Building sample playbook from seeded corpus stubs...")
    build_args = Obj()
    build_args.base = str(workdir)
    build_args.policy = None
    build_args.gmail_paths = ["data/raw_strict/gmail_primary.json", "data/raw_strict/gmail_secondary.json"]
    build_args.drive_paths = ["data/raw_strict/drive_primary.json", "data/raw_strict/drive_secondary.json"]
    build_args.out_json = "output/nda_playbook.json"
    build_args.out_md = "output/nda_playbook.md"
    cmd_build(build_args)

    sample = repo / "tests" / "fixtures" / "sample_nda.txt"
    if not sample.exists():
        print(f"\n  Sample fixture missing: {sample}")
        return

    print(f"\n  Reviewing: {sample}")
    review_args = Obj()
    review_args.base = str(workdir)
    review_args.playbook = str(workdir / "output" / "nda_playbook.json")
    review_args.counterparty = None
    review_args.file = str(sample)
    review_args.text = None
    review_args.out_json = str(workdir / "output" / "reviews" / "tutorial-review.json")
    review_args.out_md = str(workdir / "output" / "reviews" / "tutorial-review.md")
    review_args.why = True
    review_args.learn_profile = False
    review_args.scoring_profile = None
    review_args.scoring_profiles = None
    Path(review_args.out_json).parent.mkdir(parents=True, exist_ok=True)
    cmd_review(review_args)

    print()
    print("  ━━ Done ━━")
    print(f"  Review JSON: {review_args.out_json}")
    print(f"  Review MD:   {review_args.out_md}")
    print()
    print("  Next steps:")
    print("    • Open the markdown summary in your editor.")
    print("    • Run `./nda_review_cli.py doctor` against your real workspace.")
    print("    • Read GETTING_STARTED.md for the full happy-path guide.")
    print()


def cmd_wizard(args):
    base = Path(args.base)
    interactive = not args.no_prompt and sys.stdin.isatty()

    class Obj:
        pass

    setup_args = Obj()
    setup_args.base = str(base)
    setup_args.interactive = False
    setup_args.org_name = args.org_name
    setup_args.template = args.template
    setup_args.risk_posture = args.risk_posture
    setup_args.preferred_jurisdictions = args.preferred_jurisdictions
    setup_args.survival_years = args.survival_years
    setup_args.ai_policy = args.ai_policy
    setup_args.retention_carveout = args.retention_carveout
    setup_args.default_policy = args.default_policy
    setup_args.policy = args.policy
    setup_args.ingest_files = args.ingest_files
    setup_args.build = args.build
    setup_args.no_build = args.no_build
    setup_args.quick = args.quick
    setup_args.yes = args.yes
    setup_args.no_prompt = args.no_prompt
    setup_args.scoring_profile = args.scoring_profile
    setup_args.scoring_profiles = args.scoring_profiles

    if interactive:
        if not setup_args.org_name:
            setup_args.org_name = _prompt_with_default("Organization name", "Your Org")
        if not setup_args.template:
            setup_args.template = _prompt_with_default("Template (saas/healthcare/enterprise or blank)", "")
            setup_args.template = setup_args.template or None
        if not args.ingest_files and _prompt_yes_no("Run ingest after setup?", True):
            source_mode = _prompt_with_default("Ingest source (files/contracts-dir/drive-export-dir/auto)", "auto")
            if source_mode == "files":
                setup_args.ingest_files = parse_paths_input(input("Enter file paths: ").strip())
            elif source_mode == "contracts-dir":
                args.contracts_dir = _prompt_with_default("Contracts directory", str(base / "knowledge" / "contracts"))
            elif source_mode == "drive-export-dir":
                args.drive_export_dir = _prompt_with_default("Drive export directory", str(base))
        if not args.build and not args.no_build:
            setup_args.build = _prompt_yes_no("Build playbook after ingest?", True)
        if not args.review_file and not args.review_text:
            if _prompt_yes_no("Run review at the end?", True):
                args.review_file = _prompt_with_default("Review file path", str(Path(__file__).resolve().parent / "tests" / "fixtures" / "sample_nda.txt"))

    cmd_setup(setup_args)

    if args.contracts_dir or args.drive_export_dir:
        ingest_args = Obj()
        ingest_args.base = str(base)
        ingest_args.policy = args.policy
        ingest_args.files = []
        ingest_args.contracts_dir = args.contracts_dir
        ingest_args.drive_export_dir = args.drive_export_dir
        ingest_args.no_prompt = True
        ingest_args.yes = True
        cmd_ingest(ingest_args)

    if args.review_file or args.review_text:
        review_args = Obj()
        review_args.base = str(base)
        review_args.playbook = args.playbook or str(base / "output" / "nda_playbook.json")
        review_args.counterparty = args.counterparty
        review_args.file = args.review_file
        review_args.text = args.review_text
        review_args.out_json = args.out_json
        review_args.out_md = args.out_md
        review_args.why = args.why
        review_args.learn_profile = args.learn_profile
        review_args.scoring_profile = args.scoring_profile
        review_args.scoring_profiles = args.scoring_profiles
        cmd_review(review_args)


FIRST_RUN_HINT = (
    "\nNDA Review CLI — local-first NDA review and drafting.\n\n"
    "First time? Try one of:\n"
    "  ./nda_review_cli.py tutorial            # interactive primer + sample review\n"
    "  ./nda_review_cli.py quickstart          # 14-question guided setup\n"
    "  ./nda_review_cli.py setup --quick --yes # zero-friction defaults\n"
    "\nCommon commands:\n"
    "  review --file <nda>                     # score an NDA against your playbook\n"
    "  draft --template mutual ...             # generate an outgoing NDA\n"
    "  doctor                                  # diagnose first-run readiness\n"
    "\nSee `--help` for the full list, or read GETTING_STARTED.md.\n"
)


def main():
    parser = argparse.ArgumentParser(
        prog="nda-review-cli",
        description="Local-first NDA review and drafting CLI (deterministic + optional LLM augmentation).",
    )
    parser.add_argument("--version", action="version", version=f"nda-review-cli {__version__}")
    # `cmd` is technically required, but we want a friendlier message than argparse's
    # default when the user runs the tool with no args at all.
    if len(sys.argv) == 1:
        print(FIRST_RUN_HINT, file=sys.stderr)
        raise SystemExit(0)
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
    p_review.add_argument("--why", action="store_true", help="Include concise explainability evidence for each finding")
    p_review.add_argument("--learn-profile", action="store_true", help="Write deterministic counterparty profile updates from this review")
    p_review.add_argument("--scoring-profile", help="Scoring profile name")
    p_review.add_argument("--scoring-profiles", help="Path to scoring profiles JSON")
    p_review.add_argument(
        "--llm",
        nargs="?",
        const="auto",
        choices=["auto", "anthropic", "openai", "ollama", "openai-compatible"],
        help="Opt-in: also run a second-pass LLM review (votes + new findings + clause suggestions). 'auto' uses provider from config/llm.json or NDA_LLM_PROVIDER.",
    )
    p_review.add_argument("--llm-model", help="Override the LLM model id (e.g. claude-sonnet-4-6, gpt-4o-mini, qwen2.5:14b)")
    p_review.add_argument("--llm-base-url", help="Override the LLM base URL (useful for openai-compatible / Ollama / local servers)")
    p_review.add_argument("--yes-llm-send", action="store_true", help="Skip the per-call confirmation that NDA text is being sent to the LLM provider")
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
    p_red.add_argument("--mode", choices=["classic", "v2"], default="classic")
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
    p_init.add_argument("--scoring-profile", help="Scoring profile name")
    p_init.add_argument("--scoring-profiles", help="Path to scoring profiles JSON")
    p_init.set_defaults(func=cmd_init)

    p_ingest = sub.add_parser("ingest", help="Ingest existing contracts/playbooks and propose policy/profile updates")
    p_ingest.add_argument("--base", default=str(Path(__file__).resolve().parent))
    p_ingest.add_argument("--policy", help="Policy config path", default="config/org-policy.json")
    p_ingest.add_argument("--files", nargs="*", help="Optional files to ingest. If omitted, auto-discovers from knowledge/inbox, knowledge/contracts, knowledge/redlines, inbox, input")
    p_ingest.add_argument("--contracts-dir", help="Shortcut: recurse through a local contracts directory and ingest supported files")
    p_ingest.add_argument("--drive-export-dir", help="Shortcut: recurse through a local Google Drive export folder and ingest supported files")
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
    p_setup.add_argument("--contracts-dir", help="Shortcut: recurse through a local contracts directory during setup")
    p_setup.add_argument("--drive-export-dir", help="Shortcut: recurse through a local Google Drive export folder during setup")
    p_setup.add_argument("--build", action="store_true", help="Run build-playbook at the end of setup")
    p_setup.add_argument("--no-build", action="store_true", help="Skip build-playbook at the end of setup, including quick mode default build")
    p_setup.add_argument("--quick", action="store_true", help="Zero-friction onboarding: defaults + auto-ingest discovery")
    p_setup.add_argument("--yes", action="store_true", help="Approve auto-discovered files without confirmation")
    p_setup.add_argument("--no-prompt", action="store_true", help="Do not prompt for file paths when none are found")
    p_setup.add_argument("--scoring-profile", help="Scoring profile name")
    p_setup.add_argument("--scoring-profiles", help="Path to scoring profiles JSON")
    p_setup.set_defaults(func=cmd_setup)

    p_profile = sub.add_parser("profile-learn", help="Learn or update a counterparty profile from a saved review JSON")
    p_profile.add_argument("--base", default=str(Path(__file__).resolve().parent))
    p_profile.add_argument("--review-json", required=True)
    p_profile.add_argument("--counterparty")
    p_profile.set_defaults(func=cmd_profile_learn)

    p_cal = sub.add_parser("calibrate-scoring", help="Evaluate a labeled validation set against a scoring profile")
    p_cal.add_argument("--base", default=str(Path(__file__).resolve().parent))
    p_cal.add_argument("--playbook", default=str(Path(__file__).resolve().parent / "output/nda_playbook.json"))
    p_cal.add_argument("--validation-set", required=True)
    p_cal.add_argument("--scoring-profile", default="balanced")
    p_cal.add_argument("--scoring-profiles", help="Path to scoring profiles JSON")
    p_cal.add_argument("--out-json")
    p_cal.set_defaults(func=cmd_calibrate_scoring)

    p_release = sub.add_parser("release-helper", help="Generate release notes from CHANGELOG and suggest a git tag command")
    p_release.add_argument("--changelog", default=str(Path(__file__).resolve().parent / "CHANGELOG.md"))
    p_release.add_argument("--version", required=True)
    p_release.add_argument("--out")
    p_release.set_defaults(func=cmd_release_helper)

    p_quick = sub.add_parser("quickstart", help="Guided 14-question setup: collects org/risk/clause preferences, writes config + profile, optionally ingests + samples a review")
    p_quick.add_argument("--base", default=str(Path(__file__).resolve().parent))
    p_quick.add_argument("--no-prompt", action="store_true", help="Skip prompts; use defaults (CI-friendly)")
    p_quick.add_argument("--yes", action="store_true", help="Skip the final apply confirmation")
    p_quick.add_argument("--answers-file", help="Replay from a previously-saved quickstart-answers.json")
    p_quick.add_argument("--default-policy", default="config/default-policy.json", help="Seed policy used as the clause-rules base")
    p_quick.set_defaults(func=cmd_quickstart)

    p_draft = sub.add_parser("draft", help="Draft an NDA to send out, using a built-in template (mutual / one-way-out) or your own --template-file")
    p_draft.add_argument("--base", default=str(Path(__file__).resolve().parent))
    p_draft.add_argument("--template", choices=sorted(DRAFT_TEMPLATES.keys()), help="Built-in template; defaults to one suggested by your profile.nda_direction")
    p_draft.add_argument("--template-file", help="Path to a custom .md template with {{placeholders}}")
    p_draft.add_argument("--out", required=True, help="Output markdown path (canonical source)")
    p_draft.add_argument("--out-docx", help="Optional Word .docx output path")
    p_draft.add_argument("--purpose", required=True, help="Purpose / deal description (free text)")
    p_draft.add_argument("--effective-date", help="Effective date (YYYY-MM-DD); defaults to today UTC")
    p_draft.add_argument("--governing-law", help="Override governing law/jurisdiction; defaults to first preferred_jurisdictions entry")
    # Mutual party fields
    p_draft.add_argument("--party-a", help="Mutual: Party A name")
    p_draft.add_argument("--party-a-address", help="Mutual: Party A address")
    p_draft.add_argument("--party-b", help="Mutual: Party B name")
    p_draft.add_argument("--party-b-address", help="Mutual: Party B address")
    # One-way fields
    p_draft.add_argument("--disclosing-party", help="One-way: disclosing party name (defaults to org_name)")
    p_draft.add_argument("--disclosing-party-address", help="One-way: disclosing party address")
    p_draft.add_argument("--receiving-party", help="One-way: receiving party name")
    p_draft.add_argument("--receiving-party-address", help="One-way: receiving party address")
    p_draft.add_argument("--counterparty", help="Optional counterparty profile name to load tone from profiles/<name>.json")
    p_draft.add_argument("--review-after", action="store_true", help="Run the generated draft through `review --why` as a sanity check")
    p_draft.add_argument("--no-disclaimer", action="store_true", help="Omit the 'this is a starting point' header")
    p_draft.set_defaults(func=cmd_draft)

    p_neg = sub.add_parser("negotiate", help="Two-party turn-taking NDA negotiation with optional LLM agent assistance (file-based protocol)")
    neg_sub = p_neg.add_subparsers(dest="neg_cmd", required=True)

    p_ni = neg_sub.add_parser("init", help="Start a negotiation: draft NDA from template + parties, sign as Party A, emit state file")
    p_ni.add_argument("--base", default=str(Path(__file__).resolve().parent))
    p_ni.add_argument("--template", choices=sorted(DRAFT_TEMPLATES.keys()), default="mutual")
    p_ni.add_argument("--out", required=True, help="Output negotiation state JSON file")
    p_ni.add_argument("--purpose", required=True)
    p_ni.add_argument("--effective-date")
    p_ni.add_argument("--governing-law")
    # Mutual party fields
    p_ni.add_argument("--party-a-name")
    p_ni.add_argument("--party-a-address")
    p_ni.add_argument("--party-b-name")
    p_ni.add_argument("--party-b-address")
    # One-way fields
    p_ni.add_argument("--disclosing-party")
    p_ni.add_argument("--disclosing-party-address")
    p_ni.add_argument("--receiving-party")
    p_ni.add_argument("--receiving-party-address")
    p_ni.set_defaults(func=cmd_negotiate_init)

    p_nr = neg_sub.add_parser("review", help="Read-only: review the latest round vs your policy")
    p_nr.add_argument("--base", default=str(Path(__file__).resolve().parent))
    p_nr.add_argument("--state", required=True, help="Negotiation state JSON file")
    p_nr.add_argument("--as", dest="as_party", choices=["a", "b"], help="Which party you are; auto-detected from org_name if omitted")
    p_nr.set_defaults(func=cmd_negotiate_review)

    p_nc = neg_sub.add_parser("counter", help="Sign a counter-round with amendments (manual or LLM-drafted)")
    p_nc.add_argument("--base", default=str(Path(__file__).resolve().parent))
    p_nc.add_argument("--state", required=True)
    p_nc.add_argument("--out", help="Where to write the updated state file (defaults to overwriting --state)")
    p_nc.add_argument("--as", dest="as_party", choices=["a", "b"])
    p_nc.add_argument("--amendments-file", help="Manual amendments JSON file: {accept_clauses, counter_amendments, summary}")
    p_nc.add_argument("--auto", action="store_true", help="Deterministic stance-driven amendment generator (no LLM). Uses your policy + negotiation_stance.")
    p_nc.add_argument("--dry-run", action="store_true", help="Generate the proposal and print it as JSON, but do NOT write to the state file. Useful for previewing what --agent or --auto will do before committing.")
    p_nc.add_argument("--force-unblock", action="store_true", help="Continue countering even if status is `blocked` (rarely useful)")
    p_nc.add_argument("--stance", choices=sorted(NEGOTIATE_STANCE_DESCRIPTORS.keys()), help="Override negotiation_stance from policy for this round only")
    p_nc.add_argument("--agent", action="store_true", help="Use the configured LLM as a negotiation agent to draft amendments")
    p_nc.add_argument("--llm", choices=["auto", "anthropic", "openai", "ollama", "openai-compatible"], default="auto")
    p_nc.add_argument("--llm-model")
    p_nc.add_argument("--llm-base-url")
    p_nc.add_argument("--yes-llm-send", action="store_true")
    p_nc.set_defaults(func=cmd_negotiate_counter)

    p_na = neg_sub.add_parser("accept", help="Accept the current text, signing convergence on your side")
    p_na.add_argument("--base", default=str(Path(__file__).resolve().parent))
    p_na.add_argument("--state", required=True)
    p_na.add_argument("--out")
    p_na.add_argument("--as", dest="as_party", choices=["a", "b"])
    p_na.set_defaults(func=cmd_negotiate_accept)

    p_ns = neg_sub.add_parser("status", help="Show negotiation rounds, per-clause status, signatures")
    p_ns.add_argument("--state", required=True)
    p_ns.set_defaults(func=cmd_negotiate_status)

    p_sim = neg_sub.add_parser("simulate", help="Run both sides on one machine with configurable stances; report converged/blocked/max-rounds outcome (game-theoretic validation)")
    p_sim.add_argument("--party-a-base", required=True, help="Workspace for Party A (must have config/org-policy.json)")
    p_sim.add_argument("--party-b-base", required=True, help="Workspace for Party B (must have config/org-policy.json)")
    p_sim.add_argument("--stance-a", choices=sorted(NEGOTIATE_STANCE_DESCRIPTORS.keys()), help="Override Party A's stance for this simulation")
    p_sim.add_argument("--stance-b", choices=sorted(NEGOTIATE_STANCE_DESCRIPTORS.keys()), help="Override Party B's stance for this simulation")
    p_sim.add_argument("--mode", choices=["auto", "agent"], default="auto", help="Counter mode: auto (deterministic) or agent (LLM)")
    p_sim.add_argument("--max-rounds", type=int, default=20)
    p_sim.add_argument("--state", help="Where to write the simulation state file (default: <party_a_base>/_simulate_state.json)")
    p_sim.add_argument("--out", help="Where to write the simulation report JSON")
    p_sim.set_defaults(func=cmd_negotiate_simulate)

    p_ndiff = neg_sub.add_parser("diff", help="Show clause-by-clause changes between two rounds (defaults to last two)")
    p_ndiff.add_argument("--base", default=str(Path(__file__).resolve().parent))
    p_ndiff.add_argument("--state", required=True)
    p_ndiff.add_argument("--from-round", dest="from_round", type=int, help="Round number to diff from (default: round before --to-round)")
    p_ndiff.add_argument("--to-round", dest="to_round", type=int, help="Round number to diff to (default: most recent)")
    p_ndiff.add_argument("--out-md", help="Optional markdown output with redline-style code blocks")
    p_ndiff.set_defaults(func=cmd_negotiate_diff)

    p_nw = neg_sub.add_parser("withdraw", help="Withdraw from a negotiation; flips status to `withdrawn` and blocks further commands")
    p_nw.add_argument("--base", default=str(Path(__file__).resolve().parent))
    p_nw.add_argument("--state", required=True)
    p_nw.add_argument("--as", dest="as_party", choices=["a", "b"])
    p_nw.add_argument("--reason", help="Free-text reason for the withdrawal (recorded in state.withdrawal.reason)")
    p_nw.set_defaults(func=cmd_negotiate_withdraw)

    p_na = neg_sub.add_parser("analyze", help="Read-only post-hoc dashboard for any state file (trajectory, winners, source breakdown, fatigue summary, outcome interpretation)")
    p_na.add_argument("--base", default=str(Path(__file__).resolve().parent))
    p_na.add_argument("--state", required=True)
    p_na.add_argument("--out-md", help="Optional markdown output for sharing with humans (the JSON dashboard is hard to read in a meeting)")
    p_na.set_defaults(func=cmd_negotiate_analyze)

    p_nv = neg_sub.add_parser("validate", help="Standalone integrity check: schema version + hash-chain verification + per-round structural shape (exits 2 on any failure)")
    p_nv.add_argument("--state", required=True)
    p_nv.set_defaults(func=cmd_negotiate_validate)

    p_nso = neg_sub.add_parser("sign-off", help="Review key points (changed clauses, applied amendments, red flags) and approve before finalize")
    p_nso.add_argument("--base", default=str(Path(__file__).resolve().parent))
    p_nso.add_argument("--state", required=True)
    p_nso.add_argument("--as", dest="as_party", choices=["a", "b"])
    p_nso.add_argument("--yes", action="store_true", help="Skip the interactive batch confirmation prompt")
    p_nso.set_defaults(func=cmd_negotiate_signoff)

    p_nf = neg_sub.add_parser("finalize", help="Emit final .md + .docx; optionally hand off to docx2pdf + sign-CLI hooks. Requires both parties' sign-off.")
    p_nf.add_argument("--base", default=str(Path(__file__).resolve().parent))
    p_nf.add_argument("--state", required=True)
    p_nf.add_argument("--out-md", required=True)
    p_nf.add_argument("--out-docx", required=True)
    p_nf.add_argument("--to-pdf", action="store_true", help="Run config/integrations.json `docx2pdf_cmd` to convert docx → pdf")
    p_nf.add_argument("--sign", action="store_true", help="Run config/integrations.json `sign_cli_cmd` to sign the pdf (implies --to-pdf)")
    p_nf.add_argument("--skip-signoff", action="store_true", help="Bypass the both-parties-signed-off requirement (testing/non-binding only)")
    p_nf.set_defaults(func=cmd_negotiate_finalize)

    p_tutorial = sub.add_parser("tutorial", help="Interactive primer that explains policy/profile/playbook and runs a sample review")
    p_tutorial.add_argument("--base", help="Sandbox directory for the sample run (defaults to a fresh temp dir)")
    p_tutorial.add_argument("--no-prompt", action="store_true", help="Skip pauses; useful for CI smoke tests")
    p_tutorial.add_argument("--run-sample", action="store_true", help="Skip the run-sample question and run it automatically")
    p_tutorial.set_defaults(func=cmd_tutorial)

    p_wizard = sub.add_parser("wizard", help="Guided setup -> ingest -> build -> review flow")
    p_wizard.add_argument("--base", default=str(Path(__file__).resolve().parent))
    p_wizard.add_argument("--playbook")
    p_wizard.add_argument("--org-name")
    p_wizard.add_argument("--template", choices=sorted(SUPPORTED_TEMPLATES.keys()))
    p_wizard.add_argument("--risk-posture", default="balanced", choices=["strict", "balanced", "commercial"])
    p_wizard.add_argument("--preferred-jurisdictions", default="Austria")
    p_wizard.add_argument("--survival-years", type=int, default=5)
    p_wizard.add_argument("--ai-policy", default="guardrailed", choices=["restricted", "guardrailed", "permissive"])
    p_wizard.add_argument("--retention-carveout", default="Allow limited backup/legal retention under continuing confidentiality obligations.")
    p_wizard.add_argument("--default-policy", default="config/default-policy.json")
    p_wizard.add_argument("--policy", default="config/org-policy.json")
    p_wizard.add_argument("--quick", action="store_true")
    p_wizard.add_argument("--build", action="store_true")
    p_wizard.add_argument("--no-build", action="store_true")
    p_wizard.add_argument("--yes", action="store_true")
    p_wizard.add_argument("--no-prompt", action="store_true")
    p_wizard.add_argument("--ingest-files", nargs="+")
    p_wizard.add_argument("--contracts-dir")
    p_wizard.add_argument("--drive-export-dir")
    p_wizard.add_argument("--review-file")
    p_wizard.add_argument("--review-text")
    p_wizard.add_argument("--counterparty")
    p_wizard.add_argument("--out-json")
    p_wizard.add_argument("--out-md")
    p_wizard.add_argument("--why", action="store_true")
    p_wizard.add_argument("--learn-profile", action="store_true")
    p_wizard.add_argument("--scoring-profile", help="Scoring profile name")
    p_wizard.add_argument("--scoring-profiles", help="Path to scoring profiles JSON")
    p_wizard.set_defaults(func=cmd_wizard)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
