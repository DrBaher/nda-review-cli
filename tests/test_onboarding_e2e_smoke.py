import json
import subprocess
import tempfile
import unittest
from pathlib import Path


class OnboardingE2ESmokeTests(unittest.TestCase):
    def test_quick_setup_autodiscovery_build_and_review(self):
        repo = Path(__file__).resolve().parents[1]
        td = Path(tempfile.mkdtemp(prefix="nda-cli-e2e-"))

        (td / "knowledge" / "contracts").mkdir(parents=True, exist_ok=True)
        (td / "data" / "raw_strict").mkdir(parents=True, exist_ok=True)

        sample_nda = (repo / "tests" / "fixtures" / "sample_nda.txt").read_text()
        (td / "knowledge" / "contracts" / "sample_nda.txt").write_text(sample_nda)

        gmail_primary = [
            {
                "id": "1",
                "subject": "Mutual NDA redline",
                "body": "Please revise the confidentiality agreement. Term should be 3 years and standard carve-outs should apply.",
                "from": "counsel@example.com",
            }
        ]
        gmail_secondary = [
            {
                "id": "2",
                "subject": "NDA accepted",
                "body": "Looks good. Mutual obligations, return or destroy, and reasonable jurisdiction work for us.",
                "from": "ops@example.com",
            }
        ]
        drive_primary = [{"id": "d1", "name": "NDA playbook notes"}]
        drive_secondary = [{"id": "d2", "name": "Executed NDA tracker"}]

        (td / "data" / "raw_strict" / "gmail_primary.json").write_text(json.dumps(gmail_primary))
        (td / "data" / "raw_strict" / "gmail_secondary.json").write_text(json.dumps(gmail_secondary))
        (td / "data" / "raw_strict" / "drive_primary.json").write_text(json.dumps(drive_primary))
        (td / "data" / "raw_strict" / "drive_secondary.json").write_text(json.dumps(drive_secondary))

        subprocess.check_call(
            [
                "python3",
                str(repo / "nda_review_cli.py"),
                "setup",
                "--base",
                str(td),
                "--quick",
                "--yes",
                "--template",
                "saas",
            ],
            cwd=repo,
        )

        subprocess.check_call(
            [
                "python3",
                str(repo / "nda_review_cli.py"),
                "build-playbook",
                "--base",
                str(td),
            ],
            cwd=repo,
        )

        review_json = td / "output" / "reviews" / "smoke-review.json"
        review_md = td / "output" / "reviews" / "smoke-review.md"
        subprocess.check_call(
            [
                "python3",
                str(repo / "nda_review_cli.py"),
                "review",
                "--base",
                str(td),
                "--playbook",
                str(td / "output" / "nda_playbook.json"),
                "--file",
                str(repo / "tests" / "fixtures" / "sample_nda.txt"),
                "--out-json",
                str(review_json),
                "--out-md",
                str(review_md),
            ],
            cwd=repo,
        )

        self.assertTrue((td / "config" / "org-policy.json").exists())
        self.assertTrue((td / "profiles" / "default.json").exists())
        self.assertTrue((td / "output" / "nda_playbook.json").exists())
        self.assertTrue((td / "output" / "nda_playbook.md").exists())
        proposed = list((td / "knowledge" / "proposed").glob("ingest-suggestions-*.json"))
        self.assertTrue(proposed)
        ingest_payload = json.loads(proposed[0].read_text())
        self.assertTrue(ingest_payload["autodiscovered"])
        self.assertFalse(ingest_payload["skipped_for_approval"])
        self.assertGreaterEqual(len(ingest_payload["sources"]), 1)
        self.assertIn("extraction_status", ingest_payload["sources"][0])

        playbook = json.loads((td / "output" / "nda_playbook.json").read_text())
        self.assertEqual(playbook["org_name"], "Your Org")
        self.assertIn("policy", playbook)
        self.assertGreaterEqual(len(playbook["policy"]), 1)

        review_payload = json.loads(review_json.read_text())
        self.assertIn(review_payload["decision"], {"approve", "escalate", "block"})
        self.assertIn("findings", review_payload)
        self.assertTrue(review_md.exists())


if __name__ == "__main__":
    unittest.main()
