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

# 4) Review inline text
./nda_review_cli.py review --text "Mutual NDA ..."
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
```

`step2_pass2_review.py` writes `Pass 2 decision` + `Final amendment text` for each point,
so Step 3 only includes confirmed/downgraded items.

Default heuristic (`--mode defaults`):
- `high` severity → `CONFIRM`
- `low` severity → `DOWNGRADE`
