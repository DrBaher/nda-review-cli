"""Lookup-order tests for load_llm_config.

Spec (matches template-vault-cli/docs/INTEROP.md):
  1. ~/.config/contract-ops/llm.json    (preferred, suite-wide)
  2. ~/.config/nda-review-cli/llm.json  (per-CLI XDG)
  3. {base}/config/llm.json             (repo-local fallback)

First found wins.
"""
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import nda_review_cli as cli  # noqa: E402


def _empty_args() -> types.SimpleNamespace:
    return types.SimpleNamespace(llm=None, llm_model=None, llm_base_url=None)


def _write_cfg(path: Path, marker: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "provider": "anthropic",
        "model": marker,
        "api_key": "sk-test",
    }))


class LLMConfigLookupOrderTests(unittest.TestCase):
    def test_contract_ops_wins_over_per_cli_and_repo_local(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            fake_home = tmp_path / "home"
            repo_base = tmp_path / "repo"
            (fake_home / ".config").mkdir(parents=True)
            (repo_base / "config").mkdir(parents=True)

            _write_cfg(fake_home / ".config" / "contract-ops" / "llm.json", "model-from-contract-ops")
            _write_cfg(fake_home / ".config" / "nda-review-cli" / "llm.json", "model-from-per-cli")
            _write_cfg(repo_base / "config" / "llm.json", "model-from-repo-local")

            original_home = Path.home
            Path.home = staticmethod(lambda: fake_home)
            try:
                cfg = cli.load_llm_config(repo_base, _empty_args())
            finally:
                Path.home = original_home

            self.assertEqual(cfg["model"], "model-from-contract-ops")


if __name__ == "__main__":
    unittest.main()
