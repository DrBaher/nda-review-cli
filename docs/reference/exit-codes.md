# Exit codes & error envelope

Every `nda-review-cli` invocation honors the same exit-code semantics.

## The map

| Code | Meaning |
|------|---------|
| `0` | Success |
| `1` | Generic / unhandled |
| `2` | Invalid input (missing required flag, malformed value, schema fail) |
| `3` | Policy / verification failed (stale playbook, hash-chain mismatch, stalemate, LLM consent denied) |
| `4` | Not found (missing file, missing counterparty profile, missing playbook) |

## The success envelope

Structured outputs (review JSON, negotiation state, etc.) print to **stdout**, exit `0`. Human-readable summaries go to **stderr** by default — pipeline-friendly consumers can ignore stderr or suppress it with `NDA_CLI_QUIET=1`.

## The error envelope

```json
{
  "ok": false,
  "error": {
    "code": "POLICY_INVALID",
    "message": "Policy file at config/org-policy.json is missing required key 'clause_rules'.",
    "details": {
      "path": "config/org-policy.json",
      "missingKey": "clause_rules"
    }
  }
}
```

## Stable error codes

| Code | Class | Exit | Notes |
|---|---|---|---|
| `MISSING_FLAG` | input | `2` | `details.flag` names the missing flag. |
| `INVALID_ARGS` | input | `2` | Catch-all for malformed input. |
| `POLICY_INVALID` | input | `2` | Policy file fails `policy-validate`. `details.path` + `details.reason`. |
| `MISSING_PLAYBOOK` | not-found | `4` | No playbook on disk; run `setup --quick --yes` or `build-playbook`. |
| `MISSING_INPUT_FILE` | not-found | `4` | The `--file <path>` doesn't exist. |
| `STATE_HASH_MISMATCH` | policy | `3` | Negotiation state file is tampered or corrupted. `details.brokenRound` identifies the first break. |
| `STALEMATE_DETECTED` | policy | `3` | `negotiate counter` after `stalemate_threshold` rounds with no progress. `details.stuckClauses` lists them. |
| `NON_NEGOTIABLE_CONFLICT` | policy | `3` | Both parties' non-negotiables overlap with conflicting text. Surfaces in `block_diagnosis`. |
| `LLM_DECLINED` | policy | `3` | LLM consent prompt rejected, or `--yes-llm-send` missing in non-interactive context. |
| `LLM_PROVIDER_UNREACHABLE` | infra | `3` | `doctor --check-llm` round-trip failed. `details.provider` + `details.baseUrl`. |
| `PROFILE_NOT_FOUND` | not-found | `4` | `--counterparty <name>` but no `profiles/<name>.json`. Use `profile-learn` first. |
| `TEMPLATE_PLACEHOLDER_UNRESOLVED` | input | `2` | `draft --template-file <path>` has `{{KEY}}` placeholders the CLI args didn't fill. |

## Disabling the human-readable summary

```bash
NDA_CLI_QUIET=1 nda-review-cli review --file contract.docx --out-json review.json
```

Stdout still carries the structured JSON; stderr is silent. Useful for batch pipelines that parse stdout.

## See also

- [llm-data-flow.md](llm-data-flow.md) — what `LLM_DECLINED` and the consent prompt protect against.
- [state-file.md](state-file.md) — what `STATE_HASH_MISMATCH` is detecting.
- [policy.md](policy.md) — what `POLICY_INVALID` validates against.
