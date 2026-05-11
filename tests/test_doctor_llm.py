import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
CLI = REPO / "nda_review_cli.py"


def quickstart(td: Path) -> None:
    subprocess.check_call(
        ["python3", str(CLI), "quickstart", "--base", str(td), "--no-prompt", "--yes"],
        cwd=REPO,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def doctor(td: Path, *extra_args, env_overrides=None) -> dict:
    env = dict(os.environ)
    # Strip any ambient NDA_LLM_* vars unless test sets them.
    for k in list(env):
        if k.startswith("NDA_LLM_"):
            del env[k]
    if env_overrides:
        env.update(env_overrides)
    # Don't check exit code: doctor exits 2 when hard_failures present, but
    # stdout still has the JSON payload we care about.
    res = subprocess.run(
        ["python3", str(CLI), "doctor", "--base", str(td), *extra_args],
        cwd=REPO,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    return json.loads(res.stdout)


def find_llm_check(payload: dict) -> dict:
    return next(c for c in payload["checks"] if c["name"] == "llm_config")


class DoctorLLMTests(unittest.TestCase):
    def test_no_config_no_env_marks_llm_check_as_skip(self):
        td = Path(tempfile.mkdtemp(prefix="nda-doctor-noconfig-"))
        quickstart(td)
        payload = doctor(td)
        llm = find_llm_check(payload)
        self.assertEqual(llm["status"], "skip")
        self.assertIn("LLM is opt-in", llm["note"])

    def test_complete_config_marks_as_ok_without_round_trip(self):
        td = Path(tempfile.mkdtemp(prefix="nda-doctor-okconfig-"))
        quickstart(td)
        (td / "config" / "llm.json").write_text(json.dumps({
            "provider": "ollama",
            "model": "qwen2.5:14b",
            "base_url": "http://127.0.0.1:1/v1",
        }))
        payload = doctor(td)
        llm = find_llm_check(payload)
        self.assertEqual(llm["status"], "ok")
        self.assertEqual(llm["provider"], "ollama")
        self.assertEqual(llm["model"], "qwen2.5:14b")
        self.assertNotIn("round_trip", llm)

    def test_incomplete_config_marks_as_warn(self):
        td = Path(tempfile.mkdtemp(prefix="nda-doctor-warn-"))
        quickstart(td)
        # provider="openai-compatible" has no default model — surfaces the
        # missing-model problem cleanly. (anthropic/openai/ollama presets all
        # fill model from preset, so they wouldn't trip the model warning.)
        (td / "config" / "llm.json").write_text(json.dumps({"provider": "openai-compatible"}))
        payload = doctor(td)
        llm = find_llm_check(payload)
        self.assertEqual(llm["status"], "warn")
        self.assertTrue(any("model not set" in p for p in llm["problems"]),
                        f"expected 'model not set' in {llm['problems']}")

    def test_anthropic_without_api_key_marks_as_warn(self):
        td = Path(tempfile.mkdtemp(prefix="nda-doctor-warn-anth-"))
        quickstart(td)
        # anthropic preset fills the model, but api_key has no preset default.
        (td / "config" / "llm.json").write_text(json.dumps({"provider": "anthropic"}))
        payload = doctor(td)
        llm = find_llm_check(payload)
        self.assertEqual(llm["status"], "warn")
        self.assertTrue(any("api_key" in p for p in llm["problems"]),
                        f"expected api_key warning in {llm['problems']}")

    def test_check_llm_against_unreachable_url_marks_as_fail(self):
        td = Path(tempfile.mkdtemp(prefix="nda-doctor-fail-"))
        quickstart(td)
        # Port 1 is privileged; localhost:1 should reliably refuse connection.
        (td / "config" / "llm.json").write_text(json.dumps({
            "provider": "ollama",
            "model": "qwen2.5:14b",
            "base_url": "http://127.0.0.1:1/v1",
        }))
        payload = doctor(td, "--check-llm")
        llm = find_llm_check(payload)
        self.assertEqual(llm["status"], "fail")
        self.assertIn("round_trip", llm)
        self.assertFalse(llm["round_trip"]["ok"])
        self.assertTrue(llm["round_trip"]["error"])
        self.assertIn("LLM round-trip failed", " ".join(payload["hard_failures"]))

    def test_env_only_config_runs_check(self):
        td = Path(tempfile.mkdtemp(prefix="nda-doctor-env-"))
        quickstart(td)
        # No config file — provider+model via env. Round-trip not requested.
        payload = doctor(td, env_overrides={
            "NDA_LLM_PROVIDER": "ollama",
            "NDA_LLM_MODEL": "qwen2.5:14b",
            "NDA_LLM_BASE_URL": "http://127.0.0.1:1/v1",
        })
        llm = find_llm_check(payload)
        self.assertEqual(llm["status"], "ok")
        self.assertEqual(llm["provider"], "ollama")


if __name__ == "__main__":
    unittest.main()
