# Getting Started

A 10-minute, hands-on guide to going from `git clone` to your first reviewed NDA. If you just want to skim, jump to [Five-minute happy path](#five-minute-happy-path).

## Prerequisites

- **Python 3.9+** (no third-party packages required for the core flow)
- **macOS, Linux, or WSL** — paths and shell scripts assume a POSIX environment
- **Optional but recommended:**
  - `pdftotext` (from `poppler-utils`) for PDF ingestion on Linux
  - `textutil` is built into macOS and used as a `.docx`/`.pdf` fallback
  - `pandoc` if you want richer redline conversions later

Quick check:

```bash
python3 --version
which pdftotext   # optional
```

## Install

```bash
git clone <this-repo-url> nda-review-cli
cd nda-review-cli
chmod +x nda_review_cli.py review_nda.sh run_all.sh step*.sh
```

There is nothing to `pip install`. The CLI is a single self-contained Python file.

## Mental model

Three things to internalize before you run anything:

1. **Policy** is your house rules. It lives in `config/`. You edit it.
2. **Profile** is a per-counterparty memory file under `profiles/`. The CLI updates it for you when you pass `--learn-profile`.
3. **Playbook** is the compiled artifact in `output/nda_playbook.json`. You regenerate it whenever you change policy or feed in new contracts.

If you ever feel lost, run `./nda_review_cli.py tutorial` — it explains the same loop with sample data.

## Five-minute happy path

```bash
# A) Run the interactive primer (optional but recommended for first-timers)
./nda_review_cli.py tutorial

# B) Bootstrap config + profile + playbook with safe defaults
./nda_review_cli.py setup --quick --yes

# C) Confirm everything is wired up
./nda_review_cli.py doctor

# D) Review the bundled sample NDA to see the full output shape
./nda_review_cli.py review --file tests/fixtures/sample_nda.txt --why \
  --out-json output/reviews/first-review.json \
  --out-md output/reviews/first-review.md

# E) Open the markdown summary in your editor
$EDITOR output/reviews/first-review.md
```

You should now see:

- `config/org-policy.json` — your editable rules
- `profiles/default.json` — starter profile
- `output/nda_playbook.json` and `output/nda_playbook.md`
- `output/reviews/first-review.{json,md}` — the review with explainability evidence

## Reviewing your first real NDA

```bash
# Single-file review with explainability evidence
./nda_review_cli.py review --file /path/to/their-nda.txt --why \
  --out-json output/reviews/their-nda.json \
  --out-md output/reviews/their-nda.md

# Or use the pre-baked one-shot script
./review_nda.sh /path/to/their-nda.txt
```

Working with a known counterparty? Add their name and let the profile learn:

```bash
./nda_review_cli.py review --file /path/to/their-nda.txt --why \
  --counterparty "Acme Corp" --learn-profile \
  --out-json output/reviews/acme-2025-q1.json \
  --out-md output/reviews/acme-2025-q1.md
```

Subsequent reviews against `--counterparty "Acme Corp"` will pick up `profiles/Acme Corp.json` automatically.

## Feeding in your own corpus

Two easy paths:

### Local contracts folder

```bash
./nda_review_cli.py ingest --contracts-dir ~/Documents/nda-archive --yes
./nda_review_cli.py build-playbook
```

### Google Drive Takeout export

```bash
./nda_review_cli.py ingest --drive-export-dir ~/Downloads/Takeout --yes
./nda_review_cli.py build-playbook
```

Or skip the flags and drop files into one of the autodiscovery roots:

- `knowledge/inbox/`
- `knowledge/contracts/`
- `knowledge/redlines/`
- `inbox/`
- `input/`

Then run `./nda_review_cli.py ingest` (it will list candidates and confirm).

## End-to-end pipeline

When you're ready to produce a redline pack ready for Word:

```bash
./run_all.sh /path/to/nda.docx "Acme Corp" "Your Name"
```

Outputs land under `output/reviews/` and `output/tracked-redline/`.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `doctor` reports "No valid policy file found" | First run, no config yet | `./nda_review_cli.py init --base .` (or `setup --quick --yes`) |
| `doctor` reports "Missing build-playbook input: data/raw_strict/..." | You haven't extracted your Gmail/Drive corpus | Either supply paths via `--gmail-paths`/`--drive-paths`, or use the `--contracts-dir`/`--drive-export-dir` ingest flow instead |
| "Unreadable ingest candidate: foo.pdf" | No PDF extractor installed | Install `poppler-utils` (`pdftotext`) on Linux, or convert the file to `.txt`/`.md` |
| Review JSON says no findings | Playbook is empty or stale | `./nda_review_cli.py build-playbook` and rerun |
| `policy-validate` errors with `version` | Policy file pre-dates `0.2.0` schema | Open the JSON, set `"version": "0.2.0"`, ensure `org_name`, `clause_rules`, and `negotiation_signal_patterns` exist |
| Want a clean slate | Mixed test/real artifacts in repo | `rm -rf config/org-policy.json profiles/ output/ knowledge/proposed/` then `setup --quick --yes` |

Run `./nda_review_cli.py doctor` whenever something feels off — it prints the suggested fix for each detected issue.

## Where to go next

- **Concepts deep-dive:** [README.md → Core concepts](README.md#core-concepts)
- **Wizard flow:** `./nda_review_cli.py wizard --quick --yes --review-file tests/fixtures/sample_nda.txt`
- **Counterparty learning:** [README.md → Review explainability and profile learning](README.md#review-explainability-and-profile-learning)
- **Scoring profiles & calibration:** [README.md → Scoring profiles and calibration](README.md#scoring-profiles-and-calibration)
- **Run the test suite:** `python3 -m unittest discover -s tests -p 'test_*.py' -v`
