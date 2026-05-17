# The hash-chained negotiation state file

Every `nda-review-cli negotiate` invocation reads and writes a single JSON document — the state file. Both parties hold this file at any given time; it bounces between them via email, shared drive, Git, or any other channel.

## Shape

```json
{
  "negotiation_id": "...",
  "parties": { "a": { "name": "...", "address": "..." }, "b": { ... } },
  "purpose": "...",
  "template": "mutual",
  "rounds": [
    {
      "round": 1,
      "proposer": "a",
      "text": "<full clause-by-clause text>",
      "text_hash": "sha256:...",
      "clause_status": {
        "definition_of_confidential_information": "agreed",
        "term_length": "disputed"
      },
      "amendments": [
        { "clause": "term_length", "source": "auto:middleground", "text": "..." }
      ],
      "signed_at": "2026-05-09T12:34:56Z",
      "signed_by": "a",
      "hash_prev": "sha256:..." ,
      "hash_self": "sha256:..."
    }
  ],
  "status": "open",
  "stalemate_counter": 0,
  "signoffs": {}
}
```

## Hash chain

Each round's `hash_self` is `sha256(text + text_hash + amendments + signed_by + signed_at + hash_prev)`. The first round's `hash_prev` is the empty string.

On every load, the CLI re-computes every `hash_self` and re-links every `hash_prev`. Any mismatch raises `STATE_HASH_MISMATCH` (exit `3`). This catches:

- Manual editing of past rounds (the most common form of tampering).
- Corruption in transit (email/Drive sync bugs).
- Reordering of rounds.

`negotiate validate --state <path>` runs the same chain verification standalone, with no other side effects. Useful when receiving a state file from a counterparty before you start the next round.

## What the chain proves

- **Order** — events are ordered by `round`, but the chain anchors that order.
- **Authenticity of each round** — a round can't be substituted without the next round's `hash_prev` breaking.
- **Completeness** — a missing round breaks the next round's `hash_prev`.

## What it doesn't prove

- **Identity of the signer.** `signed_by` is a string the CLI writes; it doesn't carry a digital signature. If you need cryptographic signer-identity proof, hand off to `sign-cli` via the `--sign` hook on `finalize`.
- **Convergence is real.** The CLI computes `status` from `clause_status` but doesn't enforce that the clause text actually matches between parties' copies of the file. (It usually does — both parties read the same JSON.)

## Statuses

| Status | Meaning |
|---|---|
| `open` | Active negotiation, more rounds expected. |
| `converged` | All clauses `agreed`; awaiting sign-off. |
| `blocked` | Stalemate detector tripped; no progress for `stalemate_threshold` rounds. |
| `signed_off` | Both parties have signed off; `finalize` can proceed. |
| `withdrawn` | One party walked away via `negotiate withdraw --reason ...`. |
| `finalized` | `finalize` has emitted the agreed `.md` + `.docx`. Terminal. |

## Sign-off

`negotiate sign-off --as a/b` is a required human checkpoint before `negotiate finalize`. Once `status: converged`, both parties review the **key points** (clauses changed from the initial draft, amendments applied with source, red-flag patterns still present in the final text) and sign off. `finalize` is blocked until both `signoffs.a` and `signoffs.b` are populated.

This is the gate that lets you trust agent-assisted rounds without losing human review. Fatigue-conceded clauses are listed in their own block during sign-off so they get extra attention.

## Integration hooks

`negotiate finalize --to-pdf --sign` invokes user-configured commands from `config/integrations.json` (see [docs/setup/integrations.md](../setup/integrations.md)). The CLI passes placeholders like `{input_docx}`, `{output_pdf}`, `{negotiation_id}` to the configured shell command. The state file records the hand-off in the finalize block.

## Downstream consumers

[compare-cli](https://github.com/DrBaher/compare-cli) (v0.1.1+) reads
this state file via its `--from-negotiation NEG.json CANDIDATE` flag to
pull the agreed text as the BASE for clause-aware drift detection
against a candidate document (e.g. the ready-to-sign PDF the
counterparty sent back). The reader is permissive: it accepts the
top-level `status` field as the authoritative convergence signal
(matching `"converged"`, `"signed_off"`, or `"finalized"`), with
per-round `agreed: true` and per-round `clause_status` all-`"agreed"`
fallbacks for back-compat with earlier `negotiation.json` shapes.

compare-cli v0.2.0+ additionally supports `--require-signoffs`, which
errors if both `signoffs.a` and `signoffs.b` aren't populated — useful
when an unattended pipeline shouldn't proceed against a converged-but-
not-yet-signed-off state file. compare-cli does **not** verify the hash
chain; if integrity matters, pipe through `negotiate validate` first.

See compare-cli's [`COMPARE_SCHEMA.md` §9.2](https://github.com/DrBaher/compare-cli/blob/main/COMPARE_SCHEMA.md)
for the exact three-tier resolution order.

## See also

- [policy.md](policy.md) — policies drive what gets flagged in each round.
- [stance.md](stance.md) + [fatigue.md](fatigue.md) — how `auto` and `agent` modes shape proposals.
- [exit-codes.md](exit-codes.md) — `STATE_HASH_MISMATCH` and other stable codes.
