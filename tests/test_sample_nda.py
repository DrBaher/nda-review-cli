import json
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
CLI = REPO / "nda_review_cli.py"


class SampleNDATests(unittest.TestCase):
    def test_writes_bundled_fixture_to_chosen_path(self):
        td = Path(tempfile.mkdtemp(prefix="nda-sample-"))
        out = td / "first.txt"
        result = subprocess.run(
            ["python3", str(CLI), "sample-nda", "--out", str(out)],
            cwd=REPO,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        self.assertEqual(result.returncode, 0)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["out"], str(out))
        self.assertGreater(payload["bytes"], 0)
        self.assertGreater(payload["lines"], 0)
        # File matches the bundled fixture content exactly
        bundled = (REPO / "tests" / "fixtures" / "sample_nda.txt").read_text()
        self.assertEqual(out.read_text(), bundled)

    def test_creates_parent_directories(self):
        td = Path(tempfile.mkdtemp(prefix="nda-sample-deep-"))
        out = td / "deep" / "nested" / "first.txt"
        subprocess.check_call(
            ["python3", str(CLI), "sample-nda", "--out", str(out)],
            cwd=REPO,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.assertTrue(out.exists())
        self.assertGreater(out.stat().st_size, 0)


class FirstRunHintTests(unittest.TestCase):
    def test_script_invocation_shows_dot_slash_form(self):
        # Bare invocation prints the hint to stderr; argv[0] ends in .py.
        result = subprocess.run(
            ["python3", str(CLI)],
            cwd=REPO,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        hint = result.stderr.decode()
        self.assertIn("./nda_review_cli.py tutorial", hint)
        self.assertNotIn("nda-review-cli tutorial", hint)  # no console-script form

    def test_help_text_contains_sample_nda_subcommand(self):
        # Verify the new subcommand is registered and discoverable.
        result = subprocess.run(
            ["python3", str(CLI), "--help"],
            cwd=REPO,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("sample-nda", result.stdout.decode())


if __name__ == "__main__":
    unittest.main()
