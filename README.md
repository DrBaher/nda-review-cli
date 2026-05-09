# NDA Review CLI

> Review and draft NDAs against your own house policy — deterministic by default, with optional second-pass LLM adjudication via the model of your choice (Anthropic, OpenAI, Ollama, or any OpenAI-compatible endpoint). Local-first, no telemetry, single-file Python.

[![CI](https://github.com/DrBaher/nda-review-cli/actions/workflows/ci.yml/badge.svg)](https://github.com/DrBaher/nda-review-cli/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue.svg)](https://www.python.org/downloads/)
[![Local-first](https://img.shields.io/badge/local--first-yes-brightgreen.svg)](#why-it-exists)

The CLI ingests your past contracts, extracts your negotiation style into a versioned playbook, and applies it as a deterministic, explainable policy to every new NDA — with clause-by-clause findings, severity scoring, and Word-ready redlines.

📚 **[Quick start](#quick-start-3-commands)** · **[60-second demo](examples/demo.md)** · **[Getting Started guide](GETTING_STARTED.md)** · **[Architecture](ARCHITECTURE.md)** · **[Contributing](CONTRIBUTING.md)** · **[Security](SECURITY.md)**

<!--
  To regenerate the asciinema cast:
    asciinema rec --command "./scripts/record-demo.sh" demo.cast
    asciinema upload demo.cast
  Then paste the URL above this comment as `[![asciicast](URL.svg)](URL)`.
-->


## Why it exists

Sending NDAs to a SaaS reviewer means leaking your counterparty list, your fallback positions, and the wording of every contract you touch. This tool runs entirely on your machine, has no telemetry, and is auditable in a single Python file.

## What it does

- **Builds a playbook** from your historical Gmail/Drive corpus (or any folder of contracts).
- **Reviews NDAs** clause-by-clause against that playbook with severity-scored findings and explainability evidence.
- **Drafts NDAs to send out** using your house clause language — mutual or one-way disclosing — straight to `.md` + `.docx`.
- **Negotiates between two parties** turn by turn, each side running their own CLI + policy + optional LLM agent, with a tamper-evident state file passed by any channel (email, Drive, Git).
- **Generates redlines** ready to drop into Word, plus tracked-changes packs and Office Script bridges.
- **Learns counterparty profiles** deterministically so repeat parties get a consistent stance.

Everything runs locally. No model calls, no data leaves the box.

## How it works

```
Past contracts ──► ingest ──► playbook ──► review NDA ──► redline pack
                              ▲                      │
                              └── policy (your rules)│
                                                     ▼
                                       counterparty profile (learns)
```

1. **You curate a policy** (`config/org-policy.json`) — clause keywords, preferred language, red flags, risk weights.
2. **The CLI ingests** your historical contracts and emits a versioned **playbook** that captures your house style.
3. **For each new NDA**, the review engine matches clauses, flags red-flag patterns, scores severity, and emits a decision (approve / escalate / block) with explainability evidence.
4. **Output is deterministic** — same input + same policy = same output, every time. Audit trails and golden tests guarantee it.
5. **Word-ready redlines** flow out via the `step3` → `step4`/`step5` shell pipeline.

For the deep dive — components, file layout, scoring weights, determinism guarantees — see **[ARCHITECTURE.md](ARCHITECTURE.md)**.

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

The pipx install gives you `nda-review-cli` on `$PATH` everywhere; the cloned form is identical apart from the script name (`./nda_review_cli.py` vs `nda-review-cli`). Examples below use the cloned form for portability.

## Quick start (3 commands)

```bash
# 1. Clone and enter the repo
git clone https://github.com/DrBaher/nda-review-cli.git
cd nda-review-cli

# 2. One-shot setup — creates config + profile, auto-discovers any contracts in the repo, builds the playbook
./nda_review_cli.py setup --quick --yes

# 3. Review a sample NDA (bundled fixture) to see the full output
./nda_review_cli.py review --file tests/fixtures/sample_nda.txt --why
```

That's it — `output/nda_playbook.json` and a review JSON/markdown summary are now on disk. New here? See **[GETTING_STARTED.md](GETTING_STARTED.md)** for a guided walkthrough, or run `./nda_review_cli.py tutorial` for an interactive primer.

Want a guided 14-question setup that wires your stance on term length, residual knowledge, trade-secret carve-out, and affiliate disclosure into the clause rules? Run:

```bash
./nda_review_cli.py quickstart
```

It writes a replayable `config/quickstart-answers.json` so you can re-run non-interactively in CI with `--no-prompt --yes --answers-file <path>`.

## Core concepts

| Term | What it is | Where it lives |
|---|---|---|
| **Policy** | Your house rules: clause keywords, preferred language, red flags, risk weights. Edited by humans. | `config/default-policy.json` (seed) → `config/org-policy.json` (your overrides) |
| **Profile** | Per-counterparty memory: their typical positions, what they've conceded, what triggered escalation. Updated automatically with `--learn-profile`. | `profiles/<name>.json` |
| **Playbook** | A snapshot built from your corpus + policy: clause-by-clause guidance the review engine consults. Rebuilt on demand. | `output/nda_playbook.json` (+ `.md`) |

Rule of thumb: **edit the policy, let the profile learn, regenerate the playbook.**

## LLM-augmented review (opt-in)

The deterministic rule engine handles every review on its own. Pass `--llm` to run a second pass through a model of your choice — it (1) votes on each rule finding (`agree` / `soften` / `escalate` / `drop`), (2) adds findings the rules missed, and (3) suggests replacement clause language for high-severity items. Results land in `llm_annotations` in the review JSON; the deterministic findings are never overwritten.

```bash
# Anthropic Claude
NDA_LLM_API_KEY=sk-ant-... ./nda_review_cli.py review --file nda.txt --why \
  --llm anthropic --llm-model claude-sonnet-4-6 --yes-llm-send \
  --out-json output/reviews/with-llm.json --out-md output/reviews/with-llm.md

# OpenAI
NDA_LLM_API_KEY=sk-... ./nda_review_cli.py review --file nda.txt --why \
  --llm openai --llm-model gpt-4o-mini --yes-llm-send

# Local Ollama (Qwen, Llama, etc.) — no key required, fully on-prem
./nda_review_cli.py review --file nda.txt --why \
  --llm ollama --llm-model qwen2.5:14b --yes-llm-send

# Any OpenAI-compatible endpoint (Qwen API, Together, Groq, vLLM, LM Studio...)
./nda_review_cli.py review --file nda.txt --why \
  --llm openai-compatible --llm-base-url https://your-endpoint/v1 \
  --llm-model your-model --yes-llm-send
```

Configure once in `config/llm.json` (gitignored — see `config/llm.json.example` for the schema) and you can omit the flags:

```bash
cp config/llm.json.example config/llm.json
$EDITOR config/llm.json   # set provider, model, api_key
./nda_review_cli.py review --file nda.txt --why --llm --yes-llm-send
```

**Important:**
- Sending NDA text to a third-party provider may breach the NDA you're reviewing. Use `--llm ollama` or a local `openai-compatible` endpoint for fully on-prem inference.
- The CLI prints the destination (provider + base URL + model) and asks for confirmation before sending. Pass `--yes-llm-send` or set `NDA_LLM_NO_CONFIRM=1` to skip the prompt in CI.
- Network I/O is restricted to this code path. Without `--llm`, no contract text leaves the machine. See [SECURITY.md → LLM data flow](SECURITY.md#llm-data-flow-opt-in).

## Negotiating between two parties

When both sides have their own CLI install, they can co-negotiate an NDA without sending text through any third-party service. The protocol is file-based: each party signs one round at a time and passes the state file to the other side via any channel (email, shared Drive, private Git repo). Each side's LLM agent (if enabled) drafts amendments aligned with that side's `config/org-policy.json`; humans always review and sign each round before it goes back.

```bash
# Party A initializes
nda-review-cli negotiate init \
  --template mutual \
  --party-a-name "Acme Inc." --party-a-address "1 Main" \
  --party-b-name "Beta LLC"  --party-b-address "2 Side" \
  --purpose "evaluating a partnership" \
  --effective-date 2026-05-09 \
  --out negotiation.json
# → emits negotiation.json (round 1, signed by A) — send to Party B

# Party B reviews against their own policy (read-only)
nda-review-cli negotiate review --state negotiation.json

# Party B drafts a counter via LLM agent (or supplies --amendments-file for manual)
nda-review-cli negotiate counter --state negotiation.json \
  --as b --agent --llm anthropic --yes-llm-send
# → updates negotiation.json (round 2, signed by B) — send to Party A

# Party A accepts (or counters again)
nda-review-cli negotiate accept --state negotiation.json --as a
# → round 3, signed by A. clause_status now agreed for all touched clauses → status="converged"

# Status check at any point
nda-review-cli negotiate status --state negotiation.json

# Finalize: emit the agreed .md + .docx, and (optionally) hand off to your
# external docx2pdf and sign-CLI tooling via config/integrations.json hooks
nda-review-cli negotiate finalize \
  --state negotiation.json \
  --out-md output/agreed.md \
  --out-docx output/agreed.docx \
  --to-pdf --sign
```

**Negotiation stance** lets you set how aggressively your agent negotiates without per-round eyeballing. Set in `config/org-policy.json` `defaults.negotiation_stance` (asked during `quickstart`) or override per-round with `--stance`:

| Stance | Behaviour |
|---|---|
| `conservative` | Hold firm — counter every clause that materially differs from your preferred language. Reject other-party amendments by default. |
| `middleground` (default) | Compromise on low-severity items. Hold firm on high-severity / red-flag clauses. |
| `compromising` | Accept most amendments unless they trigger a red-flag pattern. Push back only on dealbreakers. |

Stance flows into both modes:
- **`negotiate counter --auto`** — fully deterministic, no LLM. Generates amendments from your policy + stance + the rule engine's red-flag detection.
- **`negotiate counter --agent --llm <provider>`** — stance is injected into the LLM prompt as concrete instructions, alongside your clause-by-clause preferred language.

### Game-theoretic predictions (validated by `negotiate simulate`)

The deterministic `--auto` mode is a multi-issue bargaining game. Each clause has a preferred text per party; each stance defines how many clauses you insist on; **clause priorities** define which ones you concede on. The CLI implements **logrolling** — the classic resolution for multi-issue bargaining where parties trade concessions across issues they value differently.

The convergence rule is the equilibrium condition; the stalemate detector bounds the worst case. We've validated the predicted outcomes empirically (see `tests/test_negotiate_simulate.py`):

| Party A × Party B | Outcome | Rounds | Why |
|---|---|---|---|
| **conservative × conservative** (default priorities) | blocked | ~7 (stalemate) | Symmetric strict preferences → no Nash equilibrium with mutual acceptance → oscillating non-convergence |
| conservative × conservative (**non-overlapping priorities**) | converged or partial-convergence | 3–5 | Logrolling: A concedes its bottom-30% to B, B concedes its bottom-30% to A. Reduces disputes from 3 → 1 in our test fixtures |
| conservative × middleground | converged | 2–3 | M concedes on non-red-flag clauses, A holds firm and wins the contested ones |
| conservative × compromising | converged | 2–3 | C concedes everywhere; only A's red flags are negotiated |
| middleground × middleground | converged | 2 | Both sides only push back on red flags; usually one round resolves them |
| middleground × compromising | converged | 2 | C concedes; M just polices red flags |
| compromising × compromising | converged | 2 | Both sides accept everything that doesn't fire a red flag |

#### How logrolling works in practice

Each party ranks the clauses 1..N during `quickstart` (1 = most important). Stance defines the **size** of your concession zone, priorities define **which clauses are in it**:

| Stance | Concession zone | Insists on |
|---|---|---|
| Conservative | bottom **30%** | top 70% by priority |
| Middleground | bottom **60%** | top 40% by priority |
| Compromising | bottom **85%** | top 15% by priority |

When the agent runs `--auto`, it accepts the current text for any clause in its concession zone (no matter how different from your preferred). It only counters on clauses outside its concession zone, applying the stance's red-flag/diff logic.

**Why two conservatives with different priorities can converge** (and statistically will): with 11 clauses, there are 11! ≈ 40M possible orderings — the chance two real teams have identical priorities is essentially zero. As long as A's bottom-30% covers some of the clauses B insists on (and vice versa), those clauses converge through logrolling. The residual stalemate is bounded to clauses where **both** parties' top priorities overlap; if the overlap is too large, the stalemate detector still trips and the CLI prompts you to change strategy.

Run the simulation yourself:

```bash
nda-review-cli negotiate simulate \
  --party-a-base /path/to/party-a-workspace \
  --party-b-base /path/to/party-b-workspace \
  --stance-a conservative --stance-b conservative \
  --mode auto --max-rounds 12
```

The report includes per-round trajectory (agreed/disputed/proposed counts), winner-per-clause for converged outcomes, and a `block_diagnosis` listing stuck clauses for blocked outcomes.

Run the simulation yourself:

```bash
nda-review-cli negotiate simulate \
  --party-a-base /path/to/party-a-workspace \
  --party-b-base /path/to/party-b-workspace \
  --stance-a conservative --stance-b conservative \
  --mode auto --max-rounds 10
```

The report includes per-round trajectory (agreed/disputed counts), winner-per-clause for converged outcomes, and a `block_diagnosis` listing stuck clauses for blocked outcomes.

**Sign-off before finalize** is a required human checkpoint. Once `status: converged`, both parties run `negotiate sign-off` to review the **key points** — clauses changed from the initial draft, amendments applied (with their source: manual / `auto:<stance>` / `agent:<stance>`), and any red-flag patterns still present in the final text. `negotiate finalize` is blocked until both sign-offs land. This is the gate that lets you trust agent-assisted rounds without losing the human review.

**How it works:**
- **State file** is a single JSON document with all rounds, each round's full text, a SHA-256 hash chain (each round's hash incorporates the previous round's hash → tamper evidence), per-round signatures, and a `signoffs` block once sign-off is complete.
- **The agent** is `review --llm` repurposed: it sees your party's policy + your stance + the latest amendments and proposes which clauses to accept and which to counter. It never signs on your behalf.
- **Convergence** happens when no clause is `disputed` and the latest two rounds were signed by alternating parties.
- **Finalize hooks** (`config/integrations.json`, gitignored) let you delegate PDF conversion and signing to your own tools — for example a `docx2pdf` repo and a `sign-CLI` repo. The CLI passes `{input_docx}`, `{output_pdf}`, `{negotiation_id}`, `{party_a_name}`, `{party_b_name}` placeholders to your configured commands. See [`config/integrations.json.example`](config/integrations.json.example).

**Safety rails:**
- Humans always run `negotiate accept` / `negotiate counter` to advance a round; the LLM agent never signs.
- `--agent` requires `--yes-llm-send` (or `NDA_LLM_NO_CONFIRM=1` env) in non-interactive contexts to prevent accidental contract leaks to a provider.
- The hash chain is verified on every load — tampering between rounds is detected.

## Drafting an NDA to send out

Draft a fresh NDA in your house language. Clause text is pulled directly from `config/org-policy.json` `clause_rules.preferred`, so anything you tuned via `quickstart` (term length, return-vs-destroy, residual-knowledge stance, trade-secret carve-out, affiliate disclosure) flows through automatically.

```bash
# Mutual NDA — both parties may disclose
./nda_review_cli.py draft \
  --template mutual \
  --party-a "Acme Inc." --party-a-address "123 Main St, Vienna, AT" \
  --party-b "Beta LLC"  --party-b-address "10 Market Way, Berlin, DE" \
  --purpose "evaluating a strategic partnership" \
  --out output/drafts/mutual.md \
  --out-docx output/drafts/mutual.docx

# One-way disclosing NDA — you share, the other side does not
./nda_review_cli.py draft \
  --template one-way-out \
  --disclosing-party "Acme Inc." --disclosing-party-address "123 Main St" \
  --receiving-party "Vendor Co"  --receiving-party-address "100 Lake Rd" \
  --purpose "vendor onboarding diligence" \
  --out output/drafts/oneway.md \
  --out-docx output/drafts/oneway.docx \
  --review-after
```

Notes:
- `--out` writes the canonical markdown source; `--out-docx` writes a Word `.docx` (stdlib-only, no `python-docx` needed).
- `--template` defaults to one suggested by your `profile.nda_direction` — `disclosing` → `one-way-out`, anything else → `mutual`.
- `--template-file path/to/your-template.md` lets you bring your own template with `{{placeholders}}`. Missing placeholders fail loudly with exit code 2.
- `--review-after` round-trips the generated draft through `review --why` so you see the same lens applied to your own outgoing language.
- A "starting point, not legal advice" disclaimer header is prepended by default; pass `--no-disclaimer` to omit.

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

- `./nda_review_cli.py quickstart` → 14-question guided setup; answers wire directly into clause rules + red flags. Replayable via `--answers-file`.
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

## Documentation map

| File | When to read it |
|---|---|
| [`README.md`](README.md) | You're here. Overview + command reference. |
| [`GETTING_STARTED.md`](GETTING_STARTED.md) | First-run walkthrough, scenario-based onboarding (solo / in-house / migration / Drive Takeout / SaaS team / drafting / LLM-augmented), troubleshooting. |
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | How the CLI is structured, data flow, where to make changes, determinism guarantees, optional LLM augmentation design. |
| [`CONTRIBUTING.md`](CONTRIBUTING.md) | Dev setup, branch model, testing conventions, network-I/O rules. |
| [`SECURITY.md`](SECURITY.md) | Threat model, LLM data flow disclosure, how to report a vulnerability. |
| [`CHANGELOG.md`](CHANGELOG.md) | Per-release user-facing notes. |
| [`config/default-policy.json`](config/default-policy.json) | Committed seed policy — generic clause rules and red flags. Edit `config/org-policy.json` for your overrides. |
| [`config/scoring-profiles.json`](config/scoring-profiles.json) | Scoring weights + decision thresholds for `balanced` / `strict` / `commercial`. |
| [`config/llm.json.example`](config/llm.json.example) | Schema for `config/llm.json` (gitignored): provider, model, API key, base URL. Used by `review --llm`. |
| [`templates/`](templates/) | Bundled NDA templates used by `draft` (mutual + one-way disclosing). Each uses `{{placeholders}}` filled from CLI args + policy clause text. |

## License

[MIT](LICENSE) © Baher Al Hakim. Use it, fork it, ship it — just keep the copyright notice.

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
