<p align="center">
  <img src="assets/icon.svg" width="120" alt="nda-review-cli">
</p>

# nda-review-cli

> Part of the three-CLI contract suite. **nda-review-cli** (draft, review, negotiate) → [**docx2pdf-cli**](https://github.com/DrBaher/docx2pdf-cli) (DOCX → PDF) → [**sign-cli**](https://github.com/DrBaher/sign-cli) (signing + audit). [Showcase site](https://cli.drbaher.com/).

[![CI](https://github.com/DrBaher/nda-review-cli/actions/workflows/ci.yml/badge.svg)](https://github.com/DrBaher/nda-review-cli/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue.svg)](https://www.python.org/downloads/)
[![Local-first](https://img.shields.io/badge/local--first-yes-brightgreen.svg)](#why-it-exists)

Built for an agent-first contract workflow — an LLM agent does the operational work (drafting, reviewing, proposing amendments, sending), a human approves the gates that need a deliberate gesture. Deterministic by default, optional second-pass LLM adjudication via the model of your choice (Anthropic / OpenAI / Ollama / any OpenAI-compatible endpoint). Local-first, no telemetry, single-file Python.

The CLI ingests your past contracts, extracts your negotiation style into a versioned playbook, and applies it as a deterministic, explainable policy to every new NDA — with clause-by-clause findings, severity scoring, and Word-ready redlines.

[![asciicast](https://asciinema.org/a/NhznjQC4UXP0et7d.svg)](https://asciinema.org/a/NhznjQC4UXP0et7d)

## Run this

```bash
./nda_review_cli.py tutorial
```

Interactive primer that walks through the concepts (policy / profile / playbook / stance / templates) and runs a sandboxed sample review end to end. Or, if you want to dive straight in:

```bash
./nda_review_cli.py setup --quick --yes && \
./nda_review_cli.py review --file tests/fixtures/sample_nda.txt --why
```

That builds a playbook from auto-discovered contracts in the repo and reviews the bundled fixture with explainability evidence.

> **Try it in the browser:** `python3 web/server.py` from a fresh clone, or deploy the bundled `deploy/Dockerfile` to Railway / Fly / Render. See [web/README.md](web/README.md).

## Where to go next

| If you are… | Start here |
|---|---|
| **A new user** evaluating the tool | This README's [Quick start](#quick-start) and [Negotiation](#negotiation) |
| **An operator** configuring providers / LLM | [docs/setup/](docs/setup/) — Anthropic, OpenAI, Ollama, OpenAI-compatible, integrations |
| **An LLM agent** driving the CLI | [AGENTS.md](AGENTS.md) → `nda-review-cli --catalog json` → [docs/reference/](docs/reference/) |
| **Someone running their first review** | [GETTING_STARTED.md](GETTING_STARTED.md) — scenario-based 10-minute walkthrough |
| **A contributor** | [ARCHITECTURE.md](ARCHITECTURE.md), [CONTRIBUTING.md](CONTRIBUTING.md) |

Concept deep-dives live in [docs/reference/](docs/reference/); LLM provider setup in [docs/setup/](docs/setup/); the one-page negotiate reference in [examples/negotiate-cheatsheet.md](examples/negotiate-cheatsheet.md).

## Why it exists

Sending NDAs to a SaaS reviewer means leaking your counterparty list, your fallback positions, and the wording of every contract you touch. This tool runs entirely on your machine, has no telemetry, and is auditable in a single Python file. Equally important: SaaS contract products assume a human at a browser. These CLIs assume the opposite — an agent doing the operational work and a human only at the explicit approval gates — which is increasingly the shape of how legal ops actually wants to operate.

## What it does

- **Builds a playbook** from your historical Gmail/Drive corpus (or any folder of contracts).
- **Reviews NDAs** clause-by-clause against that playbook with severity-scored findings and explainability evidence.
- **Drafts NDAs** in your house language (`mutual`, `one-way-out`) or against the [Common Paper Mutual NDA](https://commonpaper.com/standards/mutual-nda/1.0) industry standard.
- **Negotiates between two parties** turn by turn, each side running their own CLI + policy + optional LLM agent, with a tamper-evident hash-chained state file passed by any channel (email, Drive, Git).
- **Generates redlines** ready to drop into Word, plus tracked-changes packs and Office Script bridges.
- **Learns counterparty profiles** deterministically so repeat parties get a consistent stance.

Everything runs locally. Without `--llm`, no contract text leaves the box.

## Install

```bash
# Option A: clone and run (no install)
git clone https://github.com/DrBaher/nda-review-cli.git
cd nda-review-cli
./nda_review_cli.py --version

# Option B: pipx-install for system-wide use
pipx install git+https://github.com/DrBaher/nda-review-cli.git
nda-review-cli --version
```

Examples below use the cloned form (`./nda_review_cli.py`) for portability; once pipx-installed, the binary is `nda-review-cli`.

## Quick start

```bash
# 1. Auto-discover any contracts in the repo + build the playbook
./nda_review_cli.py setup --quick --yes

# 2. Review the bundled fixture with explainability evidence
./nda_review_cli.py review --file tests/fixtures/sample_nda.txt --why

# 3. (Optional) Run the 14-question guided setup to tune stance, priorities, non-negotiables
./nda_review_cli.py quickstart
```

`output/nda_playbook.json` + a review JSON/markdown summary land on disk. `quickstart` writes a replayable `config/quickstart-answers.json` so you can re-run non-interactively in CI with `--no-prompt --yes --answers-file <path>`.

## Core concepts

| Term | What it is | Where it lives |
|---|---|---|
| **Policy** | Your house rules: clause keywords, preferred language, red flags, risk weights. Edited by humans. | `config/default-policy.json` (seed) → `config/org-policy.json` (your overrides). See [docs/reference/policy.md](docs/reference/policy.md). |
| **Profile** | Per-counterparty memory: their typical positions, what they've conceded, what triggered escalation. Updated automatically with `--learn-profile`. | `profiles/<name>.json` |
| **Playbook** | A snapshot built from your corpus + policy: clause-by-clause guidance the review engine consults. Rebuilt on demand. | `output/nda_playbook.json` (+ `.md`) |
| **Stance** | How aggressively your agent negotiates: `conservative` / `middleground` / `compromising`. | `config/org-policy.json` `defaults.negotiation_stance`. See [docs/reference/stance.md](docs/reference/stance.md). |

Rule of thumb: **edit the policy, let the profile learn, regenerate the playbook.**

## Reviewing

Score any NDA against your policy. `--why` adds explainability evidence (matched clause keywords, paragraph index, confidence). `--counterparty <name>` enriches with that party's profile; `--learn-profile` updates the profile from this review.

```bash
./nda_review_cli.py review --file path/to/incoming.docx --why \
  --counterparty "Vendor Co" --learn-profile \
  --out-json output/reviews/review.json \
  --out-md output/reviews/review.md
```

For opt-in LLM-augmented review, see [docs/setup/](docs/setup/) — pick the provider (Anthropic, OpenAI, Ollama, OpenAI-compatible) and follow that file.

## Drafting

Three bundled templates. Clause text is pulled directly from `config/org-policy.json` so anything you tuned via `quickstart` flows through.

```bash
# House mutual NDA
./nda_review_cli.py draft --template mutual \
  --party-a "Acme Inc." --party-a-address "123 Main St" \
  --party-b "Beta LLC"  --party-b-address "10 Market Way" \
  --purpose "evaluating a partnership" \
  --out output/drafts/mutual.md --out-docx output/drafts/mutual.docx

# Common Paper Mutual NDA v1.0 (industry standard, CC BY 4.0)
./nda_review_cli.py draft --template common-paper-mutual \
  --party-a "Acme Inc." --party-a-address "123 Main St" \
  --party-b "Beta LLC"  --party-b-address "10 Market Way" \
  --purpose "evaluating a partnership" --governing-law "California" \
  --out output/drafts/cp-mutual.md --out-docx output/drafts/cp-mutual.docx

# One-way disclosing NDA
./nda_review_cli.py draft --template one-way-out \
  --disclosing-party "Acme Inc." --disclosing-party-address "123 Main St" \
  --receiving-party "Vendor Co" --receiving-party-address "100 Lake Rd" \
  --purpose "vendor onboarding diligence" \
  --out output/drafts/oneway.md --review-after
```

Pass `--template-file path/to/your-template.md` to bring your own template with `{{placeholders}}`. Missing placeholders fail loudly with exit `2`. `--review-after` round-trips the draft through `review --why` so you see the same lens applied to your own outgoing language. Pass `--no-disclaimer` to omit the "starting point, not legal advice" header.

## Negotiation

When both sides have their own CLI install, they can co-negotiate an NDA without sending text through any third-party service. The protocol is file-based: each party signs one round at a time and passes the state file via any channel (email, shared Drive, private Git).

```bash
# Party A initializes
nda-review-cli negotiate init --template mutual \
  --party-a-name "Acme Inc." --party-a-address "1 Main" \
  --party-b-name "Beta LLC"  --party-b-address "2 Side" \
  --purpose "evaluating a partnership" --effective-date 2026-05-09 \
  --out negotiation.json

# Party B reviews against their own policy (read-only)
nda-review-cli negotiate review --state negotiation.json

# Party B drafts a counter via LLM agent (preview before committing)
nda-review-cli negotiate counter --state negotiation.json \
  --as b --agent --llm anthropic --yes-llm-send --dry-run
nda-review-cli negotiate counter --state negotiation.json \
  --as b --agent --llm anthropic --yes-llm-send

# Party A accepts (or counters again)
nda-review-cli negotiate accept --state negotiation.json --as a

# Status check, sign-off, finalize
nda-review-cli negotiate status --state negotiation.json
nda-review-cli negotiate sign-off --state negotiation.json --as a
nda-review-cli negotiate sign-off --state negotiation.json --as b
nda-review-cli negotiate finalize --state negotiation.json \
  --out-md output/agreed.md --out-docx output/agreed.docx \
  --to-pdf --sign
```

`--auto` mode runs without an LLM — deterministic stance + clause-priority logic. `--agent --llm` layers in LLM-assisted amendments. Both modes go through the same hash-chained state file with the same human sign-off gate.

For game-theoretic predictions, stance × stance outcomes, fatigue concession (the deterministic deadlock-breaker), and the full negotiate command set, see [docs/reference/stance.md](docs/reference/stance.md), [docs/reference/fatigue.md](docs/reference/fatigue.md), [docs/reference/state-file.md](docs/reference/state-file.md), and the [one-page cheatsheet](examples/negotiate-cheatsheet.md).

`negotiate finalize --to-pdf --sign` invokes user-configured commands in `config/integrations.json` — for example handing off to `docx2pdf-cli` and `sign-cli`. See [docs/setup/integrations.md](docs/setup/integrations.md).

## Onboarding shortcuts

- `tutorial` — interactive primer that explains the concepts and runs a sandboxed sample review.
- `quickstart` — 14-question guided setup; answers wire directly into clause rules + red flags. Replayable via `--answers-file`.
- `setup --quick --yes` — defaults + auto-discovers ingest files + runs `build-playbook` in one shot.
- `wizard --quick --yes --review-file <nda>` — setup → ingest → build → review in one go.
- `init --template saas|healthcare|enterprise` — opinionated starting points.
- `sample-nda --out <path>` — drops the bundled sample NDA into a path of your choice.
- `doctor` — diagnose first-run issues with actionable fixes. Add `--check-llm` to verify a configured LLM provider.

Auto-discovery: if you don't pass `--ingest-files`, `setup` and `ingest` scan `knowledge/inbox/`, `knowledge/contracts/`, `knowledge/redlines/`, `inbox/`, `input/`. `ingest --contracts-dir <dir>` recurses any local folder; `ingest --drive-export-dir <dir>` recurses a Google Drive Takeout export.

## Common workflows

```bash
# Review with full pipeline (review + hybrid pack + redline + find/replace)
./run_all.sh /path/to/nda.docx "Counterparty Name" "Reviewer Name"

# Generate a clause-ready redline draft from a saved review
./nda_review_cli.py generate-redlines --mode v2 \
  --review-json output/reviews/review.json \
  --out output/reviews/clause-ready-redline.md

# Playbook versioning
./nda_review_cli.py playbook-snapshot
./nda_review_cli.py playbook-diff --a output/playbook_versions/A.json --b output/playbook_versions/B.json
./nda_review_cli.py playbook-lock --counterparty "Counterparty Name"

# Pre-step4 quality gate
./nda_review_cli.py quality-gate \
  --redline output/reviews/redline-instructions-*.md \
  --source-text /path/to/nda.txt \
  --out-json output/reviews/quality-gate.json

# Negotiation analysis + validation
./nda_review_cli.py negotiate analyze --state negotiation.json
./nda_review_cli.py negotiate diff --state negotiation.json --out-md round-2.md
./nda_review_cli.py negotiate validate --state negotiation.json   # tamper check
./nda_review_cli.py negotiate simulate \
  --party-a-base /tmp/a --party-b-base /tmp/b \
  --stance-a conservative --stance-b conservative --mode auto
```

## For LLM agents

The full contract is in [AGENTS.md](AGENTS.md). Highlights:

- **Output contract**: structured JSON to stdout on success; `{ok: false, error: {code, message, details?}}` on failure (stderr). Human summaries also on stderr — suppress with `NDA_CLI_QUIET=1`.
- **Exit codes**: `0/2/3/4` for success / invalid input / policy-or-chain-failure / not-found.
- **Discovery**: `nda-review-cli --catalog json` for the full subcommand + flag inventory; `<cmd> --help` for per-command epilogs with concrete examples; `examples` for curated walkthroughs.
- **LLM safety**: `--llm` is opt-in per call, prints destination + asks for consent, supports `ollama` or any OpenAI-compatible endpoint for fully on-prem inference. See [docs/reference/llm-data-flow.md](docs/reference/llm-data-flow.md).

## Common subcommands

```
build-playbook   Build NDA playbook from a contracts corpus
review           Review an NDA against the playbook (add --why for evidence)
draft            Draft a fresh NDA in your house language
negotiate <sub>  Two-party turn-taking negotiation (init/counter/accept/sign-off/finalize/...)
generate-redlines  Generate Word-ready redlines from a review JSON
quickstart       14-question guided setup
tutorial         Interactive primer + sample review
setup            Combined init + ingest + build-playbook
ingest           Ingest historical contracts/playbooks
doctor           Diagnose first-run issues; --check-llm verifies LLM provider
policy-validate  Validate a policy file against the schema
profile-learn    Learn or update a counterparty profile from a saved review
calibrate-scoring  Evaluate a labeled validation set against a scoring profile
sample-nda       Drop the bundled sample NDA into a path of your choice
examples         Show 7 curated walkthroughs
```

Full list via `--help` or `--catalog json`.

## Exit codes

| Code | Meaning |
|---|---|
| `0` | Success |
| `2` | Invalid input |
| `3` | Policy / hash-chain / verification failed (also LLM consent denied) |
| `4` | Not found (missing file, missing profile, missing playbook) |

Full envelope and stable error codes in [docs/reference/exit-codes.md](docs/reference/exit-codes.md).

## License

[MIT](LICENSE) © Baher Al Hakim.

## See also

- [AGENTS.md](AGENTS.md) — agent quickstart (output contract, exit codes, discovery, failure recovery, LLM safety).
- [GETTING_STARTED.md](GETTING_STARTED.md) — scenario-based onboarding walkthrough.
- [docs/setup/](docs/setup/) — LLM provider configuration + integration hooks.
- [docs/reference/](docs/reference/) — concept deep-dives (policy, stance, fatigue, scoring, state file, exit codes, LLM data flow).
- [examples/negotiate-cheatsheet.md](examples/negotiate-cheatsheet.md) — one-page negotiation reference.
- [ARCHITECTURE.md](ARCHITECTURE.md) — components, data flow, determinism guarantees.
- [SECURITY.md](SECURITY.md) — threat model + LLM data-flow disclosure.
- [CHANGELOG.md](CHANGELOG.md) — what landed and when.
