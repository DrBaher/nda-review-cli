# Reference

Concept-level reference for `nda-review-cli`. Each file is the canonical home for one topic — everything else in the repo links here.

| File | Topic |
|---|---|
| [policy.md](policy.md) | The policy file (`config/org-policy.json`) — clause rules, red flags, scoring weights. The single source of truth for what your house style is. |
| [stance.md](stance.md) | Negotiation stance + clause priorities. How `--auto` decides what to concede on. The game-theoretic predictions. |
| [fatigue.md](fatigue.md) | Fatigue concession — the deterministic deadlock-breaker for symmetric negotiations. |
| [scoring.md](scoring.md) | Scoring profiles (`balanced` / `strict` / `commercial`), decision thresholds, calibration. |
| [state-file.md](state-file.md) | The hash-chained negotiation state file. What it stores, how tamper-detection works. |
| [exit-codes.md](exit-codes.md) | The exit-code map every command honors, plus stable error codes. |
| [llm-data-flow.md](llm-data-flow.md) | What leaves your machine when you pass `--llm`, and what doesn't. |
