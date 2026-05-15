# Agents

Drive `nda-review-cli` from an LLM agent or non-interactive client. Same three-file shape as the rest of the suite.

## Output contract

- **Success**: structured JSON to **stdout**, exit `0`. Reviews emit `{review: {findings: [...], decision, scores}}`; negotiation commands emit the new state file (or a diff of it).
- **Failure**: `{ok: false, error: {code, message, details?}}` to **stderr**, non-zero exit. Stable error `code` values include `POLICY_INVALID`, `STATE_HASH_MISMATCH`, `MISSING_PLAYBOOK`, `LLM_DECLINED`, `STALEMATE_DETECTED`.
- Human-readable summaries also go to **stderr** by default. Suppress them with `NDA_CLI_QUIET=1` for clean pipelines.

## Exit codes

| Code | Meaning |
|------|---------|
| `0` | Success |
| `2` | Invalid input (missing flag, malformed value, schema fail) |
| `3` | Policy / verification failed — stale playbook, hash-chain mismatch, stalemate, LLM consent denied |
| `4` | Not found — missing file, missing counterparty profile, missing playbook |

## Discovery

```bash
nda-review-cli --catalog json    # full subcommand + flag inventory (machine-readable)
nda-review-cli --help            # human-readable index
nda-review-cli <cmd> --help      # per-command help with concrete examples in --help epilogs
nda-review-cli --version
```

Call `--catalog json` at startup to know what's available. Don't hardcode subcommand names.

## The agent-as-negotiator architecture

`nda-review-cli` was designed for an agent driving the operational work while a human owns the deliberate-gesture gates. Three places the agent runs:

- **`review --llm`** — second-pass adjudication. The agent votes on each rule finding, adds findings the rules missed, and proposes replacement clause language for high-severity items. Deterministic findings are never overwritten.
- **`negotiate counter --agent --llm`** — drafts amendments aligned with your policy + stance + clause priorities. The hash-chained state file is signed by you, not the agent.
- **`signer policy run`** — a declarative policy spec that gates which clauses the agent can sign off on. (Future scope; today, sign-off is always human.)

The state file format is hash-chained: any tampering breaks `negotiate validate` and the next load. A human can audit between agent rounds exactly what was proposed, line by line.

## Failure → recovery

| Symptom | Diagnose | Recover |
|---|---|---|
| `MISSING_FLAG` / `POLICY_INVALID` | `nda-review-cli <cmd> --help` (or `--catalog json` for the machine surface) | Fix the flag, or run `nda-review-cli policy-validate --file <path>`. |
| `STATE_HASH_MISMATCH` on `negotiate counter` | `nda-review-cli negotiate validate --state <path>` | The state file was tampered with or corrupted in transit. Restore from the last known-good copy; don't continue. |
| `MISSING_PLAYBOOK` | `nda-review-cli doctor` | Run `setup --quick --yes` to auto-discover ingest sources and build a playbook. |
| `LLM_DECLINED` | Provider returned a refusal | Try a different model, or fall back to `--auto` (deterministic, no LLM). Don't silently strip `--strict-fidelity`-equivalent guards. |
| `STALEMATE_DETECTED` | `negotiate analyze --state <path>` for the stuck clauses | Surface the `block_diagnosis` to a human. Don't auto-resolve; the stalemate exists for a reason. |
| Counterparty profile unknown | `nda-review-cli profile-learn --counterparty <name> --review-json <path>` | One-shot learning from a saved review. |

## Recommended defaults

```bash
# Read-only review with explainability evidence + counterparty profile
nda-review-cli review --file contract.docx --why \
  --counterparty "Vendor Co" \
  --out-json ./review.json

# After config/llm.json is set up, layer in opt-in LLM adjudication
nda-review-cli doctor --check-llm    # confirms provider reachability before sending
nda-review-cli review --file contract.docx --why --llm --yes-llm-send \
  --out-json ./review.json
```

For agent-first negotiation:

```bash
nda-review-cli negotiate counter --state negotiation.json \
  --as b --agent --llm --yes-llm-send --dry-run    # preview before committing
nda-review-cli negotiate counter --state negotiation.json \
  --as b --agent --llm --yes-llm-send              # commit the round
```

Always pair `--agent` with `--dry-run` first in non-interactive contexts; the dry-run output lets you inspect what the LLM proposed before the round is signed and added to the chain.

## LLM safety

- **NDA text leaving the box.** `--llm` is opt-in per call. Without `--llm`, no contract text leaves the machine. With `--llm`, the CLI prints the destination (provider + base URL + model) and asks for consent unless `--yes-llm-send` or `NDA_LLM_NO_CONFIRM=1` is set.
- **Fully local inference.** For NDAs you can't legally send to a third-party model, use `--llm ollama` (local) or `--llm openai-compatible --llm-base-url http://your-internal:1234/v1` (any OpenAI-compatible endpoint — vLLM, LM Studio, an internal proxy).
- **Per-call consent.** The non-negotiable safety rail: each `--llm` call confirms the destination before sending. Don't bypass it in batch unless every NDA in the batch has been cleared for that provider.
- **Network I/O scope.** Network calls happen only on the `--llm` code path. Documented in [SECURITY.md](SECURITY.md).

## Discovery commands

| Command | Returns |
|---|---|
| `--catalog json` | Full subcommand + flag inventory. Stable across minor versions. |
| `--help` | Human-readable index with grouped subcommands. |
| `<cmd> --help` | Per-subcommand help with `--help` epilogs (concrete examples). |
| `examples` | Curated walkthroughs covering review, draft, negotiate. |
| `tutorial` | Interactive primer; runs a sandboxed sample review. |
| `doctor` | Diagnose first-run issues. Add `--check-llm` to verify LLM provider reachability. |
| `doctor --check-llm` | 1-token round-trip to the configured LLM provider. Confirms reachability + auth + model name. |

## Library use (Python)

```python
from nda_review_cli import review_nda, load_policy

policy = load_policy("config/org-policy.json")
result = review_nda(text=nda_text, policy=policy, learn_profile=False)
# → { "findings": [...], "decision": "escalate", "scores": {...} }
```

Stdlib-only at runtime. LLM augmentation requires only `urllib`; no `anthropic` / `openai` SDK dependency.

## See also

- [docs/setup/](docs/setup/) — LLM provider configuration (Anthropic, OpenAI, Ollama, OpenAI-compatible).
- [docs/reference/](docs/reference/) — concept deep-dives (policy, stance, fatigue, scoring profiles, hash-chained state).
- [examples/negotiate-cheatsheet.md](examples/negotiate-cheatsheet.md) — one-page negotiation reference.
- [SECURITY.md](SECURITY.md) — threat model + LLM data-flow disclosure.
- [CHANGELOG.md](CHANGELOG.md) — what landed and when.
