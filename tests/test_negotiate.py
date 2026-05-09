"""End-to-end tests for the two-party negotiate flow."""
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
    org_path = td / "config" / "org-policy.json"
    org = json.loads(org_path.read_text())
    org["org_name"] = org_name
    org_path.write_text(json.dumps(org))


class NegotiateTests(unittest.TestCase):
    def setUp(self):
        self.workdir = Path(tempfile.mkdtemp(prefix="nda-neg-test-"))
        self.party_a = self.workdir / "a"
        self.party_b = self.workdir / "b"
        self.shared = self.workdir / "shared"
        self.shared.mkdir(parents=True, exist_ok=True)
        quickstart(self.party_a, "Acme")
        quickstart(self.party_b, "Beta")
        self.state = self.shared / "state.json"

    def _run(self, *args, expect_code: int = 0):
        result = subprocess.run(
            ["python3", str(CLI), *args],
            cwd=REPO,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, expect_code, msg=f"args={args} stderr={result.stderr}")
        return result

    def test_full_negotiation_round_trip(self):
        # 1. Party A initializes
        self._run(
            "negotiate", "init",
            "--base", str(self.party_a),
            "--template", "mutual",
            "--party-a-name", "Acme",
            "--party-a-address", "1 Main",
            "--party-b-name", "Beta",
            "--party-b-address", "2 Side",
            "--purpose", "evaluating a partnership",
            "--effective-date", "2026-05-09",
            "--out", str(self.state),
        )
        state = json.loads(self.state.read_text())
        self.assertEqual(state["status"], "in_progress")
        self.assertEqual(len(state["rounds"]), 1)
        self.assertEqual(state["rounds"][0]["proposer"], "a")
        # Hash chain: round 1's hash is deterministic from initial text + ""
        self.assertTrue(state["rounds"][0]["text_hash"])

        # 2. Party B reviews — read-only, no state changes
        before = self.state.read_text()
        self._run("negotiate", "review", "--base", str(self.party_b), "--state", str(self.state))
        self.assertEqual(self.state.read_text(), before, "review must not mutate state")

        # 3. Party B counters with manual amendments
        amendments = self.workdir / "b-amendments.json"
        amendments.write_text(json.dumps({
            "accept_clauses": ["definition_of_confidential_information", "exceptions"],
            "counter_amendments": [{
                "clause": "term_and_survival",
                "old_text": state["rounds"][0]["text"].split("## 5. Term and Survival\n\n")[1].split("\n\n")[0],
                "new_text": "NDA term 3 years with confidentiality survival of 7 years. Trade-secret protection extends indefinitely.",
                "rationale": "Beta requires longer survival.",
            }],
            "summary": "Beta accepts most clauses but requests longer term + survival.",
        }))
        self._run(
            "negotiate", "counter",
            "--base", str(self.party_b),
            "--state", str(self.state),
            "--amendments-file", str(amendments),
        )
        state = json.loads(self.state.read_text())
        self.assertEqual(len(state["rounds"]), 2)
        self.assertEqual(state["rounds"][1]["proposer"], "b")
        self.assertIn("definition_of_confidential_information", state["rounds"][1]["accept_clauses"])
        self.assertEqual(state["clause_status"]["term_and_survival"]["status"], "disputed")
        self.assertEqual(state["clause_status"]["definition_of_confidential_information"]["status"], "agreed")

        # 4. Party A accepts → converged
        self._run(
            "negotiate", "accept",
            "--base", str(self.party_a),
            "--state", str(self.state),
        )
        state = json.loads(self.state.read_text())
        self.assertEqual(state["status"], "converged")
        self.assertEqual(state["clause_status"]["term_and_survival"]["status"], "agreed")

        # 5. Finalize
        out_md = self.workdir / "final.md"
        out_docx = self.workdir / "final.docx"
        self._run(
            "negotiate", "finalize",
            "--base", str(self.party_a),
            "--state", str(self.state),
            "--out-md", str(out_md),
            "--out-docx", str(out_docx),
        )
        self.assertTrue(out_md.exists())
        self.assertTrue(out_docx.exists())
        # Amendment landed in the final text
        self.assertIn("NDA term 3 years", out_md.read_text())

    def test_cannot_counter_your_own_round(self):
        self._run(
            "negotiate", "init",
            "--base", str(self.party_a),
            "--template", "mutual",
            "--party-a-name", "Acme", "--party-a-address", "1 Main",
            "--party-b-name", "Beta", "--party-b-address", "2 Side",
            "--purpose", "x", "--effective-date", "2026-05-09",
            "--out", str(self.state),
        )
        # Party A tries to counter their own round 1 — should error
        amendments = self.workdir / "a-amendments.json"
        amendments.write_text(json.dumps({"accept_clauses": [], "counter_amendments": [], "summary": ""}))
        result = self._run(
            "negotiate", "counter",
            "--base", str(self.party_a),
            "--state", str(self.state),
            "--amendments-file", str(amendments),
            expect_code=1,  # SystemExit raises with default code 1 when given a string
        )
        self.assertIn("proposed by you", result.stderr)

    def test_finalize_blocked_when_not_converged(self):
        self._run(
            "negotiate", "init",
            "--base", str(self.party_a),
            "--template", "mutual",
            "--party-a-name", "Acme", "--party-a-address", "1 Main",
            "--party-b-name", "Beta", "--party-b-address", "2 Side",
            "--purpose", "x", "--effective-date", "2026-05-09",
            "--out", str(self.state),
        )
        result = self._run(
            "negotiate", "finalize",
            "--base", str(self.party_a),
            "--state", str(self.state),
            "--out-md", str(self.workdir / "final.md"),
            "--out-docx", str(self.workdir / "final.docx"),
            expect_code=1,
        )
        self.assertIn("converged", result.stderr)

    def test_hash_chain_detects_tampering(self):
        self._run(
            "negotiate", "init",
            "--base", str(self.party_a),
            "--template", "mutual",
            "--party-a-name", "Acme", "--party-a-address", "1 Main",
            "--party-b-name", "Beta", "--party-b-address", "2 Side",
            "--purpose", "x", "--effective-date", "2026-05-09",
            "--out", str(self.state),
        )
        # Tamper with the round 1 text but leave the hash unchanged
        state = json.loads(self.state.read_text())
        state["rounds"][0]["text"] = state["rounds"][0]["text"] + "\n\nINSERTED MALICIOUS CLAUSE."
        self.state.write_text(json.dumps(state))
        result = self._run(
            "negotiate", "review",
            "--base", str(self.party_b),
            "--state", str(self.state),
            expect_code=1,
        )
        self.assertIn("Hash-chain mismatch", result.stderr)

    def test_finalize_invokes_configured_hooks(self):
        # Set up a converged negotiation
        self._run(
            "negotiate", "init",
            "--base", str(self.party_a),
            "--template", "mutual",
            "--party-a-name", "Acme", "--party-a-address", "1 Main",
            "--party-b-name", "Beta", "--party-b-address", "2 Side",
            "--purpose", "x", "--effective-date", "2026-05-09",
            "--out", str(self.state),
        )
        empty_amendments = self.workdir / "empty.json"
        empty_amendments.write_text(json.dumps({
            "accept_clauses": ["definition_of_confidential_information", "exceptions", "term_and_survival",
                               "use_restrictions", "return_or_destroy", "residuals", "assignment_and_affiliates",
                               "governing_law_jurisdiction", "liability_and_remedies", "non_solicit_non_compete",
                               "mutuality"],
            "counter_amendments": [],
            "summary": "Beta accepts all clauses as proposed.",
        }))
        self._run(
            "negotiate", "counter",
            "--base", str(self.party_b),
            "--state", str(self.state),
            "--amendments-file", str(empty_amendments),
        )
        self._run(
            "negotiate", "accept",
            "--base", str(self.party_a),
            "--state", str(self.state),
        )
        state = json.loads(self.state.read_text())
        self.assertEqual(state["status"], "converged")

        # Configure fake hooks that just `cp` the input to the output to simulate the chain
        integrations_path = self.party_a / "config" / "integrations.json"
        integrations_path.write_text(json.dumps({
            "docx2pdf_cmd": "cp {input_docx} {output_pdf}",
            "sign_cli_cmd": "cp {input_pdf} {output_pdf}",
        }))

        out_md = self.workdir / "final.md"
        out_docx = self.workdir / "final.docx"
        self._run(
            "negotiate", "finalize",
            "--base", str(self.party_a),
            "--state", str(self.state),
            "--out-md", str(out_md),
            "--out-docx", str(out_docx),
            "--to-pdf", "--sign",
        )
        # docx2pdf hook produced the .pdf; sign hook produced the .signed.pdf
        self.assertTrue(out_docx.with_suffix(".pdf").exists())
        signed = out_docx.with_suffix("").with_name(out_docx.stem + ".signed.pdf")
        self.assertTrue(signed.exists(), f"missing {signed}")

        state = json.loads(self.state.read_text())
        self.assertEqual(state["status"], "finalized")
        self.assertEqual(len(state["finalized"]["hooks"]), 2)
        self.assertEqual(state["finalized"]["hooks"][0]["hook"], "docx2pdf")
        self.assertEqual(state["finalized"]["hooks"][1]["hook"], "sign_cli")


if __name__ == "__main__":
    unittest.main()
