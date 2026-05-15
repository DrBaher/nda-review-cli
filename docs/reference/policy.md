# Policy

Your house rules. The single source of truth for what the rule engine looks for in every NDA.

## Files

- **`config/default-policy.json`** — generic seed shipped with the repo. Edit if you want to change the defaults for new users; otherwise leave alone.
- **`config/org-policy.json`** — your local override. Gitignored. This is where your house-specific tuning lives.
- **`--policy <path>`** — per-invocation override. Wins over both files.

Load order (highest priority first):

1. `--policy <path>` (if passed)
2. `config/org-policy.json` (if present)
3. `config/default-policy.json` (the seed)
4. Minimal built-in fallback (only used when no file is found)

## Schema

```json
{
  "version": "0.5.0",
  "org_name": "Your Org",
  "clause_rules": {
    "definition_of_confidential_information": {
      "keywords": ["confidential information", "trade secret"],
      "preferred": "Confidential Information means information disclosed by either party that is identified as confidential at the time of disclosure, or that reasonably should be understood as confidential.",
      "red_flags": ["no objective boundary", "perpetual obligation on receiving party"]
    },
    "term_length": {
      "keywords": ["term", "duration"],
      "preferred": "This Agreement remains in effect for two (2) years from the Effective Date.",
      "red_flags": ["perpetual term", "unlimited duration"]
    }
  },
  "negotiation_signal_patterns": {
    "risk": ["liability", "indemn", "unlimited", "perpetual"],
    "operational": ["return", "destroy", "audit"]
  },
  "defaults": {
    "negotiation_stance": "middleground",
    "clause_priorities": ["definition_of_confidential_information", "term_length"],
    "max_clause_bounces": 4
  },
  "non_negotiable_clauses": ["definition_of_confidential_information"]
}
```

Required top-level keys: `org_name`, `clause_rules`, `negotiation_signal_patterns`.

Required per clause rule: `keywords`, `preferred`, `red_flags`.

## Per-clause fields

| Field | Type | What it does |
|---|---|---|
| `keywords` | string[] | Regex-friendly substrings that identify the clause in any document. The first match wins. |
| `preferred` | string | Your preferred language for this clause. Used by `draft` to render new NDAs and by `negotiate counter --auto` to propose amendments. |
| `red_flags` | string[] | Patterns that, if found in the clause text, raise a finding. Use them for stance markers like `"perpetual obligation"`, `"unilateral termination"`, `"foreign jurisdiction"`. |

## Tuning workflow

1. Start with `quickstart` — it walks 14 questions and writes your stance, priorities, non-negotiables, term cap, residual-knowledge stance, trade-secret carve-out, affiliate-disclosure scope into `config/org-policy.json` for you.
2. Edit `config/org-policy.json` directly when `quickstart` doesn't cover a clause. The structure is human-readable; reload happens on every CLI invocation.
3. Run `policy-validate --file config/org-policy.json` after each edit to catch schema errors before they break a review.
4. Run `review --file tests/fixtures/sample_nda.txt --why` to see how the new rules behave against a known fixture.

## Editing safety

- The CLI never overwrites `config/org-policy.json` without explicit user action (`quickstart`, `init`, `setup` with a clean target).
- `profile-learn` writes to `profiles/<name>.json`, not to the policy. Profiles are auxiliary memory; the policy is the rule book.
- `negotiate counter --learn-profile` is the same — it updates profile state, not the policy.

## Versioning

`version` must be `>= MIN_POLICY_VERSION` (currently `0.2.0`). `policy-validate` enforces this. The CLI never auto-migrates a policy; manual edits keep the audit trail clean.

## See also

- [stance.md](stance.md) — how priorities + stance compose into the concession-zone formula.
- [fatigue.md](fatigue.md) — what happens when both parties stay conservative.
- [scoring.md](scoring.md) — how weights and thresholds shape the final decision label.
