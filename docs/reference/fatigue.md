# Fatigue concession

The deterministic deadlock-breaker for symmetric negotiations.

## The problem

Two `conservative` parties with overlapping priorities and identical non-negotiables can't converge through pure stance + priority logic — they each insist on the same clauses, neither concedes, and the negotiation bounces those clauses forever. In game-theoretic terms, the multi-issue bargaining game has no Nash equilibrium under symmetric strict preferences.

The historical CLI behavior was to hit the stalemate detector and flip to `status: blocked` with a `block_diagnosis`. Useful for surfacing deadlocks to humans, but it left rounds stuck.

## The fix

Every clause has a **bounce count**: the number of consecutive most-recent rounds in which that clause was amended by alternating proposers. Once a clause's bounce count reaches `max_clause_bounces` (default **4**), the next round's proposer is **forced to accept the current text** regardless of stance, priority, or red flags.

The choice of who concedes is determined by round parity (whoever proposes round K+1 concedes), so the rule is predictable and auditable.

## Tagging

Fatigue-conceded amendments are tagged `auto:<stance>+fatigue` in the round, and the sign-off step surfaces them as a separate "Fatigue concessions (review carefully)" block. Humans always see which clauses were force-resolved and can override before signing.

## Tuning

```jsonc
// config/org-policy.json
{
  "defaults": {
    "max_clause_bounces": 4    // default
  }
}
```

| Value | Effect |
|---|---|
| `4` (default) | Conservative × conservative converges in ~6 rounds with 1–3 fatigue concessions. Comfortable balance. |
| `1–3` | Faster convergence but more clauses force-conceded without earning genuine agreement. |
| `0` | Fatigue disabled. Symmetric `conservative × conservative` falls back to blocked behavior — useful when you want deadlocks surfaced for human escalation rather than auto-resolved. |
| `> 4` | Slower convergence; more rounds before any forced concession. Useful when you want exhaustive auto-negotiation before any concession. |

## Empirical validation

`negotiate simulate` walks all stance pairings under both fatigue-enabled (`max_clause_bounces: 4`) and fatigue-disabled (`max_clause_bounces: 0`) configurations. See `tests/test_negotiate_simulate.py` — it locks in the predicted matrix as a regression.

```bash
nda-review-cli negotiate simulate \
  --party-a-base /path/to/party-a-workspace \
  --party-b-base /path/to/party-b-workspace \
  --stance-a conservative --stance-b conservative \
  --mode auto --max-rounds 12
```

The report includes per-round trajectory (agreed / disputed / proposed counts), winner-per-clause for converged outcomes, source breakdown (`manual` / `auto:<stance>` / `agent:<stance>` / `auto:<stance>+fatigue`), and a `block_diagnosis` listing stuck clauses for blocked outcomes.

## Why this approach

Real-world negotiators get tired of arguing the same clause repeatedly and concede the issues that are bouncing the most. Fatigue concession encodes that pattern deterministically — rather than randomizing (which would break the determinism guarantee) we apply a fixed rule that mirrors real bargaining behavior.

The alternative — leaving the negotiation blocked — is worse for two reasons:

1. It forces a human into the loop even on low-stakes clauses where both parties would actually concede if they had to choose.
2. The deadlock isn't informative — it tells you nothing about which clauses *actually* mattered. Fatigue concession reveals that by force-resolving the clauses neither party can find a reason to fight harder over.

## Non-negotiables override fatigue

Clauses listed in `config/org-policy.json` `non_negotiable_clauses` are **never** fatigue-conceded. If both parties have overlapping non-negotiables that conflict, the negotiation goes `blocked` rather than auto-resolving — the clear signal that this deal can't close on these terms.

## See also

- [stance.md](stance.md) — how the stance × priority composition leads to the pathological case.
- [policy.md](policy.md) — where `max_clause_bounces` and `non_negotiable_clauses` are configured.
