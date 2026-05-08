# NDA Review CLI (Medicus)

Builds an NDA negotiation playbook from your extracted Gmail/Drive corpus, then reviews NDA text against that playbook.

## Commands

```bash
cd /Users/bbot/.openclaw/workspace/projects/nda-review-cli-medicus

# 1) Build playbook from raw_strict dataset
./nda_review_cli.py build-playbook

# Outputs:
# - output/medicus_nda_playbook.json
# - output/medicus_nda_playbook.md

# 2) One-command review for a new NDA (recommended)
./review_nda.sh /path/to/nda.txt

# 3) Direct review command
./nda_review_cli.py review --file /path/to/nda.txt

# Optional: counterparty profile-aware review (loads profiles/<name>.json)
./nda_review_cli.py review --file /path/to/nda.txt --counterparty "Healthchecks360"

# 4) Review inline text
./nda_review_cli.py review --text "Mutual NDA ..."

# 5) Onboarding config wizard (non-interactive flags shown)
./nda_review_cli.py init --org-name "Acme" --risk-posture balanced --preferred-jurisdictions "Austria,Germany"

# 6) Ingest existing knowledge (contracts/redlines/playbooks)
./nda_review_cli.py ingest --files /path/to/nda1.txt /path/to/redline_notes.txt

# 7) Combined setup (init + optional ingest)
./nda_review_cli.py setup --org-name "Acme" --ingest-files /path/to/nda1.txt
```

## Expected input files

- `data/raw_strict/gmail_baher_strict.json`
- `data/raw_strict/gmail_personal_strict.json`
- `data/raw_strict/drive_baher_strict.json`
- `data/raw_strict/drive_personal_strict.json`

## Notes

- This is a rules-first MVP generated from corpus signals.
- Use output playbook as a living policy file and refine clause positions over time.


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
./nda_review_cli.py playbook-lock --counterparty "Healthchecks360"

# Clause-ready redline draft from review JSON
./nda_review_cli.py generate-redlines --review-json output/reviews/review-*.json --out output/reviews/clause-ready-redline.md

# Office Script bridge from Step 5 pack
./nda_review_cli.py generate-office-script --find-replace-pack output/reviews/find-replace-pack-*.md --out output/tracked-redline/office-script.ts

# Quality gate before Step 4
./nda_review_cli.py quality-gate --redline output/reviews/redline-instructions-*.md --source-text /path/to/nda.txt --out-json output/reviews/quality-gate.json
```

## Quality gates in Step 4

`step4_prepare_tracked_redline.sh` now checks:
- Step 3 redline pack has actionable numbered items
- Potential AI clause contradiction signals (allow + prohibit)

## Tests & CI

```bash
python3 -m py_compile nda_review_cli.py step2_pass2_review.py generate_redline_instructions.py rule_engine.py
python3 -m unittest discover -s tests -p 'test_*.py' -v
```

GitHub Actions runs the same checks on push/PR.

## Anchor safety mode (Step 5)

```bash
STRICT_ANCHORS=1 ./step5_find_replace_pack.sh /path/to/source.txt /path/to/redline-instructions.md
```

In strict mode, Step 5 fails if any find-anchor is not unique.
