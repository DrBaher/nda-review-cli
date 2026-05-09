# NDA Review CLI

Review NDAs against your own house playbook — without sending them to a third-party service. The CLI ingests your past contracts, extracts your negotiation style, and applies it as a deterministic, explainable policy to every new NDA.

## What it does

- **Builds a playbook** from your historical Gmail/Drive corpus (or any folder of contracts).
- **Reviews NDAs** clause-by-clause against that playbook with severity-scored findings and explainability evidence.
- **Generates redlines** ready to drop into Word, plus tracked-changes packs and Office Script bridges.
- **Learns counterparty profiles** deterministically so repeat parties get a consistent stance.

Everything runs locally. No model calls, no data leaves the box.

## Quick start (3 commands)

```bash
# 1. Clone and enter the repo
git clone <this-repo-url> nda-review-cli && cd nda-review-cli

# 2. One-shot setup — creates config + profile, auto-discovers any contracts in the repo, builds the playbook
./nda_review_cli.py setup --quick --yes

# 3. Review a sample NDA (bundled fixture) to see the full output
./nda_review_cli.py review --file tests/fixtures/sample_nda.txt --why
```

That's it — `output/nda_playbook.json` and a review JSON/markdown summary are now on disk. New here? See **[GETTING_STARTED.md](GETTING_STARTED.md)** for a guided walkthrough, or run `./nda_review_cli.py tutorial` for an interactive primer.

## Core concepts

| Term | What it is | Where it lives |
|---|---|---|
| **Policy** | Your house rules: clause keywords, preferred language, red flags, risk weights. Edited by humans. | `config/default-policy.json` (seed) → `config/org-policy.json` (your overrides) |
| **Profile** | Per-counterparty memory: their typical positions, what they've conceded, what triggered escalation. Updated automatically with `--learn-profile`. | `profiles/<name>.json` |
| **Playbook** | A snapshot built from your corpus + policy: clause-by-clause guidance the review engine consults. Rebuilt on demand. | `output/nda_playbook.json` (+ `.md`) |

Rule of thumb: **edit the policy, let the profile learn, regenerate the playbook.**

## Common workflows

```bash
# Review a single NDA
./review_nda.sh /path/to/nda.txt

# Review with explainability evidence (triggered phrases, paragraph index, confidence)
./nda_review_cli.py review --file /path/to/nda.txt --why \
  --out-json output/reviews/review.json --out-md output/reviews/review.md

# Counterparty-aware review that also updates their profile
./nda_review_cli.py review --file /path/to/nda.txt \
  --counterparty "Acme Corp" --learn-profile \
  --out-json output/reviews/review.json

# Generate a clause-ready redline draft from a saved review
./nda_review_cli.py generate-redlines --mode v2 \
  --review-json output/reviews/review.json \
  --out output/reviews/clause-ready-redline.md

# Full pipeline: review + hybrid pack + redline + find/replace pack
./run_all.sh /path/to/nda.docx "Counterparty Name" "Reviewer Name"
```

## Onboarding shortcuts

- `./nda_review_cli.py tutorial` → interactive primer that explains the concepts and runs a sample review.
- `./nda_review_cli.py setup --quick --yes` → defaults + auto-discovers ingest files + runs `build-playbook`.
- `./nda_review_cli.py wizard --quick --yes --review-file <nda>` → setup → ingest → build → review in one go.
- `./nda_review_cli.py init --template saas|healthcare|enterprise` → opinionated starting points.
- `./nda_review_cli.py doctor` → diagnose first-run issues with actionable fixes.
- `./nda_review_cli.py policy-validate --file config/default-policy.json` → schema/version check.

If you do not pass `--ingest-files`, `setup` and `ingest` auto-scan:

- `knowledge/inbox/`, `knowledge/contracts/`, `knowledge/redlines/`
- `inbox/`, `input/`

`ingest --contracts-dir <dir>` recurses a local contracts folder. `ingest --drive-export-dir <dir>` recurses a Google Drive Takeout/`My Drive` export.

When files are auto-discovered, the CLI shows the candidate list and asks for confirmation unless you pass `--yes` or `--no-prompt`. If nothing is found and you are in an interactive terminal, the CLI asks once for file paths.

## Policy configuration

The CLI is generic by default:

- Committed seed policy: `config/default-policy.json`
- Local user/org override: `config/org-policy.json` (ignored by git)
- Explicit override: pass `--policy /path/to/policy.json`

`build-playbook` loads policy in this order:

1. `--policy`, if provided
2. `config/org-policy.json`, if present
3. `config/default-policy.json`
4. repo `config/default-policy.json`
5. minimal built-in fallback rules

`policy-validate` enforces:

- semantic `version` with minimum supported version `0.2.0`
- required top-level keys: `org_name`, `clause_rules`, `negotiation_signal_patterns`
- required clause rule shape: `keywords`, `preferred`, `red_flags`
- readable JSON/schema error output with exit code `2` on failure

## Review explainability and profile learning

- `review --why` adds a concise evidence block per finding in JSON and markdown:
  - triggered phrase(s)
  - heading / paragraph index
  - matched rule patterns
  - confidence score
- `review --learn-profile` updates `profiles/<counterparty>.json` deterministically and records:
  - `source_review_file`
  - UTC timestamp
  - changed fields
- You can also learn from an existing review file:

```bash
./nda_review_cli.py profile-learn --counterparty "Counterparty Name" \
  --review-json output/reviews/review.json
```

## Scoring profiles and calibration

- Committed defaults live in `config/scoring-profiles.json`.
- Built-in profile names: `balanced`, `strict`, `commercial`.
- Decision thresholds are file-driven and can be overridden by editing the scoring profiles file.

```bash
./nda_review_cli.py review --file /path/to/nda.txt --scoring-profile strict

./nda_review_cli.py calibrate-scoring \
  --playbook output/nda_playbook.json \
  --validation-set tests/fixtures/scoring_validation_set.json \
  --scoring-profile balanced \
  --out-json output/calibration.json
```

## Expected input files

By default, `build-playbook` looks for:

- `data/raw_strict/gmail_primary.json`
- `data/raw_strict/gmail_secondary.json`
- `data/raw_strict/drive_primary.json`
- `data/raw_strict/drive_secondary.json`

You can override these paths:

```bash
./nda_review_cli.py build-playbook \
  --gmail-paths data/raw_strict/gmail_work.json data/raw_strict/gmail_personal.json \
  --drive-paths data/raw_strict/drive_work.json data/raw_strict/drive_personal.json
```

`doctor` checks these paths, validates discovered policy files, and tests whether autodiscovered ingest candidates are actually readable.

## Ingestion extraction

- `.docx`: tries `word/document.xml` extraction from the zip payload first, then `textutil` fallback on macOS.
- `.pdf`: tries `pdftotext` first, then `textutil` fallback.
- Ingest output now records `extraction_status`, `extractors_tried`, and any extraction `error` per source.

## Notes

- This is a rules-first MVP generated from corpus signals.
- Use the output playbook as a living policy file and refine clause positions over time.
- Keep user/org-specific policies, extracted email/Drive data, and review outputs local.

## One-command pipeline

```bash
./run_all.sh /path/to/nda.docx "Counterparty Name" "Reviewer Name"
```

This runs deterministic review, hybrid pack, step3 redline instructions, and step5 find/replace pack.
If input is `.docx`, it also prepares step4 tracked-redline package.

## Step 2 pass (choose one-by-one or defaults)

```bash
# A) Interactive loop (one-by-one)
./step2_pass2_review.py --pack output/reviews/hybrid-approval-pack-*.md --mode interactive

# B) Apply recommended defaults automatically
./step2_pass2_review.py --pack output/reviews/hybrid-approval-pack-*.md --mode defaults \
  --export-json applied-defaults.json

# C) Apply from explicit JSON decisions
./step2_pass2_review.py --pack output/reviews/hybrid-approval-pack-*.md \
  --decisions-json decisions.json --export-json applied.json

# D) Resume unfinished decisions only (and high severity only)
./step2_pass2_review.py --pack output/reviews/hybrid-approval-pack-*.md \
  --mode interactive --resume --only-high

# E) Accept defaults except selected points
./step2_pass2_review.py --pack output/reviews/hybrid-approval-pack-*.md \
  --mode defaults --accept-defaults-except "2=DROP,5=CONFIRM"
```

`step2_pass2_review.py` writes `Pass 2 decision` + `Final amendment text` for each point,
so Step 3 only includes confirmed/downgraded items.

Default heuristic (`--mode defaults`):
- `high` severity → `CONFIRM`
- `low` severity → `DOWNGRADE`

## Additional utilities

```bash
# Playbook versioning
./nda_review_cli.py playbook-snapshot
./nda_review_cli.py playbook-diff --a output/playbook_versions/playbook-A.json --b output/playbook_versions/playbook-B.json --out output/playbook_versions/diff.patch
./nda_review_cli.py playbook-lock --counterparty "Counterparty Name"

# Clause-ready redline draft from review JSON
./nda_review_cli.py generate-redlines --review-json output/reviews/review-*.json --out output/reviews/clause-ready-redline.md

# Clause-specific redline generator v2
./nda_review_cli.py generate-redlines --mode v2 --review-json output/reviews/review-*.json --out output/reviews/clause-ready-redline-v2.md

# Office Script bridge from Step 5 pack
./nda_review_cli.py generate-office-script --find-replace-pack output/reviews/find-replace-pack-*.md --out output/tracked-redline/office-script.ts

# Quality gate before Step 4
./nda_review_cli.py quality-gate --redline output/reviews/redline-instructions-*.md --source-text /path/to/nda.txt --out-json output/reviews/quality-gate.json

# Release helper from CHANGELOG
./nda_review_cli.py release-helper --version 0.4.0
```

## Wizard flow

`wizard` walks setup -> ingest -> build -> review with plain terminal prompts when stdin is interactive.
For non-interactive runs, pass the same flags directly:

```bash
./nda_review_cli.py wizard \
  --base /tmp/nda-cli \
  --quick \
  --yes \
  --no-prompt \
  --contracts-dir /path/to/contracts \
  --drive-export-dir /path/to/drive-export \
  --review-file /path/to/nda.txt \
  --counterparty "Counterparty Name" \
  --why \
  --learn-profile \
  --out-json output/reviews/wizard-review.json \
  --out-md output/reviews/wizard-review.md
```

## Quality gates in Step 4

`step4_prepare_tracked_redline.sh` checks:
- Step 3 redline pack has actionable numbered items
- Potential AI clause contradiction signals (allow + prohibit)

## Tests & CI

```bash
python3 -m py_compile nda_review_cli.py step2_pass2_review.py generate_redline_instructions.py rule_engine.py
python3 -m unittest discover -s tests -p 'test_*.py' -v
```

GitHub Actions runs the same checks on push/PR.
The CI matrix covers Linux and macOS.

## Anchor safety mode (Step 5)

```bash
STRICT_ANCHORS=1 ./step5_find_replace_pack.sh /path/to/source.txt /path/to/redline-instructions.md
```

In strict mode, Step 5 fails if any find-anchor is not unique.

## Troubleshooting

| Symptom | Try |
|---|---|
| `No valid policy file found` from `doctor` | `./nda_review_cli.py init --base .` then re-run `doctor`. |
| `Missing build-playbook input: data/raw_strict/...` | Use `setup --quick` (skips raw build) or supply real paths via `--gmail-paths`/`--drive-paths`. |
| `Unreadable ingest candidate: foo.pdf` | Install `pdftotext` (`poppler-utils`) or convert the PDF to `.txt`/`.md`. |
| Review finds nothing | Confirm the playbook exists at `output/nda_playbook.json` and rerun `build-playbook`. |
| Want to start over | `rm -rf config/org-policy.json profiles/ output/ knowledge/proposed/` and re-run `setup --quick --yes`. |

For more, run `./nda_review_cli.py doctor` — it prints actionable fixes for each detected issue.
