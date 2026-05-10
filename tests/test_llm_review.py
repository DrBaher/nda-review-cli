"""Tests for opt-in LLM augmentation in cmd_review.

The transport (urllib.request.urlopen) is monkey-patched with a fake response
so no real HTTP is performed and no API key is needed.
"""
import io
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import nda_review_cli as cli  # noqa: E402


class _FakeHTTPResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def _fake_anthropic_response(text: str):
    payload = {
        "model": "claude-sonnet-4-6",
        "content": [{"type": "text", "text": text}],
        "usage": {"input_tokens": 123, "output_tokens": 45},
    }
    return _FakeHTTPResponse(json.dumps(payload).encode("utf-8"))


def _fake_openai_response(text: str):
    payload = {
        "model": "qwen2.5:14b",
        "choices": [{"message": {"role": "assistant", "content": text}}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 60},
    }
    return _FakeHTTPResponse(json.dumps(payload).encode("utf-8"))


_LLM_RESPONSE_JSON = json.dumps({
    "votes": [
        {"finding_index": 0, "vote": "agree", "rationale": "Standard concern, agree with rule engine."},
        {"finding_index": 1, "vote": "soften", "rationale": "Common in this jurisdiction; not a deal-breaker."}
    ],
    "additional_findings": [
        {"clause": "data_protection", "severity": "high", "concern": "No GDPR-compliant data processing addendum.", "evidence": "Section 8 references personal data without DPA."}
    ],
    "clause_suggestions": [
        {"clause": "term_and_survival", "suggested_text": "Term: 2 years from Effective Date. Survival: 5 years post-termination, indefinitely for trade secrets.", "reason": "Aligns with house policy."}
    ],
})


class LLMConfigTests(unittest.TestCase):
    def test_load_llm_config_file_then_env_then_cli(self):
        td = Path(tempfile.mkdtemp(prefix="llm-cfg-"))
        (td / "config").mkdir()
        (td / "config" / "llm.json").write_text(json.dumps({
            "provider": "anthropic", "model": "claude-from-file", "api_key": "key-from-file"
        }))
        # Env var overrides model
        with mock.patch.dict(os.environ, {"NDA_LLM_MODEL": "claude-from-env"}, clear=False):
            class Args:
                llm = None
                llm_model = None
                llm_base_url = None
            cfg = cli.load_llm_config(td, Args())
            self.assertEqual(cfg["provider"], "anthropic")
            self.assertEqual(cfg["model"], "claude-from-env")
            # Preset fills in base_url
            self.assertEqual(cfg["base_url"], "https://api.anthropic.com/v1")
            self.assertEqual(cfg["api_key"], "key-from-file")

        # CLI provider switches to ollama and applies the preset
        class Args2:
            llm = "ollama"
            llm_model = None
            llm_base_url = None
        cfg2 = cli.load_llm_config(td, Args2())
        self.assertEqual(cfg2["provider"], "ollama")
        self.assertEqual(cfg2["base_url"], "http://localhost:11434/v1")
        self.assertIn("qwen", cfg2["model"])

    def test_unknown_provider_raises(self):
        with self.assertRaises(SystemExit):
            cli.llm_call({"provider": "made-up", "model": "x", "base_url": "http://x"}, "s", "u")


class LLMResponseParserTests(unittest.TestCase):
    def test_parses_clean_json(self):
        out = cli._parse_llm_review_response(_LLM_RESPONSE_JSON)
        self.assertEqual(len(out["votes"]), 2)
        self.assertEqual(len(out["additional_findings"]), 1)
        self.assertEqual(len(out["clause_suggestions"]), 1)

    def test_parses_json_inside_code_fence(self):
        wrapped = "Here you go:\n```json\n" + _LLM_RESPONSE_JSON + "\n```\n"
        out = cli._parse_llm_review_response(wrapped)
        self.assertEqual(len(out["votes"]), 2)

    def test_garbled_response_returns_parse_error(self):
        out = cli._parse_llm_review_response("not json at all")
        self.assertIn("_parse_error", out)


class CmdReviewWithLLMTests(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp(prefix="llm-review-"))
        (self.td / "config").mkdir()
        (self.td / "config" / "llm.json").write_text(json.dumps({
            "provider": "anthropic",
            "model": "claude-sonnet-4-6",
            "api_key": "test-key",
        }))
        # Build a tiny playbook
        playbook = {
            "org_name": "Test Org",
            "policy": [
                {"clause": "term_and_survival", "preferred": "Finite term.", "red_flags": ["perpetual"]},
                {"clause": "use_restrictions", "preferred": "Narrow purpose.", "red_flags": ["any purpose"]},
            ],
            "scoring_profile": {"name": "balanced"},
        }
        self.pb = self.td / "playbook.json"
        self.pb.write_text(json.dumps(playbook))
        self.nda = self.td / "nda.txt"
        self.nda.write_text(
            "NON-DISCLOSURE AGREEMENT\n\n"
            "Term: this agreement is perpetual and survives indefinitely.\n"
            "Use Restrictions: receiver may use confidential information for any purpose.\n"
        )

    def _make_args(self, **overrides):
        class A:
            base = str(self.td)
            playbook = str(self.pb)
            counterparty = None
            file = str(self.nda)
            text = None
            out_json = str(self.td / "review.json")
            out_md = str(self.td / "review.md")
            why = True
            learn_profile = False
            scoring_profile = None
            scoring_profiles = None
            llm = None
            llm_model = None
            llm_base_url = None
            yes_llm_send = False
        a = A()
        a.base = str(self.td)
        a.playbook = str(self.pb)
        a.file = str(self.nda)
        a.out_json = str(self.td / "review.json")
        a.out_md = str(self.td / "review.md")
        for k, v in overrides.items():
            setattr(a, k, v)
        return a

    def test_review_without_llm_has_no_llm_block(self):
        cli.cmd_review(self._make_args())
        data = json.loads((self.td / "review.json").read_text())
        self.assertNotIn("llm_annotations", data)
        self.assertNotIn("llm_used", data)

    def test_review_with_llm_anthropic_attaches_annotations(self):
        with mock.patch("nda_review_cli.urllib.request.urlopen", return_value=_fake_anthropic_response(_LLM_RESPONSE_JSON)) as m:
            cli.cmd_review(self._make_args(llm="anthropic", yes_llm_send=True))
            self.assertEqual(m.call_count, 1)
            req = m.call_args[0][0]
            self.assertIn("api.anthropic.com", req.full_url)
            self.assertEqual(req.headers.get("X-api-key"), "test-key")

        data = json.loads((self.td / "review.json").read_text())
        self.assertTrue(data["llm_used"])
        ann = data["llm_annotations"]
        self.assertEqual(ann["provider"], "anthropic")
        self.assertGreaterEqual(len(ann["votes"]), 1)
        self.assertEqual(ann["additional_findings"][0]["clause"], "data_protection")

        md = (self.td / "review.md").read_text()
        self.assertIn("LLM Annotations", md)
        self.assertIn("(LLM)", md)
        self.assertIn("data_protection", md)

    def test_review_with_llm_ollama_uses_local_base_url(self):
        # Wipe the file config to ensure preset fills base_url and api_key
        (self.td / "config" / "llm.json").unlink()
        with mock.patch("nda_review_cli.urllib.request.urlopen", return_value=_fake_openai_response(_LLM_RESPONSE_JSON)) as m:
            cli.cmd_review(self._make_args(llm="ollama", llm_model="qwen2.5:14b", yes_llm_send=True))
            req = m.call_args[0][0]
            self.assertIn("localhost:11434", req.full_url)
            self.assertIn("/chat/completions", req.full_url)

    def test_llm_without_provider_raises_friendly_error(self):
        # No file config, no env, no CLI provider
        (self.td / "config" / "llm.json").unlink()
        with self.assertRaises(SystemExit) as ctx:
            cli.cmd_review(self._make_args(llm="auto", yes_llm_send=True))
        self.assertIn("provider", str(ctx.exception))

    def test_non_interactive_send_requires_explicit_consent(self):
        # Default args have yes_llm_send=False, and the test runner is non-interactive
        with mock.patch("nda_review_cli.urllib.request.urlopen", return_value=_fake_anthropic_response(_LLM_RESPONSE_JSON)):
            with self.assertRaises(SystemExit):
                cli.cmd_review(self._make_args(llm="anthropic", yes_llm_send=False))


if __name__ == "__main__":
    unittest.main()
