import json
import subprocess
import tempfile
import unittest
from pathlib import Path


class WizardFlowTests(unittest.TestCase):
    def test_wizard_noninteractive_with_connectors_and_learning(self):
        repo = Path(__file__).resolve().parents[1]
        td = Path(tempfile.mkdtemp(prefix="nda-cli-wizard-"))

        (td / "contracts").mkdir(parents=True, exist_ok=True)
        (td / "drive_export" / "My Drive").mkdir(parents=True, exist_ok=True)
        (td / "data" / "raw_strict").mkdir(parents=True, exist_ok=True)

        sample_nda = (repo / "tests" / "fixtures" / "sample_nda.txt").read_text()
        (td / "contracts" / "sample_nda.txt").write_text(sample_nda)
        (td / "drive_export" / "My Drive" / "notes.txt").write_text("Please revise the NDA term and jurisdiction clauses.")

        (td / "data" / "raw_strict" / "gmail_primary.json").write_text(json.dumps([{"id": "1", "subject": "NDA", "body": "Mutual NDA with carve-outs.", "from": "legal@example.com"}]))
        (td / "data" / "raw_strict" / "gmail_secondary.json").write_text(json.dumps([{"id": "2", "subject": "NDA accepted", "body": "Looks good for us.", "from": "ops@example.com"}]))
        (td / "data" / "raw_strict" / "drive_primary.json").write_text(json.dumps([{"id": "d1", "name": "Drive note"}]))
        (td / "data" / "raw_strict" / "drive_secondary.json").write_text(json.dumps([{"id": "d2", "name": "Drive note 2"}]))

        review_json = td / "output" / "reviews" / "wizard-review.json"
        review_md = td / "output" / "reviews" / "wizard-review.md"
        subprocess.check_call(
            [
                "python3",
                str(repo / "nda_review_cli.py"),
                "wizard",
                "--base",
                str(td),
                "--quick",
                "--yes",
                "--no-prompt",
                "--template",
                "saas",
                "--contracts-dir",
                str(td / "contracts"),
                "--drive-export-dir",
                str(td / "drive_export"),
                "--review-file",
                str(repo / "tests" / "fixtures" / "sample_nda.txt"),
                "--out-json",
                str(review_json),
                "--out-md",
                str(review_md),
                "--counterparty",
                "Wizard Counterparty",
                "--why",
                "--learn-profile",
            ],
            cwd=repo,
        )

        self.assertTrue((td / "config" / "org-policy.json").exists())
        self.assertTrue((td / "config" / "scoring-profiles.json").exists())
        self.assertTrue(review_json.exists())
        self.assertTrue(review_md.exists())
        profile = td / "profiles" / "wizard_counterparty.json"
        self.assertTrue(profile.exists())
        review_payload = json.loads(review_json.read_text())
        self.assertTrue(review_payload["explainability_mode"])
        self.assertIn("profile_learning", review_payload)


if __name__ == "__main__":
    unittest.main()
