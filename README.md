# NDA Review CLI

Builds an NDA negotiation playbook from a user's extracted Gmail/Drive corpus, then reviews NDA text against that playbook.

## Commands

```bash
cd /path/to/nda-review-cli

# 1) Build playbook from raw_strict dataset
./nda_review_cli.py build-playbook

# Outputs:
# - output/nda_playbook.json
# - output/nda_playbook.md

# 2) One-command review for a new NDA (recommended)
./review_nda.sh /path/to/nda.txt

# 3) Direct review command
./nda_review_cli.py review --file /path/to/nda.txt

# Optional: counterparty profile-aware review (loads profiles/<name>.json)
./nda_review_cli.py review --file /path/to/nda.txt --counterparty "Counterparty Name"

# Review explainability mode with concise evidence
./nda_review_cli.py review --file /path/to/nda.txt --why --out-json output/reviews/review.json --out-md output/reviews/review.md

# Review + deterministic profile learning
./nda_review_cli.py review --file /path/to/nda.txt --counterparty "Counterparty Name" --learn-profile --out-json output/reviews/review.json

# 4) Review inline text
./nda_review_cli.py review --text "Mutual NDA ..."

# 5) Onboarding config wizard (non-interactive flags shown)
./nda_review_cli.py init --org-name "Acme" --template saas --risk-posture balanced --preferred-jurisdictions "Austria,Germany"

# 6) Ingest existing knowledge (contracts/redlines/playbooks)
./nda_review_cli.py ingest --files /path/to/nda1.txt /path/to/redline_notes.txt

# Approve autodiscovered onboarding files without prompting
./nda_review_cli.py ingest --yes

# 7) Combined setup (init + optional ingest)
./nda_review_cli.py setup --org-name "Acme" --ingest-files /path/to/nda1.txt --build

# Import connector shortcuts
./nda_review_cli.py ingest --contracts-dir /path/to/contracts
./nda_review_cli.py ingest --drive-export-dir /path/to/google-drive-export

# 8) Fastest onboarding (zero required args)
./nda_review_cli.py setup --quick --yes

# 9) Guided wizard flow
./nda_review_cli.py wizard --quick --yes --review-file /path/to/nda.txt --out-json output/reviews/wizard.json

# 10) Score calibration against a labeled validation set
./nda_review_cli.py calibrate-scoring --validation-set tests/fixtures/scoring_validation_set.json --scoring-profile balanced --out-json output/calibration.json

# 11) Release notes helper
./nda_review_cli.py release-helper --version 0.4.0 --out output/release-notes-0.4.0.md

# 12) Validate policy files and first-run environment
./nda_review_cli.py policy-validate --file config/default-policy.json
./nda_review_cli.py doctor
```

## Onboarding shortcuts

- `./nda_review_cli.py setup --quick --yes` → writes base config + profile using defaults, auto-discovers ingest files, and runs `build-playbook` by default.
- `setup --quick` now defaults `build=true`; use `--no-build` to skip, or `--build` on non-quick setup to opt in.
- `init` supports opinionated templates: `--template saas|healthcare|enterprise`.
- `init`, `review`, `setup`, and `wizard` accept `--scoring-profile` plus optional `--scoring-profiles config/scoring-profiles.json`.
- If you do not pass `--ingest-files`, `setup` and `ingest` auto-scan:
  - `knowledge/inbox/`
  - `knowledge/contracts/`
  - `knowledge/redlines/`
  - `inbox/`
  - `input/`
- `ingest --contracts-dir` recurses through a local contracts folder.
- `ingest --drive-export-dir` recurses through a downloaded Google Drive export folder such as `My Drive/` or `Takeout/`.
- When files are auto-discovered, the CLI shows the candidate list and asks for confirmation unless you pass `--yes` or `--no-prompt`.
- If nothing is found and you are in an interactive terminal, the CLI asks once for file paths.

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
./nda_review_cli.py profile-learn --counterparty "Counterparty Name" --review-json output/reviews/review.json
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
