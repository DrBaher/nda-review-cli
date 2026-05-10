"""Game-theoretic validation: simulate stance × stance pairings end-to-end and
assert the predicted convergence behaviour. This is the empirical validation
of the bargaining-theory predictions in the README/ARCHITECTURE.

Each test sets up two parties with genuinely divergent preferred clause text
(so the stalemate is not trivially avoided by identical defaults) and runs
`negotiate simulate` to completion."""
import json
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
CLI = REPO / "nda_review_cli.py"


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
    """Force Party B's preferred clause text to differ from Party A's defaults
    on enough clauses that conservative-on-both produces a true stalemate."""
    p = td / "config" / "org-policy.json"
    o = json.loads(p.read_text())
    rules = o["clause_rules"]
    rules["term_and_survival"]["preferred"] = (
        "NDA term 5 years, confidentiality survival 10 years. "
        "No carve-out for trade secrets."
    )
    rules["return_or_destroy"]["preferred"] = (
        "Receiving party must destroy and certify destruction within 7 days; "
        "no backup retention permitted."
    )
    rules["residuals"]["preferred"] = "Accept broad residual knowledge."
    rules["mutuality"]["preferred"] = "Unilateral receiving-party-bound NDA only."
    p.write_text(json.dumps(o))


def run_simulation(party_a: Path, party_b: Path, stance_a: str, stance_b: str, max_rounds: int = 12) -> dict:
    result = subprocess.run(
        [
            "python3", str(CLI), "negotiate", "simulate",
            "--party-a-base", str(party_a),
            "--party-b-base", str(party_b),
            "--stance-a", stance_a,
            "--stance-b", stance_b,
            "--mode", "auto",
            "--max-rounds", str(max_rounds),
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise AssertionError(f"simulate exited {result.returncode}: {result.stderr}")
    return json.loads(result.stdout)


class StanceMatrixTests(unittest.TestCase):
    """The predicted bargaining-theory outcomes, locked in as a regression."""

    def setUp(self):
        # Per-test fresh workspaces so the few tests that disable fatigue don't
        # leak state into later tests.
        self.workdir = Path(tempfile.mkdtemp(prefix="nda-sim-"))
        self.party_a = self.workdir / "a"
        self.party_b = self.workdir / "b"
        quickstart(self.party_a, "Acme")
        quickstart(self.party_b, "Beta")
        make_party_b_divergent(self.party_b)

    def test_conservative_x_conservative_resolves_via_fatigue(self):
        """Pure 'never give ground' would be a no-deal equilibrium under static stance,
        but fatigue concession force-resolves clauses that bounce too many times.
        Result: converges (or partial-converges within max-rounds) and at least one
        round is tagged with +fatigue source."""
        report = run_simulation(self.party_a, self.party_b, "conservative", "conservative", max_rounds=20)
        # Either fully converges via fatigue, or hits max_rounds with significant
        # progress made through fatigue concessions
        self.assertIn(report["outcome"], ("converged", "max_rounds_exceeded"))
        agreed = sum(1 for v in report["final_clause_status"].values() if v == "agreed")
        self.assertGreater(agreed, 0)
        # At least one round should be tagged as fatigue-driven
        state = json.loads(Path(report["state_file"]).read_text())
        fatigue_rounds = [r for r in state["rounds"] if "+fatigue" in r.get("amendment_source", "")]
        self.assertGreater(len(fatigue_rounds), 0, "Expected at least one fatigue-tagged round")

    def test_compromising_x_compromising_converges_fast(self):
        """Both sides accept anything that doesn't fire a red flag — converges in 2-3 rounds."""
        report = run_simulation(self.party_a, self.party_b, "compromising", "compromising")
        self.assertEqual(report["outcome"], "converged")
        self.assertLessEqual(report["rounds_used"], 3)
        disputed = sum(1 for v in report["final_clause_status"].values() if v == "disputed")
        self.assertEqual(disputed, 0)

    def test_middleground_x_compromising_converges(self):
        """Mixed stance: middleground holds firm on red flags, compromising concedes elsewhere."""
        report = run_simulation(self.party_a, self.party_b, "middleground", "compromising")
        self.assertEqual(report["outcome"], "converged")
        self.assertLessEqual(report["rounds_used"], 4)

    def test_conservative_x_compromising_converges_with_a_winning(self):
        """Pure asymmetry: A holds firm, B caves on everything except red flags. A's text wins."""
        report = run_simulation(self.party_a, self.party_b, "conservative", "compromising")
        self.assertEqual(report["outcome"], "converged")
        # Most clauses end with last_proposer = a (A's preferred won) since B compromises
        winner_a = sum(1 for w in report["winner_per_clause"].values() if w == "a")
        winner_b = sum(1 for w in report["winner_per_clause"].values() if w == "b")
        self.assertGreater(winner_a, winner_b, f"winners A={winner_a}, B={winner_b}")

    def test_blocked_state_includes_stuck_clauses_when_fatigue_disabled(self):
        """With fatigue disabled (max_clause_bounces=0), conservative × conservative
        falls back to the original behavior: blocked, with a diagnosis listing stuck clauses."""
        # Disable fatigue on both parties
        for base in (self.party_a, self.party_b):
            p = base / "config" / "org-policy.json"
            o = json.loads(p.read_text())
            o.setdefault("defaults", {})["max_clause_bounces"] = 0
            p.write_text(json.dumps(o))
        report = run_simulation(self.party_a, self.party_b, "conservative", "conservative")
        self.assertEqual(report["outcome"], "blocked")
        diag = report.get("block_diagnosis")
        self.assertIsNotNone(diag)
        self.assertIn("stuck_clauses", diag)
        self.assertGreaterEqual(len(diag["stuck_clauses"]), 1)
        self.assertGreater(diag["rounds_without_progress"], 0)

    def test_trajectory_recorded_per_round(self):
        """The trajectory shows agreed/disputed counts evolving each round — useful for analysis."""
        report = run_simulation(self.party_a, self.party_b, "compromising", "compromising")
        self.assertGreaterEqual(len(report["trajectory"]), 2)
        for entry in report["trajectory"]:
            self.assertIn("agreed", entry)
            self.assertIn("disputed", entry)
            self.assertIn("proposer", entry)


class StalemateDetectorTests(unittest.TestCase):
    """Direct test of the rounds-without-progress detector via a real flow."""

    def test_blocked_status_blocks_further_counter(self):
        td = Path(tempfile.mkdtemp(prefix="nda-block-"))
        party_a = td / "a"
        party_b = td / "b"
        quickstart(party_a, "Acme")
        quickstart(party_b, "Beta")
        make_party_b_divergent(party_b)
        # Disable fatigue so the stalemate actually blocks rather than self-resolving
        for base in (party_a, party_b):
            p = base / "config" / "org-policy.json"
            o = json.loads(p.read_text())
            o.setdefault("defaults", {})["max_clause_bounces"] = 0
            p.write_text(json.dumps(o))
        report = run_simulation(party_a, party_b, "conservative", "conservative")
        self.assertEqual(report["outcome"], "blocked")
        # After block, attempting another counter without --force-unblock is rejected
        state_path = report["state_file"]
        result = subprocess.run(
            [
                "python3", str(CLI), "negotiate", "counter",
                "--base", str(party_b),
                "--state", state_path,
                "--auto",
            ],
            cwd=REPO,
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("blocked", result.stderr.lower())


class PriorityLogrollingTests(unittest.TestCase):
    """Validate that clause_priorities reduces stalemate via logrolling."""

    @classmethod
    def setUpClass(cls):
        cls.workdir = Path(tempfile.mkdtemp(prefix="nda-prio-"))

    def _setup_pair(self, with_priorities: bool):
        a = self.workdir / ("with-prio-a" if with_priorities else "no-prio-a")
        b = self.workdir / ("with-prio-b" if with_priorities else "no-prio-b")
        quickstart(a, "Acme")
        quickstart(b, "Beta")
        make_party_b_divergent(b)
        # Disable fatigue for these tests so we can isolate the priority-based
        # logrolling effect from the fatigue-based force-concession effect.
        for base in (a, b):
            p = base / "config" / "org-policy.json"
            o = json.loads(p.read_text())
            o.setdefault("defaults", {})["max_clause_bounces"] = 0
            p.write_text(json.dumps(o))
        if with_priorities:
            # Realistic non-overlapping: conflicting clauses fall into A's
            # concession zone for two of them, B's for one of them. The
            # remaining conflict (term_and_survival) is in both insistence
            # zones — that one should still stalemate.
            prio_a = [
                "term_and_survival",  # A's #1 — A insists
                "residuals",
                "use_restrictions",
                "non_solicit_non_compete",
                "governing_law_jurisdiction",
                "liability_and_remedies",
                "definition_of_confidential_information",
                "exceptions",
                "mutuality",            # A's #9 — A concedes (B wins mutuality)
                "return_or_destroy",    # A's #10 — A concedes (B wins return_or_destroy)
                "assignment_and_affiliates",
            ]
            prio_b = [
                "governing_law_jurisdiction",
                "liability_and_remedies",
                "definition_of_confidential_information",
                "exceptions",
                "mutuality",
                "return_or_destroy",
                "assignment_and_affiliates",
                "term_and_survival",     # B's #8 — B still insists (in top 8)
                "non_solicit_non_compete",
                "residuals",             # B's #10 — B concedes (A wins residuals)
                "use_restrictions",
            ]
            for base, prios in ((a, prio_a), (b, prio_b)):
                p = base / "config" / "org-policy.json"
                o = json.loads(p.read_text())
                o["clause_priorities"] = prios
                p.write_text(json.dumps(o))
        return a, b

    def test_logrolling_reduces_disputes_under_conservative_x_conservative(self):
        a_no, b_no = self._setup_pair(with_priorities=False)
        rep_no = run_simulation(a_no, b_no, "conservative", "conservative")
        disputes_no = sum(1 for v in rep_no["final_clause_status"].values() if v == "disputed")

        a_yes, b_yes = self._setup_pair(with_priorities=True)
        rep_yes = run_simulation(a_yes, b_yes, "conservative", "conservative")
        disputes_yes = sum(1 for v in rep_yes["final_clause_status"].values() if v == "disputed")

        self.assertLess(
            disputes_yes, disputes_no,
            f"With priorities ({disputes_yes}) should resolve more clauses than without ({disputes_no})",
        )

    def test_winner_per_clause_reflects_logrolling(self):
        a, b = self._setup_pair(with_priorities=True)
        rep = run_simulation(a, b, "conservative", "conservative")
        winners = rep["winner_per_clause"]
        # mutuality and return_or_destroy: A's bottom-priority, both diverged → B wins
        # residuals: B's bottom-priority, diverged → A wins
        if "mutuality" in winners:
            self.assertEqual(winners["mutuality"], "b", f"B should win mutuality (in A's concession zone)")
        if "return_or_destroy" in winners:
            self.assertEqual(winners["return_or_destroy"], "b", f"B should win return_or_destroy (in A's concession zone)")
        if "residuals" in winners:
            self.assertEqual(winners["residuals"], "a", f"A should win residuals (in B's concession zone)")


class FatigueConcessionTests(unittest.TestCase):
    """Tests for the fatigue concession rule."""

    @classmethod
    def setUpClass(cls):
        cls.workdir = Path(tempfile.mkdtemp(prefix="nda-fatigue-"))

    def _setup(self, max_bounces: int):
        a = self.workdir / f"a-mb{max_bounces}"
        b = self.workdir / f"b-mb{max_bounces}"
        quickstart(a, "Acme")
        quickstart(b, "Beta")
        make_party_b_divergent(b)
        for base in (a, b):
            p = base / "config" / "org-policy.json"
            o = json.loads(p.read_text())
            o.setdefault("defaults", {})["max_clause_bounces"] = max_bounces
            p.write_text(json.dumps(o))
        return a, b

    def test_fatigue_resolves_conservative_x_conservative(self):
        """Default max_clause_bounces=4: cons × cons converges via fatigue."""
        a, b = self._setup(max_bounces=4)
        report = run_simulation(a, b, "conservative", "conservative", max_rounds=20)
        # Should converge or at least make significant progress through fatigue
        agreed = sum(1 for v in report["final_clause_status"].values() if v == "agreed")
        self.assertGreater(agreed, 7, f"Expected most clauses agreed via fatigue; got {agreed}")
        # Check the state file for fatigue tags
        state = json.loads(Path(report["state_file"]).read_text())
        fatigue_rounds = [r for r in state["rounds"] if "+fatigue" in r.get("amendment_source", "")]
        self.assertGreater(len(fatigue_rounds), 0)
        # Each fatigued round should list which clauses were fatigue-conceded
        for r in fatigue_rounds:
            self.assertIn("fatigue_concessions", r)
            self.assertGreater(len(r["fatigue_concessions"]), 0)

    def test_fatigue_disabled_with_max_bounces_zero(self):
        """max_clause_bounces=0 disables fatigue → cons × cons blocks as before."""
        a, b = self._setup(max_bounces=0)
        report = run_simulation(a, b, "conservative", "conservative")
        self.assertEqual(report["outcome"], "blocked")

    def test_fatigue_only_concedes_bouncing_clauses(self):
        """A clause that converges normally (no bouncing) should not get fatigue-concessions."""
        a, b = self._setup(max_bounces=4)
        report = run_simulation(a, b, "compromising", "compromising", max_rounds=10)
        # Compromising x compromising converges in round 2; nothing bounces
        state = json.loads(Path(report["state_file"]).read_text())
        for r in state["rounds"]:
            self.assertEqual(
                r.get("fatigue_concessions") or [], [],
                f"Round {r['round']} unexpectedly applied fatigue: {r.get('fatigue_concessions')}",
            )


class ConcessionZoneTests(unittest.TestCase):
    """Direct unit tests of the concession-zone math."""

    @classmethod
    def setUpClass(cls):
        # Import the helpers directly for unit-level testing.
        import sys as _sys
        _sys.path.insert(0, str(REPO))
        import nda_review_cli as _cli
        cls.cli = _cli

    def test_concession_zone_sizes_per_stance(self):
        rules = {f"c{i}": {} for i in range(11)}
        priorities = list(rules.keys())
        zone_cons = self.cli._negotiate_concession_zone(rules, priorities, "conservative")
        zone_mid = self.cli._negotiate_concession_zone(rules, priorities, "middleground")
        zone_comp = self.cli._negotiate_concession_zone(rules, priorities, "compromising")
        # 11 clauses: 30%=3, 60%=7, 85%=9
        self.assertEqual(len(zone_cons), 3)
        self.assertEqual(len(zone_mid), 7)
        self.assertEqual(len(zone_comp), 9)
        # Concession zone is the *bottom* of the priority list
        self.assertEqual(zone_cons, {"c8", "c9", "c10"})

    def test_unranked_clauses_default_to_bottom(self):
        rules = {f"c{i}": {} for i in range(5)}
        priorities = ["c0", "c1"]  # only 2 explicitly ranked; others go to bottom
        zone_cons = self.cli._negotiate_concession_zone(rules, priorities, "conservative")
        # 5 clauses * 30% = 1.5 → round = 2 → bottom 2 (the unranked ones, lowest)
        self.assertIn("c4", zone_cons)


if __name__ == "__main__":
    unittest.main()
