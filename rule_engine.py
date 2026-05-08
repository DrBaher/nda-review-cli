#!/usr/bin/env python3
import re
from typing import Dict, List, Tuple

# Deterministic red-flag trigger patterns by clause.
RED_FLAG_PATTERNS: Dict[str, List[str]] = {
    "definition_of_confidential_information": [r"includes,? but not limited to", r"including but not limited to"],
    "exceptions": [r"recipient can prove", r"required by law"],
    "term_and_survival": [r"indefinite", r"survive indefinitely", r"perpetual"],
    "use_restrictions": [r"any purpose", r"without limitation", r"purpose"],
    "return_or_destroy": [r"immediately", r"within\s+7\s+days", r"certif(?:y|icate)"],
    "residuals": [r"unaided memory", r"residual"],
    "non_solicit_non_compete": [r"non-?compete", r"non-?solicit"],
    "governing_law_jurisdiction": [r"exclusive", r"sole jurisdiction", r"courts of"],
    "liability_and_remedies": [r"unlimited", r"indemn", r"injunctive"],
    "assignment_and_affiliates": [r"without consent", r"assign(?:ment)?"],
    "mutuality": [r"discloser", r"recipient"],
}


def clause_hit(text: str, keywords: List[str]) -> Tuple[bool, List[str]]:
    hits = []
    for k in keywords:
        if re.search(k, text, re.I):
            hits.append(k)
    return (len(hits) > 0), hits


def red_flag_hits(clause: str, text: str) -> List[dict]:
    out = []
    for pat in RED_FLAG_PATTERNS.get(clause, []):
        m = re.search(pat, text, re.I)
        if m:
            out.append({"pattern": pat, "match": text[m.start():m.end()]})
    return out
