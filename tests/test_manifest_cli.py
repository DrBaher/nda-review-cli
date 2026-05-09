import json
import subprocess
import tempfile
import unittest
from pathlib import Path


class ManifestCliTests(unittest.TestCase):
    def test_create_manifest(self):
        repo = Path(__file__).resolve().parents[1]
        out = Path(tempfile.gettempdir()) / "nda_manifest_test.json"
        if out.exists():
            out.unlink()

        subprocess.check_call(
            [
                "python3",
                str(repo / "nda_review_cli.py"),
                "create-manifest",
                "--base",
                str(repo),
                "--counterparty",
                "Healthchecks360",
                "--playbook",
                str(repo / "output/nda_playbook.json"),
                "--files",
                "README.md",
                "--out",
                str(out),
            ],
            cwd=repo,
        )

        data = json.loads(out.read_text())
        self.assertEqual(data["counterparty"], "Healthchecks360")
        self.assertEqual(len(data["artifacts"]), 1)
        self.assertTrue(data["artifacts"][0]["sha256"])


if __name__ == "__main__":
    unittest.main()
