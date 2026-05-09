import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from nda_review_cli import DEFAULT_SCORING_PROFILES, learn_profile_from_review, load_policy_config, review_text


def make_playbook():
    repo = Path(__file__).resolve().parents[1]
    policy = load_policy_config(repo, "config/default-policy.json")
    return {
        "policy": [
            {
                "clause": clause,
                "preferred_position": cfg["preferred"],
                "red_flags": cfg["red_flags"],
                "keywords": cfg["keywords"],
            }
            for clause, cfg in policy["clause_rules"].items()
        ]
    }


class ReviewExplainabilityTests(unittest.TestCase):
    def test_review_text_includes_evidence_and_confidence(self):
        base = Path(__file__).parent / "fixtures"
        text = (base / "sample_nda.txt").read_text()
        scoring_profile = {
            "name": "strict",
            "weights": DEFAULT_SCORING_PROFILES["strict"]["weights"],
            "decision_thresholds": DEFAULT_SCORING_PROFILES["strict"]["decision_thresholds"],
            "path": str(Path(__file__).resolve().parents[1] / "config" / "scoring-profiles.json"),
        }

        result = review_text(text, make_playbook(), scoring_profile=scoring_profile, explainability=True)

        self.assertTrue(result["explainability_mode"])
        self.assertEqual(result["scoring_profile"]["name"], "strict")
        self.assertTrue(result["findings"])
        first = result["findings"][0]
        self.assertIn("evidence", first)
        self.assertIn("confidence_score", first)
        self.assertGreater(first["confidence_score"], 0)
        self.assertIn("rule_patterns", first["evidence"])

    def test_profile_learning_writes_auditable_updates(self):
        repo = Path(__file__).resolve().parents[1]
        td = Path(tempfile.mkdtemp(prefix="nda-cli-profile-"))
        review_data = review_text(
            "The confidentiality obligations survive indefinitely and the courts of a foreign venue have exclusive jurisdiction.",
            make_playbook(),
            scoring_profile={
                "name": "balanced",
                "weights": DEFAULT_SCORING_PROFILES["balanced"]["weights"],
                "decision_thresholds": DEFAULT_SCORING_PROFILES["balanced"]["decision_thresholds"],
                "path": str(repo / "config" / "scoring-profiles.json"),
            },
            explainability=True,
        )
        review_data["input_file"] = "tests/fixtures/sample_nda.txt"

        payload = learn_profile_from_review(td, "Example Counterparty", review_data, "output/reviews/example.json")

        profile_path = Path(payload["profile_path"])
        self.assertTrue(profile_path.exists())
        saved = json.loads(profile_path.read_text())
        self.assertIn("review_memory", saved)
        self.assertIn("last_review", saved["review_memory"])
        self.assertIn("changed_fields", saved["review_memory"]["last_review"])
        self.assertIn("source_review_file", saved["review_memory"]["last_review"])

    def test_calibrate_scoring_cli_reports_metrics(self):
        repo = Path(__file__).resolve().parents[1]
        td = Path(tempfile.mkdtemp(prefix="nda-cli-calibration-"))
        playbook = td / "playbook.json"
        playbook.write_text(json.dumps(make_playbook()))
        out = td / "calibration.json"

        subprocess.check_call(
            [
                "python3",
                str(repo / "nda_review_cli.py"),
                "calibrate-scoring",
                "--base",
                str(repo),
                "--playbook",
                str(playbook),
                "--validation-set",
                str(repo / "tests" / "fixtures" / "scoring_validation_set.json"),
                "--scoring-profile",
                "balanced",
                "--out-json",
                str(out),
            ],
            cwd=repo,
        )

        data = json.loads(out.read_text())
        self.assertEqual(data["cases"], 3)
        self.assertIn("accuracy", data)
        self.assertIn("confusion_by_decision_bucket", data)

    def test_generate_redlines_v2_contains_clause_blocks(self):
        repo = Path(__file__).resolve().parents[1]
        td = Path(tempfile.mkdtemp(prefix="nda-cli-redline-v2-"))
        review_json = td / "review.json"
        review_json.write_text(json.dumps(review_text(
            "Residual rights apply to unaided memory and confidentiality is perpetual.",
            make_playbook(),
            scoring_profile={
                "name": "balanced",
                "weights": DEFAULT_SCORING_PROFILES["balanced"]["weights"],
                "decision_thresholds": DEFAULT_SCORING_PROFILES["balanced"]["decision_thresholds"],
                "path": str(repo / "config" / "scoring-profiles.json"),
            },
            explainability=True,
        )))
        out = td / "redlines-v2.md"

        subprocess.check_call(
            [
                "python3",
                str(repo / "nda_review_cli.py"),
                "generate-redlines",
                "--mode",
                "v2",
                "--review-json",
                str(review_json),
                "--out",
                str(out),
            ],
            cwd=repo,
        )

        text = out.read_text()
        self.assertIn("Clause-specific Redline Draft v2", text)
        self.assertIn("Suggested replacement text block", text)
        self.assertIn("Severity:", text)


if __name__ == "__main__":
    unittest.main()
