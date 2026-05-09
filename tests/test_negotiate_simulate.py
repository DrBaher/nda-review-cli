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


def run_simulation(party_a: Path, party_b: Path, stance_a: str, stance_b: str, max_rounds: int = 10) -> dict:
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

    @classmethod
    def setUpClass(cls):
        cls.workdir = Path(tempfile.mkdtemp(prefix="nda-sim-"))
        cls.party_a = cls.workdir / "a"
        cls.party_b = cls.workdir / "b"
        quickstart(cls.party_a, "Acme")
        quickstart(cls.party_b, "Beta")
        make_party_b_divergent(cls.party_b)

    def test_conservative_x_conservative_blocks(self):
        """Pure 'never give ground' on both sides → mutual rejection equilibrium → no deal."""
        report = run_simulation(self.party_a, self.party_b, "conservative", "conservative")
        self.assertEqual(report["outcome"], "blocked")
        # Stalemate detector kicks in within max-rounds
        self.assertLess(report["rounds_used"], 10)
        # At least some clauses still disputed (the ones where preferred text differs)
        disputed = sum(1 for v in report["final_clause_status"].values() if v == "disputed")
        self.assertGreaterEqual(disputed, 1)
        self.assertIsNotNone(report.get("block_diagnosis"))

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

    def test_blocked_state_includes_stuck_clauses(self):
        """When blocked, the diagnosis must list the clauses still disputed."""
        report = run_simulation(self.party_a, self.party_b, "conservative", "conservative")
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


if __name__ == "__main__":
    unittest.main()
