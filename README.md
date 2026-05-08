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
