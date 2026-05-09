import json
import subprocess
import tempfile
import unittest
from pathlib import Path


class OnboardingIngestTests(unittest.TestCase):
    def test_init_and_ingest_noninteractive(self):
        repo = Path(__file__).resolve().parents[1]
        td = Path(tempfile.mkdtemp(prefix="nda-cli-test-"))

        subprocess.check_call(
            [
                "python3",
                str(repo / "nda_review_cli.py"),
                "init",
                "--base",
                str(td),
                "--org-name",
                "Acme Health",
                "--template",
                "healthcare",
                "--risk-posture",
                "strict",
                "--preferred-jurisdictions",
                "Austria,Germany",
            ],
            cwd=repo,
        )

        self.assertTrue((td / "config" / "org-policy.json").exists())
        self.assertTrue((td / "profiles" / "default.json").exists())

        sample = repo / "tests" / "fixtures" / "sample_nda.txt"
        subprocess.check_call(
            [
                "python3",
                str(repo / "nda_review_cli.py"),
                "ingest",
                "--base",
                str(td),
                "--files",
                str(sample),
            ],
            cwd=repo,
        )

        proposed = list((td / "knowledge" / "proposed").glob("ingest-suggestions-*.json"))
        self.assertTrue(proposed)
        payload = json.loads(proposed[0].read_text())
        self.assertIn("suggestions", payload)
        self.assertGreaterEqual(len(payload["sources"]), 1)
        self.assertIn("extraction_status", payload["sources"][0])


if __name__ == "__main__":
    unittest.main()
