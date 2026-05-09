"""Tests for the polish round of negotiation features:
- non_negotiable_clauses (hard-floor opt-out from fatigue + force counter)
- counter --dry-run (preview without committing state)
- negotiate diff (clause-by-clause change view between rounds)
- negotiate withdraw (graceful abort)
- negotiate analyze (post-hoc dashboard)
- LLM agent prompt parity (priorities + fatigue + non-negotiable injected)
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


REPO = Path(__file__).resolve().parents[1]
CLI = REPO / "nda_review_cli.py"
sys.path.insert(0, str(REPO))


def quickstart(td: Path, org_name: str) -> None:
    subprocess.check_call(
        ["python3", str(CLI), "quickstart", "--base", str(td), "--no-prompt", "--yes"],
        cwd=REPO,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    p = td / "config" / "org-policy.json"
    o = json.loads(p.read_text())
    o["org_name"] = org_name
    p.write_text(json.dumps(o))


def make_party_b_divergent(td: Path) -> None:
    p = td / "config" / "org-policy.json"
    o = json.loads(p.read_text())
    o["clause_rules"]["term_and_survival"]["preferred"] = (
        "NDA term 5 years, survival 10 years."
    )
    o["clause_rules"]["return_or_destroy"]["preferred"] = (
        "Destroy and certify destruction within 7 days."
    )
    o["clause_rules"]["residuals"]["preferred"] = "Accept broad residual knowledge."
    o["clause_rules"]["mutuality"]["preferred"] = "Unilateral receiving-party-bound NDA only."
    p.write_text(json.dumps(o))


def init_negotiation(party_a: Path, state_path: Path):
    subprocess.check_call(
        [
            "python3", str(CLI), "negotiate", "init",
            "--base", str(party_a),
            "--template", "mutual",
            "--party-a-name", "Acme", "--party-a-address", "1 Main",
            "--party-b-name", "Beta", "--party-b-address", "2 Side",
            "--purpose", "x", "--effective-date", "2026-05-09",
            "--out", str(state_path),
        ],
        cwd=REPO,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def run(args, expect_code: int = 0, capture: bool = True):
    result = subprocess.run(
        ["python3", str(CLI), *args],
        cwd=REPO,
        capture_output=capture,
        text=True,
    )
    if expect_code is not None and result.returncode != expect_code:
        raise AssertionError(f"args={args} rc={result.returncode} stderr={result.stderr}")
    return result


class NonNegotiableTests(unittest.TestCase):
    def setUp(self):
        self.workdir = Path(tempfile.mkdtemp(prefix="nda-nonneg-"))
        self.party_a = self.workdir / "a"
        self.party_b = self.workdir / "b"
        quickstart(self.party_a, "Acme")
        quickstart(self.party_b, "Beta")
        make_party_b_divergent(self.party_b)
        # B marks term_and_survival as non-negotiable — should never get fatigued away
        p = self.party_b / "config" / "org-policy.json"
        o = json.loads(p.read_text())
        o["non_negotiable_clauses"] = ["term_and_survival"]
        p.write_text(json.dumps(o))
        self.state = self.workdir / "state.json"

    def test_non_negotiable_clause_never_fatigue_conceded(self):
        init_negotiation(self.party_a, self.state)
        # Run several conservative-x-conservative rounds. The party that
        # marked a clause non-negotiable (B) must NEVER fatigue-concede that
        # clause itself. The OTHER party (A) may still fatigue-concede it
        # (i.e. accept B's preferred), and that's fine from B's perspective.
        run(["negotiate", "simulate",
             "--party-a-base", str(self.party_a),
             "--party-b-base", str(self.party_b),
             "--stance-a", "conservative", "--stance-b", "conservative",
             "--mode", "auto", "--max-rounds", "20",
             "--state", str(self.state)])
        state = json.loads(self.state.read_text())
        b_fatigue_concessions = [
            c for r in state["rounds"]
            if r.get("proposer") == "b"
            for c in (r.get("fatigue_concessions") or [])
        ]
        self.assertNotIn(
            "term_and_survival", b_fatigue_concessions,
            f"non-negotiable clause must never be fatigue-conceded by the marking party; got {b_fatigue_concessions}",
        )

    def test_non_negotiable_clause_always_countered_when_diverged(self):
        # When divergent text is in a non-negotiable clause, B's auto agent
        # MUST counter it on its first turn, regardless of stance/priority.
        # Even with compromising stance, non-negotiable wins.
        p = self.party_b / "config" / "org-policy.json"
        o = json.loads(p.read_text())
        o.setdefault("defaults", {})["negotiation_stance"] = "compromising"
        p.write_text(json.dumps(o))
        init_negotiation(self.party_a, self.state)
        run(["negotiate", "counter",
             "--base", str(self.party_b),
             "--state", str(self.state),
             "--auto"])
        state = json.loads(self.state.read_text())
        round2 = state["rounds"][1]
        countered_clauses = {a["clause"] for a in round2["amendments"]}
        self.assertIn(
            "term_and_survival", countered_clauses,
            "non-negotiable clause must be countered even under compromising stance",
        )


class DryRunTests(unittest.TestCase):
    def setUp(self):
        self.workdir = Path(tempfile.mkdtemp(prefix="nda-dry-"))
        self.party_a = self.workdir / "a"
        self.party_b = self.workdir / "b"
        quickstart(self.party_a, "Acme")
        quickstart(self.party_b, "Beta")
        make_party_b_divergent(self.party_b)
        self.state = self.workdir / "state.json"
        init_negotiation(self.party_a, self.state)

    def test_dry_run_does_not_modify_state(self):
        before = self.state.read_text()
        result = run([
            "negotiate", "counter",
            "--base", str(self.party_b),
            "--state", str(self.state),
            "--auto", "--dry-run",
        ])
        after = self.state.read_text()
        self.assertEqual(before, after, "Dry run must not mutate the state file")
        preview = json.loads(result.stdout)
        self.assertTrue(preview["dry_run"])
        self.assertTrue(preview["state_unchanged"])
        self.assertEqual(preview["would_be_round"], 2)
        self.assertEqual(preview["proposer"], "b")

    def test_dry_run_still_applies_fatigue_logic(self):
        # Run two rounds first to accumulate bounce count, then dry-run another.
        run(["negotiate", "counter", "--base", str(self.party_b), "--state", str(self.state), "--auto"])
        run(["negotiate", "counter", "--base", str(self.party_a), "--state", str(self.state), "--auto"])
        result = run([
            "negotiate", "counter",
            "--base", str(self.party_b),
            "--state", str(self.state),
            "--auto", "--dry-run",
        ])
        preview = json.loads(result.stdout)
        # Fatigue may or may not fire yet depending on bounce count, but the
        # field must be present and the dry-run flag must be set.
        self.assertIn("fatigue_concessions", preview)
        self.assertEqual(preview["proposer"], "b")


class DiffTests(unittest.TestCase):
    def setUp(self):
        self.workdir = Path(tempfile.mkdtemp(prefix="nda-diff-"))
        self.party_a = self.workdir / "a"
        self.party_b = self.workdir / "b"
        quickstart(self.party_a, "Acme")
        quickstart(self.party_b, "Beta")
        make_party_b_divergent(self.party_b)
        # Force B to conservative so it actually counters every diverged clause
        p = self.party_b / "config" / "org-policy.json"
        o = json.loads(p.read_text())
        o.setdefault("defaults", {})["negotiation_stance"] = "conservative"
        p.write_text(json.dumps(o))
        self.state = self.workdir / "state.json"
        init_negotiation(self.party_a, self.state)
        run(["negotiate", "counter", "--base", str(self.party_b), "--state", str(self.state), "--auto"])

    def test_diff_default_last_two_rounds(self):
        result = run([
            "negotiate", "diff",
            "--base", str(self.party_a),
            "--state", str(self.state),
        ])
        payload = json.loads(result.stdout)
        self.assertEqual(payload["from_round"], 1)
        self.assertEqual(payload["to_round"], 2)
        self.assertEqual(payload["to_round_proposer"], "b")
        # B's auto counters at least one diverged clause
        self.assertGreaterEqual(len(payload["changes"]), 1)
        clauses_changed = {c["clause"] for c in payload["changes"]}
        # At least one of the diverged clauses should be in the diff
        self.assertTrue(
            clauses_changed & {"term_and_survival", "return_or_destroy", "residuals", "mutuality"},
            f"expected diverged clauses in diff, got {clauses_changed}",
        )

    def test_diff_writes_markdown_when_requested(self):
        out_md = self.workdir / "diff.md"
        run([
            "negotiate", "diff",
            "--base", str(self.party_a),
            "--state", str(self.state),
            "--out-md", str(out_md),
        ])
        self.assertTrue(out_md.exists())
        md = out_md.read_text()
        self.assertIn("Negotiation diff", md)
        self.assertIn("```diff", md)


class WithdrawTests(unittest.TestCase):
    def setUp(self):
        self.workdir = Path(tempfile.mkdtemp(prefix="nda-wd-"))
        self.party_a = self.workdir / "a"
        self.party_b = self.workdir / "b"
        quickstart(self.party_a, "Acme")
        quickstart(self.party_b, "Beta")
        self.state = self.workdir / "state.json"
        init_negotiation(self.party_a, self.state)

    def test_withdraw_flips_status_and_blocks_further_counter(self):
        run([
            "negotiate", "withdraw",
            "--base", str(self.party_b),
            "--state", str(self.state),
            "--as", "b",
            "--reason", "deal terms unacceptable",
        ])
        state = json.loads(self.state.read_text())
        self.assertEqual(state["status"], "withdrawn")
        self.assertEqual(state["withdrawal"]["withdrawn_by"], "b")
        self.assertIn("unacceptable", state["withdrawal"]["reason"])

        # Subsequent counter attempts are rejected
        result = run([
            "negotiate", "counter",
            "--base", str(self.party_b),
            "--state", str(self.state),
            "--auto",
        ], expect_code=1)
        self.assertIn("withdrawn", result.stderr.lower())


class AnalyzeTests(unittest.TestCase):
    def setUp(self):
        self.workdir = Path(tempfile.mkdtemp(prefix="nda-an-"))
        self.party_a = self.workdir / "a"
        self.party_b = self.workdir / "b"
        quickstart(self.party_a, "Acme")
        quickstart(self.party_b, "Beta")
        make_party_b_divergent(self.party_b)
        self.state = self.workdir / "state.json"
        # Build a small converged negotiation
        init_negotiation(self.party_a, self.state)
        run(["negotiate", "counter", "--base", str(self.party_b), "--state", str(self.state), "--auto"])
        run(["negotiate", "accept", "--base", str(self.party_a), "--state", str(self.state), "--as", "a"])

    def test_analyze_returns_full_dashboard(self):
        result = run(["negotiate", "analyze", "--base", str(self.party_a), "--state", str(self.state)])
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "converged")
        self.assertIn("trajectory", payload)
        self.assertGreaterEqual(len(payload["trajectory"]), 2)
        self.assertIn("source_breakdown", payload)
        self.assertIn("winner_per_clause", payload)
        self.assertIn("wins_by_party", payload)
        self.assertIn("outcome_interpretation", payload)
        self.assertEqual(payload["outcome_interpretation"]["label"], "converged organically")

    def test_analyze_handles_blocked_negotiation(self):
        # Start a fresh blocked negotiation
        wd = Path(tempfile.mkdtemp(prefix="nda-an-blocked-"))
        a = wd / "a"; b = wd / "b"
        quickstart(a, "Acme"); quickstart(b, "Beta")
        make_party_b_divergent(b)
        for base in (a, b):
            p = base / "config" / "org-policy.json"
            o = json.loads(p.read_text())
            o.setdefault("defaults", {})["max_clause_bounces"] = 0  # disable fatigue
            p.write_text(json.dumps(o))
        state = wd / "state.json"
        init_negotiation(a, state)
        run(["negotiate", "simulate",
             "--party-a-base", str(a), "--party-b-base", str(b),
             "--stance-a", "conservative", "--stance-b", "conservative",
             "--mode", "auto", "--max-rounds", "12", "--state", str(state)])
        result = run(["negotiate", "analyze", "--base", str(a), "--state", str(state)])
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "blocked")
        self.assertIsNotNone(payload["block_diagnosis"])
        self.assertEqual(payload["outcome_interpretation"]["label"], "blocked (deadlock detected)")


class LLMPromptParityTests(unittest.TestCase):
    """Verify the LLM agent's user prompt now includes priorities + fatigue + non-negotiable."""

    def test_agent_prompt_includes_priority_and_non_negotiable_markers(self):
        # Mock the urllib transport and capture the request body
        import nda_review_cli as cli

        # Build a tiny synthetic state
        state = {
            "schema_version": "0.1",
            "negotiation_id": "test",
            "parties": {"a": {"name": "Acme", "role": "mutual"}, "b": {"name": "Beta", "role": "mutual"}},
            "rounds": [
                {
                    "round": 1, "proposer": "a",
                    "text": "## 5. Term and Survival\n\nNDA term 2y survival 5y.\n\n",
                    "amendments": [], "accept_clauses": [],
                },
                {
                    "round": 2, "proposer": "b",
                    "text": "## 5. Term and Survival\n\nNDA term 5y survival 10y.\n\n",
                    "amendments": [{"clause": "term_and_survival", "old_text": "x", "new_text": "y", "rationale": "B's preferred"}],
                    "accept_clauses": [],
                },
            ],
        }
        org_policy = {
            "clause_rules": {
                "term_and_survival": {"preferred": "NDA term 2y survival 5y.", "red_flags": []},
                "residuals": {"preferred": "Reject residuals.", "red_flags": []},
            },
            "clause_priorities": ["term_and_survival", "residuals"],
            "non_negotiable_clauses": ["term_and_survival"],
        }
        captured_body = {}

        def fake_urlopen(req, timeout=120):
            import io
            body = json.loads(req.data.decode("utf-8"))
            captured_body.update(body)
            response = json.dumps({
                "model": "test-model",
                "choices": [{"message": {"content": json.dumps({"accept_clauses": [], "counter_amendments": [], "summary": "test"})}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            })
            return _FakeResponse(response.encode("utf-8"))

        cfg = {"provider": "openai", "model": "test-model", "base_url": "https://example.com/v1", "api_key": "test"}
        with mock.patch("nda_review_cli.urllib.request.urlopen", side_effect=fake_urlopen):
            cli._negotiate_agent_propose(state, "a", org_policy, cfg, "conservative")

        user_msg = captured_body["messages"][1]["content"]
        # Verify the prompt mentions the new fields
        self.assertIn("priority order", user_msg.lower())
        self.assertIn("non-negotiable", user_msg.lower())
        self.assertIn("term_and_survival", user_msg)
        self.assertIn("rank", user_msg.lower())
        self.assertIn("NON_NEGOTIABLE", user_msg)
        self.assertIn("bounce_count=1", user_msg)  # term_and_survival bounced once in round 2


class ValidateTests(unittest.TestCase):
    def setUp(self):
        self.workdir = Path(tempfile.mkdtemp(prefix="nda-validate-"))
        self.party_a = self.workdir / "a"
        self.party_b = self.workdir / "b"
        quickstart(self.party_a, "Acme")
        quickstart(self.party_b, "Beta")
        self.state = self.workdir / "state.json"
        init_negotiation(self.party_a, self.state)

    def test_validate_passes_on_clean_state(self):
        result = run([
            "negotiate", "validate",
            "--state", str(self.state),
        ])
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["hash_chain_verified"])
        self.assertEqual(payload["structural_issues"], [])
        self.assertEqual(payload["rounds_total"], 1)

    def test_validate_fails_on_tampered_text(self):
        # Tamper with round 1 text but leave hash unchanged → load() detects it
        state = json.loads(self.state.read_text())
        state["rounds"][0]["text"] = state["rounds"][0]["text"] + "\n\nINSERTED CLAUSE."
        self.state.write_text(json.dumps(state))
        result = run([
            "negotiate", "validate",
            "--state", str(self.state),
        ], expect_code=2)
        payload = json.loads(result.stdout)
        self.assertFalse(payload["ok"])
        self.assertIn("Hash-chain mismatch", payload.get("error", ""))

    def test_validate_fails_on_proposer_alternation_violation(self):
        # Append a fake round with the same proposer as round 1 (a). Since
        # round 1 is the initial draft signed by A, round 2 with proposer=a
        # would violate alternation. We must also chain the hash correctly so
        # we hit the structural check rather than the hash-chain check.
        state = json.loads(self.state.read_text())
        prev_hash = state["rounds"][0]["text_hash"]
        new_text = state["rounds"][0]["text"] + "\n\n## extra\n\nadded\n"
        # Re-derive the hash so the chain itself remains intact.
        import hashlib
        h = hashlib.sha256()
        h.update(prev_hash.encode("utf-8"))
        h.update(b"\x00")
        h.update(new_text.encode("utf-8"))
        state["rounds"].append({
            "round": 2,
            "proposer": "a",  # SAME as round 1 — alternation violated
            "timestamp": "2026-05-09T00:00:00+00:00",
            "text": new_text,
            "text_hash": h.hexdigest(),
            "amendments": [],
            "accept_clauses": [],
            "summary": "",
            "signature": {"signer": "a", "signed_at": "2026-05-09T00:00:00+00:00", "method": "json_flag"},
            "amendment_source": "manual",
        })
        self.state.write_text(json.dumps(state))
        result = run([
            "negotiate", "validate",
            "--state", str(self.state),
        ], expect_code=2)
        payload = json.loads(result.stdout)
        self.assertFalse(payload["ok"])
        issues = " ".join(payload["structural_issues"])
        self.assertIn("alternation", issues)


class _FakeResponse:
    def __init__(self, data: bytes):
        self._data = data

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self._data


if __name__ == "__main__":
    unittest.main()
