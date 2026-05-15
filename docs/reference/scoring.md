# Scoring profiles

How the rule engine turns clause-level findings into a final document-level decision.

## Built-in profiles

Three profiles ship in `config/scoring-profiles.json`:

| Profile | Stance | When to use |
|---|---|---|
| `balanced` (default) | Equal weight to legal/commercial/operational findings | Most general-purpose NDAs |
| `strict` | Heavier weight on legal findings + higher severity multipliers | Regulated industries, high-stakes commercial deals |
| `commercial` | Lighter weight on operational findings | Commodity vendor NDAs, low-stakes diligence |

Each profile has two parts:

- **`weights`** — per-category multipliers and severity multipliers.
- **`decision_thresholds`** — score cutoffs for `approve` / `escalate` / `block`.

## Per-profile shape

```json
{
  "balanced": {
    "weights": {
      "legal": 1.2,
      "commercial": 1.0,
      "operational": 1.0,
      "severity_high": 3,
      "severity_low": 1
    },
    "decision_thresholds": {
      "approve_max": 4.99,
      "escalate_max": 9.99
    }
  }
}
```

## The math

For each review:

1. Each finding has a category (`legal` / `commercial` / `operational`) and a severity (`high` / `med` / `low`).
2. `finding_score = severity_multiplier * category_weight`.
3. `total_score = sum(finding_score for finding in findings)`.
4. Decision:
   - `total_score <= approve_max` → `approve`
   - `total_score <= escalate_max` → `escalate`
   - else → `block`

## Customizing

Edit `config/scoring-profiles.json` to add or modify profiles:

```bash
nda-review-cli review --file contract.docx --scoring-profile strict
nda-review-cli review --file contract.docx --scoring-profile your-custom-profile
```

## Calibration

`calibrate-scoring` evaluates a labeled validation set against a profile and reports the false-positive / false-negative rate at each threshold:

```bash
nda-review-cli calibrate-scoring \
  --playbook output/nda_playbook.json \
  --validation-set tests/fixtures/scoring_validation_set.json \
  --scoring-profile balanced \
  --out-json output/calibration.json
```

The validation set is a JSON array of `{file, expected_decision}` entries. Useful for tuning a profile against historical reviews where you know the right outcome.

## See also

- [policy.md](policy.md) — clause rules drive the findings that scoring turns into decisions.
- [stance.md](stance.md) — stance affects which findings get raised in the first place.
