# `negotiate` cheatsheet

One-page reference for the two-party NDA negotiation flow. For the full story see [README → Negotiating between two parties](../README.md#negotiating-between-two-parties) and [ARCHITECTURE → Game-theoretic foundations](../ARCHITECTURE.md#game-theoretic-foundations-of-negotiate).

## Lifecycle at a glance

```
init ──► counter ◄─────► counter ──► accept ──► sign-off (×2) ──► finalize
                  ↑          │                                       │
                  │          └─► dry-run (preview)                   ├─► .md
                  │                                                  ├─► .docx
                  │                                          (opt) ──┼─► .pdf
                  ├─► diff (review changes)                  (opt) ──┴─► signed.pdf
                  ├─► simulate (test stance combos)
                  ├─► analyze (post-hoc dashboard)
                  └─► withdraw (graceful exit)
```

## Per-party setup (one-time)

```bash
nda-review-cli quickstart                # answers Q1..Q16 incl. stance, priorities
# Edit config/org-policy.json to set non_negotiable_clauses if you have absolute redlines
# Optionally edit config/llm.json for --agent mode
# Optionally edit config/integrations.json for --to-pdf and --sign hooks at finalize
```

## Commands

| Command | Purpose | Key flags |
|---|---|---|
| `negotiate init` | Round 1, signed by Party A | `--template mutual\|one-way-out`, `--purpose`, `--out`, party name/address fields |
| `negotiate review` | Read-only; score current text vs your policy | `--state`, `--as a\|b` |
| `negotiate counter` | Sign next round with amendments | `--state`, `--as`, one of: `--amendments-file <path>` / `--auto` / `--agent --llm <provider>`, plus `--dry-run`, `--stance` (override) |
| `negotiate accept` | Sign next round accepting current text wholesale | `--state`, `--as` |
| `negotiate diff` | Show clause-by-clause changes between two rounds | `--state`, `--from-round`, `--to-round`, `--out-md` |
| `negotiate status` | Show round history, per-clause status, signatures | `--state` |
| `negotiate analyze` | Post-hoc dashboard: trajectory, winners, source breakdown, fatigue summary | `--state` |
| `negotiate validate` | Standalone integrity check: schema + hash-chain + per-round structural shape | `--state` (exits 2 on failure) |
| `negotiate sign-off` | Required human gate: review key points, batch-confirm | `--state`, `--as`, `--yes` |
| `negotiate finalize` | Emit agreed `.md` + `.docx`; optional PDF + sign hooks | `--state`, `--out-md`, `--out-docx`, `--to-pdf`, `--sign`, `--skip-signoff` (testing) |
| `negotiate withdraw` | Graceful exit; flips status to `withdrawn` | `--state`, `--as`, `--reason` |
| `negotiate simulate` | Run both sides on one machine; emit a structured report (game-theoretic validation) | `--party-a-base`, `--party-b-base`, `--stance-a`, `--stance-b`, `--mode auto\|agent`, `--max-rounds` |

## Counter modes

| Mode | When to use | Determinism |
|---|---|---|
| `--amendments-file <path>` | Manual: hand-write amendments JSON | Fully deterministic — you wrote it |
| `--auto` | Stance-driven deterministic agent. No LLM, no API keys, fully on-prem. | Fully deterministic |
| `--agent --llm <provider>` | LLM-driven. Same provider list as `review --llm`: `anthropic` / `openai` / `ollama` / `openai-compatible`. | LLM call is non-deterministic; everything else (fatigue, hash chain, etc.) is deterministic |

Add `--dry-run` to any of these to preview the proposal without writing the round.

## Stance × stance outcomes (with default fatigue + priorities)

| Stance pair | Outcome | Rounds | Notes |
|---|---|---|---|
| conservative × conservative | converged via fatigue | ~6 | Stuck clauses force-conceded after `max_clause_bounces` (default 4). Tagged `+fatigue` in source. |
| conservative × middleground | converged | 2–3 | M concedes; A wins contested clauses |
| conservative × compromising | converged | 2 | C concedes everywhere; A wins almost all clauses |
| middleground × middleground | converged | 2 | Red-flag-only pushback |
| middleground × compromising | converged | 2 | C concedes; M polices red flags |
| compromising × compromising | converged | 2 | First draft sticks |

`negotiate simulate` lets you validate any pairing against your actual policies.

## Convergence guarantees

The CLI guarantees one of these terminal outcomes within bounded rounds:

| Status | What it means |
|---|---|
| `converged` | Both parties have alternated to mutually-acceptable text. Run sign-off → finalize. |
| `blocked` | Stalemate detector tripped (no progress for `max_no_progress_rounds`, default 4). Likely a non-negotiable conflict. Human escalation needed. |
| `withdrawn` | One party walked away. Terminal. |
| `signed_off` | Both parties have completed sign-off. Ready to finalize. |
| `finalized` | Final `.md` + `.docx` (and optional PDF/signed-PDF) emitted. |

## Tuning knobs (in `config/org-policy.json`)

```json
{
  "negotiation_stance": "middleground",
  "clause_priorities": ["term_and_survival", "residuals", "..."],
  "non_negotiable_clauses": ["definition_of_confidential_information"],
  "defaults": {
    "max_clause_bounces": 4,        // 0 disables fatigue
    "max_no_progress_rounds": 4     // stalemate detector threshold
  }
}
```

## Common patterns

### A drafts, B negotiates with their LLM agent, A reviews diff and accepts

```bash
# Party A
nda-review-cli negotiate init --template mutual ... --out neg.json
# (send neg.json to B)

# Party B
nda-review-cli negotiate counter --state neg.json --as b --agent --llm ollama --yes-llm-send --dry-run
nda-review-cli negotiate counter --state neg.json --as b --agent --llm ollama --yes-llm-send
# (send updated neg.json back to A)

# Party A
nda-review-cli negotiate diff --state neg.json --out-md round2.md
$EDITOR round2.md
nda-review-cli negotiate accept --state neg.json --as a
```

### Both sides sign off, finalize through external tools

```bash
nda-review-cli negotiate sign-off --state neg.json --as a   # interactive
nda-review-cli negotiate sign-off --state neg.json --as b   # interactive
nda-review-cli negotiate finalize --state neg.json \
  --out-md output/agreed.md --out-docx output/agreed.docx \
  --to-pdf --sign
```

### Validate stance combination before committing to a real negotiation

```bash
nda-review-cli negotiate simulate \
  --party-a-base /workspaces/team-a \
  --party-b-base /workspaces/team-b \
  --stance-a conservative --stance-b conservative \
  --mode auto --max-rounds 12 \
  --out simulation-report.json

nda-review-cli negotiate analyze --state simulation-report.json   # if you saved the state too
```

### Walk away

```bash
nda-review-cli negotiate withdraw --state neg.json --as a \
  --reason "deal terms unacceptable; closing the deal isn't worth the AI-data exposure"
```
