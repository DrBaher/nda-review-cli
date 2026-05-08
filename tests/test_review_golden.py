import json
import unittest
from pathlib import Path

from nda_review_cli import CLAUSE_RULES, review_text


def make_playbook():
    return {
        "policy": [
            {
                "clause": clause,
                "preferred_position": cfg["preferred"],
                "red_flags": cfg["red_flags"],
                "keywords": cfg["keywords"],
            }
            for clause, cfg in CLAUSE_RULES.items()
        ]
    }


class ReviewGoldenTests(unittest.TestCase):
    def test_golden_review_shape_and_clauses(self):
        base = Path(__file__).parent / "fixtures"
        text = (base / "sample_nda.txt").read_text()
        golden = json.loads((base / "expected_review_golden.json").read_text())

        result = review_text(text, make_playbook())
        self.assertEqual(result["decision"], golden["decision"])
        self.assertGreaterEqual(result["risk_score"], golden["min_risk_score"])

        found = {f["clause"] for f in result["findings"]}
        for c in golden["must_include_clauses"]:
            self.assertIn(c, found)

        for f in result["findings"]:
            self.assertIn("risk_bucket", f)
            self.assertIn("rule_hits", f)


if __name__ == "__main__":
    unittest.main()
