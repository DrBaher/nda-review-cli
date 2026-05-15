# Stance + clause priorities

How the deterministic `negotiate counter --auto` agent decides what to concede on.

## The two knobs

| Knob | What it controls | Set in |
|---|---|---|
| **Stance** | The *size* of your concession zone — how many clauses you're willing to let slide. | `config/org-policy.json` `defaults.negotiation_stance`, or `--stance` per round. |
| **Priorities** | *Which* clauses are in the concession zone. Top-priority clauses you insist on; bottom-priority ones you concede. | `config/org-policy.json` `defaults.clause_priorities` (top → bottom). Set during `quickstart`. |

Stance defines how many; priorities define which.

## The three stances

| Stance | Concession zone | Insists on | Behavior |
|---|---|---|---|
| `conservative` | bottom **30%** | top 70% | Counters every clause that materially differs from preferred. Rejects most amendments. |
| `middleground` (default) | bottom **60%** | top 40% | Compromises on low-severity items. Holds firm on high-severity / red-flag clauses. |
| `compromising` | bottom **85%** | top 15% | Accepts most amendments unless they trigger a red-flag pattern. Pushes back only on dealbreakers. |

## How it composes

For each clause `c` and stance `s`:

1. If `c` is in the **non-negotiable** set → counter every time the text differs, regardless of stance or priority.
2. Otherwise, if `c` is in the bottom `(stance_size)%` of your priority list → accept the current text. This is your concession zone.
3. Otherwise → apply stance's red-flag/diff logic:
   - `conservative`: counter if text differs at all from preferred.
   - `middleground`: counter if a red-flag pattern fires OR the diff is materially adverse.
   - `compromising`: counter only if a red-flag pattern fires.

## Game-theoretic predictions

The `negotiate simulate` command validates these empirically. Pairwise outcomes for two parties running `--auto`:

| Party A × Party B | Outcome | Rounds | Why |
|---|---|---|---|
| `conservative × conservative` | converged via fatigue | ~6 (3 clauses force-conceded) | Pure-stance equilibrium would block; [fatigue concession](fatigue.md) force-resolves bouncing clauses. |
| `conservative × conservative`, `max_clause_bounces=0` | blocked | ~7 | Original behavior — symmetric strict preferences have no Nash equilibrium. Useful when you want deadlocks surfaced for human escalation. |
| `conservative × middleground` | converged | 2–3 | M concedes on non-red-flag clauses, A holds firm and wins the contested ones. |
| `conservative × compromising` | converged | 2–3 | C concedes everywhere; only A's red flags get negotiated. |
| `middleground × middleground` | converged | 2 | Both sides push back only on red flags; usually one round resolves them. |
| `middleground × compromising` | converged | 2 | C concedes; M just polices red flags. |
| `compromising × compromising` | converged | 2 | Both sides accept everything that doesn't fire a red flag. |

## Why priorities matter for convergence

With 11 typical clauses there are 11! ≈ 40 million orderings — the chance two real teams have identical priorities is effectively zero. As long as A's bottom-30% covers some clauses B insists on (and vice versa), those clauses converge through **logrolling** — the classic resolution for multi-issue bargaining where parties trade concessions across issues they value differently.

The residual stalemate is bounded to clauses where *both* parties' top priorities overlap. If the overlap is too large and the stalemate detector trips (`status: blocked`), the CLI surfaces the stuck clauses via `block_diagnosis` for human escalation.

## When to override per-round

- `--stance conservative` when negotiating a high-stakes document and you don't want auto-concession.
- `--stance compromising` when the counterparty is much larger and concessions are expected — useful for vendor-side NDAs where the counterparty's template is essentially non-negotiable.

The per-round override doesn't change your stored stance; it only affects that single round.

## See also

- [policy.md](policy.md) — where stance, priorities, and non-negotiables are stored.
- [fatigue.md](fatigue.md) — the deadlock-breaker for the conservative × conservative case.
- [examples/negotiate-cheatsheet.md](../../examples/negotiate-cheatsheet.md) — one-page reference for every negotiate subcommand.
