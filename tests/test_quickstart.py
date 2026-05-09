import json
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
CLI = REPO / "nda_review_cli.py"


class QuickstartTests(unittest.TestCase):
    def test_quickstart_defaults_write_policy_and_profile(self):
        td = Path(tempfile.mkdtemp(prefix="nda-quickstart-"))
        subprocess.check_call(
            ["python3", str(CLI), "quickstart", "--base", str(td), "--no-prompt", "--yes"],
            cwd=REPO,
        )
        org_policy = json.loads((td / "config" / "org-policy.json").read_text())
        profile = json.loads((td / "profiles" / "default.json").read_text())
        answers = json.loads((td / "config" / "quickstart-answers.json").read_text())

        # Quickstart-only fields land in policy defaults
        for key in (
            "nda_term_years",
            "return_or_destroy_pref",
            "residual_knowledge",
            "trade_secret_indefinite",
            "affiliate_disclosure",
        ):
            self.assertIn(key, org_policy["defaults"], f"missing default: {key}")

        # Profile annotations from the quiz
        self.assertEqual(profile["role"], answers["role"])
        self.assertEqual(profile["nda_direction"], answers["nda_direction"])

        # Defaults stance: residual_knowledge=reject must add residual red flags
        residuals = org_policy["clause_rules"]["residuals"]
        self.assertIn("residual knowledge", residuals["red_flags"])
        self.assertIn("retained in unaided memory", residuals["red_flags"])

    def test_quickstart_answers_replay_changes_clause_rules(self):
        td = Path(tempfile.mkdtemp(prefix="nda-quickstart-replay-"))
        answers = {
            "org_name": "Acme Bio",
            "org_type": "healthcare",
            "role": "in-house",
            "nda_direction": "disclosing",
            "risk_posture": "strict",
            "preferred_jurisdictions": "Delaware,New York",
            "survival_years": 7,
            "ai_policy": "restricted",
            "nda_term_years": 3,
            "return_or_destroy_pref": "either_with_certification",
            "residual_knowledge": "accept",
            "trade_secret_indefinite": False,
            "affiliate_disclosure": "case_by_case",
        }
        ans_path = td / "answers.json"
        ans_path.parent.mkdir(parents=True, exist_ok=True)
        ans_path.write_text(json.dumps(answers))

        subprocess.check_call(
            [
                "python3",
                str(CLI),
                "quickstart",
                "--base",
                str(td),
                "--no-prompt",
                "--yes",
                "--answers-file",
                str(ans_path),
            ],
            cwd=REPO,
        )

        org_policy = json.loads((td / "config" / "org-policy.json").read_text())
        rules = org_policy["clause_rules"]

        # Term + survival reflect the answers
        ts = rules["term_and_survival"]["preferred"]
        self.assertIn("NDA term 3 year(s)", ts)
        self.assertIn("survival of 7 year(s)", ts)
        self.assertIn("No indefinite carve-out", ts)
        self.assertIn("indefinite trade-secret protection", rules["term_and_survival"]["red_flags"])

        # Return-or-destroy text + extra red flag for missing certification
        rnd = rules["return_or_destroy"]["preferred"]
        self.assertIn("certification of destruction", rnd)
        self.assertIn("no destruction certification", rules["return_or_destroy"]["red_flags"])

        # Residual knowledge=accept clears red flags
        self.assertEqual(rules["residuals"]["red_flags"], [])

        # Affiliate disclosure=case_by_case wording
        self.assertIn("case-by-case", rules["assignment_and_affiliates"]["preferred"])

        # Profile picks up template since org_type was "healthcare"
        profile = json.loads((td / "profiles" / "default.json").read_text())
        self.assertEqual(profile["template"], "healthcare")


if __name__ == "__main__":
    unittest.main()
