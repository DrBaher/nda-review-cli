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

Two equivalent options.

### A. Clone and run (no install)

```bash
git clone https://github.com/DrBaher/nda-review-cli.git
cd nda-review-cli
chmod +x nda_review_cli.py review_nda.sh run_all.sh step*.sh
./nda_review_cli.py --version
```

### B. pipx-install for system-wide use

```bash
pipx install git+https://github.com/DrBaher/nda-review-cli.git
nda-review-cli --version
```

Either way, there are no runtime dependencies — the CLI is stdlib-only Python (`>=3.9`). The pipx form gives you `nda-review-cli` on `$PATH`; the cloned form uses `./nda_review_cli.py`. Examples below use the cloned form.

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

# A2) Or, for a guided 14-question setup that wires your preferences into clause rules
./nda_review_cli.py quickstart

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

## Onboarding scenarios

Pick the path that matches your situation.

### Scenario A — Solo lawyer, no historical corpus

You just want to review NDAs against a sensible default policy.

```bash
./nda_review_cli.py setup --quick --yes
./nda_review_cli.py review --file /path/to/nda.txt --why \
  --out-md output/reviews/nda.md
```

You'll lean on `config/default-policy.json` (the committed seed). Edit `config/org-policy.json` whenever you want to override a clause rule. Skip ingest entirely.

**Want to encode your stance up front?** Run `./nda_review_cli.py quickstart` instead of `setup --quick`. It asks 14 questions covering term length, return-vs-destroy, residual-knowledge stance, trade-secret carve-out, and affiliate-disclosure scope — each answer changes the clause rules and red-flag patterns the review engine uses. Saves a `config/quickstart-answers.json` you can replay non-interactively later.

### Scenario B — In-house legal, large contracts archive

You have a folder of past NDAs and want the playbook to reflect your house style.

```bash
./nda_review_cli.py setup --quick --yes \
  --org-name "Acme Legal" \
  --template enterprise \
  --contracts-dir ~/Documents/nda-archive

./nda_review_cli.py build-playbook
./nda_review_cli.py doctor

./nda_review_cli.py review --file /path/to/incoming-nda.docx --why \
  --counterparty "Vendor Co" --learn-profile \
  --out-json output/reviews/vendor-co-2025q2.json \
  --out-md output/reviews/vendor-co-2025q2.md
```

Run this once per new counterparty; the profile improves over time.

### Scenario C — Migrating from manual Word redlines

You already have redline `.docx` files and want the CLI to learn from them.

```bash
mkdir -p knowledge/redlines knowledge/contracts
cp ~/redlines/*.docx knowledge/redlines/
cp ~/contracts/*.docx knowledge/contracts/

./nda_review_cli.py ingest --yes
./nda_review_cli.py build-playbook
```

If `pdftotext` isn't installed and you have PDFs, either install `poppler-utils` or pre-convert with `textutil` (macOS) / `pdftotext` (Linux).

### Scenario D — Google Drive Takeout export

```bash
# Download a Google Takeout for your Drive, unzip it under ~/Downloads/Takeout
./nda_review_cli.py setup --quick --yes \
  --drive-export-dir ~/Downloads/Takeout

./nda_review_cli.py build-playbook
```

The CLI handles `My Drive/`, `Shared drives/`, and Takeout folder layouts.

### Scenario E — SaaS team rolling this out internally

Use a template + scoring profile per team:

```bash
./nda_review_cli.py setup --quick --yes \
  --org-name "Acme SaaS" \
  --template saas \
  --scoring-profile balanced \
  --contracts-dir /shared/contracts/nda-archive
```

Commit a sanitized `config/default-policy.json` to the repo. Keep `config/org-policy.json` and `profiles/` per-user (already gitignored).

### Scenario F — Drafting an outgoing NDA

After `quickstart` (or any `setup`), generate a fresh NDA in your house language:

```bash
# Mutual NDA
./nda_review_cli.py draft \
  --template mutual \
  --party-a "Acme Inc." --party-a-address "123 Main St" \
  --party-b "Beta LLC"  --party-b-address "10 Market Way" \
  --purpose "evaluating a strategic partnership" \
  --out output/drafts/mutual.md \
  --out-docx output/drafts/mutual.docx

# One-way disclosing — you share, the other side does not
./nda_review_cli.py draft \
  --template one-way-out \
  --disclosing-party "Acme Inc." --disclosing-party-address "123 Main St" \
  --receiving-party "Vendor Co"  --receiving-party-address "100 Lake Rd" \
  --purpose "vendor onboarding diligence" \
  --out output/drafts/oneway.md \
  --out-docx output/drafts/oneway.docx \
  --review-after
```

Clause text is pulled from `config/org-policy.json` `clause_rules[*].preferred`, so anything tuned via `quickstart` (term length, return-vs-destroy, residual stance, trade-secret carve-out, affiliate scope) flows in automatically. `--review-after` round-trips the draft through `review --why` so you see your own outgoing language scored by the same lens.

### Scenario G — Adding a second-pass LLM (Anthropic / OpenAI / local Ollama)

The deterministic review is enough on its own. If you want a model to vote on findings, add ones the rules missed, and suggest replacement clause language, opt in with `--llm`.

```bash
# Bootstrap: copy the example, fill in provider + model + key
cp config/llm.json.example config/llm.json
$EDITOR config/llm.json

# Fully on-prem with Ollama (no cloud, no key)
./nda_review_cli.py review --file /path/to/nda.txt --why \
  --llm ollama --llm-model qwen2.5:14b --yes-llm-send \
  --out-md output/reviews/with-llm.md

# Anthropic Claude
NDA_LLM_API_KEY=sk-ant-... ./nda_review_cli.py review --file /path/to/nda.txt --why \
  --llm anthropic --llm-model claude-sonnet-4-6 --yes-llm-send \
  --out-md output/reviews/with-llm.md

# Once configured in config/llm.json, just `--llm`
./nda_review_cli.py review --file /path/to/nda.txt --why --llm --yes-llm-send
```

Important:
- The CLI prints the destination (provider + base URL + model) and waits for Enter before sending. Pass `--yes-llm-send` or set `NDA_LLM_NO_CONFIRM=1` to skip the prompt in CI.
- Sending NDA text to a third-party provider may breach the NDA itself. Use Ollama or a local OpenAI-compatible server (vLLM, LM Studio) when on-prem inference matters.
- See [SECURITY.md → LLM data flow](SECURITY.md#llm-data-flow-opt-in) for the full list of what's sent and to whom.

### Scenario H — Negotiating an NDA between two parties

Both parties have their own CLI install. They exchange a single state file by any channel they prefer.

```bash
# === Party A's machine ===
./nda_review_cli.py quickstart                              # set Acme's policy
./nda_review_cli.py negotiate init \
  --template mutual \
  --party-a-name "Acme" --party-a-address "1 Main" \
  --party-b-name "Beta" --party-b-address "2 Side" \
  --purpose "evaluating a partnership" \
  --out negotiation.json
# Send negotiation.json to Party B (email / Drive / Git)

# === Party B's machine ===
./nda_review_cli.py quickstart                              # set Beta's policy
./nda_review_cli.py negotiate review --state negotiation.json
./nda_review_cli.py negotiate counter --state negotiation.json --as b \
  --agent --llm ollama --yes-llm-send                       # local LLM agent drafts counter
# Review the resulting amendments in `negotiate status` before sending back
./nda_review_cli.py negotiate status --state negotiation.json
# Send updated negotiation.json back to Party A

# === Party A's machine ===
./nda_review_cli.py negotiate review --state negotiation.json
./nda_review_cli.py negotiate accept --state negotiation.json --as a
# Status: converged → finalize
./nda_review_cli.py negotiate finalize \
  --state negotiation.json \
  --out-md output/agreed.md \
  --out-docx output/agreed.docx \
  --to-pdf --sign                                           # uses config/integrations.json hooks
```

Set up `config/integrations.json` (gitignored) once per machine to wire in your own `docx2pdf` and `sign-CLI` tools — see `config/integrations.json.example` for placeholders. Every round is signed and hash-chained; tampering is detected on load.

## Where to go next

- **How the CLI is structured:** [ARCHITECTURE.md](ARCHITECTURE.md)
- **Concepts deep-dive:** [README.md → Core concepts](README.md#core-concepts)
- **Wizard flow:** `./nda_review_cli.py wizard --quick --yes --review-file tests/fixtures/sample_nda.txt`
- **Counterparty learning:** [README.md → Review explainability and profile learning](README.md#review-explainability-and-profile-learning)
- **Scoring profiles & calibration:** [README.md → Scoring profiles and calibration](README.md#scoring-profiles-and-calibration)
- **Contributing:** [CONTRIBUTING.md](CONTRIBUTING.md)
- **Run the test suite:** `python3 -m unittest discover -s tests -p 'test_*.py' -v`
