# Architecture

A short tour of how the CLI is structured, why it's structured that way, and where to look when changing behaviour.

## Design principles

1. **Local-first.** The tool never sends contract text, policies, or profiles over the network. There is no model call in the hot path. Anyone can audit what runs by reading one Python file.
2. **Deterministic.** Same input + same policy = same output, every time. No timestamps in review bodies, no random tie-breakers, no LLM outputs that drift between versions.
3. **Rules-first, not model-first.** Patterns and configs do the work. The corpus only provides signals (frequency counts, example matches) used to seed the playbook.
4. **Single-file CLI.** `nda_review_cli.py` is intentionally one large file with stdlib only. Easier to ship, audit, and hack on.
5. **Friendly defaults, opt-in complexity.** `setup --quick --yes` produces something useful in seconds. Every flag has a sensible default.

## High-level data flow

```
                ┌────────────────────┐
                │  Past contracts    │   (Gmail/Drive corpus, contracts dir,
                │  + policy seed     │    or Drive Takeout export)
                └─────────┬──────────┘
                          │  ingest / build-playbook
                          ▼
   ┌────────────────────────────────────────────────────────┐
   │   PLAYBOOK   output/nda_playbook.json + .md            │
   │   (clause rules, signal frequencies, preferred lang)   │
   └─────────┬──────────────────────────────────────────────┘
             │  review
             ▼
   ┌─────────────────────────────────────────────────────────┐
   │   REVIEW     output/reviews/<name>.json + .md           │
   │   (clause-by-clause findings, severity, evidence)       │
   └────┬───────┬───────────────┬──────────────────────────┘
        │       │               │
        │       │               └─►  generate-redlines (clause-ready amendments)
        │       │
        │       └─► profile-learn / --learn-profile
        │            updates profiles/<counterparty>.json
        │
        ▼
   hybrid_review.sh ─► step3_redline_pack.sh ─► step4_prepare_tracked_redline.sh
                                          └──► step5_find_replace_pack.sh
                                               (Word-ready outputs)
```

Three ground-truth artefacts, three operations:

| Artefact | What it is | Edit cadence |
|---|---|---|
| **Policy** (`config/*.json`) | House rules: clause keywords, preferred language, red flags, risk weights | Manual, rare |
| **Profile** (`profiles/<name>.json`) | Per-counterparty memory | Auto (`--learn-profile`) or manual |
| **Playbook** (`output/nda_playbook.json`) | Compiled artefact used by review | Regenerate on demand |

## Components

### `nda_review_cli.py` — the single-file CLI

All subcommands live here. Roughly grouped:

| Group | Commands | Purpose |
|---|---|---|
| Onboarding | `init`, `setup`, `wizard`, `quickstart`, `tutorial`, `doctor` | First-run config + sanity checks |
| Knowledge | `ingest`, `build-playbook` | Turn raw contracts into a playbook |
| Review | `review`, `profile-learn`, `calibrate-scoring` | Score NDAs against the playbook |
| Authoring | `draft` | Generate outgoing NDAs (mutual / one-way-out) in `.md` + `.docx` |
| Negotiation | `negotiate init/review/counter/accept/status/finalize` | Two-party turn-taking negotiation with optional LLM agent and external `docx2pdf` + `sign-CLI` hooks |
| Output | `generate-redlines`, `generate-office-script`, `quality-gate` | Produce Word-ready amendments |
| Versioning | `playbook-snapshot`, `playbook-diff`, `playbook-lock` | Track playbook changes |
| Meta | `policy-validate`, `release-helper`, `create-manifest` | Schema/version/release plumbing |

### `rule_engine.py` — deterministic clause matching

Tiny module exporting `clause_hit()` and `red_flag_hits()`. Holds the regex pattern catalogue per clause. Imported by `nda_review_cli.py`. Kept separate so it can be unit-tested in isolation (`tests/test_rule_engine.py`).

### Shell orchestration scripts

The `.sh` scripts wire the Python building blocks into common workflows. They exist because legal users often live in shell, not Python.

| Script | Wraps | Purpose |
|---|---|---|
| `review_nda.sh` | `nda_review_cli.py review` | Sensible defaults for one-shot review |
| `hybrid_review.sh` | review + post-processing | Produces a "hybrid approval pack" markdown |
| `step3_redline_pack.sh` | `generate_redline_instructions.py` | Step 3: clause-numbered redline instruction set |
| `step4_prepare_tracked_redline.sh` | step3 output + Word | Step 4: builds `.docx` tracked-changes runbook |
| `step5_find_replace_pack.sh` | step3 output | Step 5: anchor-safe find/replace pack |
| `run_all.sh` | all of the above | End-to-end pipeline from `.txt`/`.docx` to redline pack |

### `step2_pass2_review.py` — interactive triage

Standalone Python tool for the human-in-the-loop pass-2 step. Reads a hybrid approval pack and lets a reviewer confirm/downgrade/drop each finding before redlines are generated. Supports `--mode interactive`, `--mode defaults`, and `--decisions-json`.

### `generate_redline_instructions.py` — pack → instruction set

Parses a hybrid approval pack and produces clause-numbered redline instructions. Invoked by `step3_redline_pack.sh`.

### `config/`

- `default-policy.json` — committed seed policy (generic).
- `scoring-profiles.json` — committed scoring profile presets (`balanced`, `strict`, `commercial`).
- `org-policy.json` — gitignored. User overrides live here.

### `templates/`

- `mutual_nda.md` — bundled mutual NDA template used by `draft --template mutual`.
- `one_way_out_nda.md` — bundled one-way disclosing NDA template used by `draft --template one-way-out`.

Both templates use `{{placeholders}}`. Clause-text placeholders (`{{clause_term_and_survival}}`, `{{clause_residuals}}`, etc.) pull straight from `config/org-policy.json` `clause_rules[*].preferred`, so any change made via `quickstart` or hand-edit of policy flows into drafts without changing template files.

### `tests/`

| Test | What it covers |
|---|---|
| `test_rule_engine.py` | Pattern catalogue determinism |
| `test_review_golden.py` | Locked review output for a fixture NDA — fail loudly if anything drifts |
| `test_review_explainability.py` | `--why` evidence shape and profile-learn determinism |
| `test_onboarding_ingest.py` | `init` + `ingest` non-interactive flow |
| `test_onboarding_e2e_smoke.py` | Full `setup --quick → build → review` happy path |
| `test_wizard_flow.py` | End-to-end wizard run with autodiscovered files |
| `test_manifest_cli.py` | Audit manifest output shape |

## Review pipeline (deep dive)

```
NDA text  ─►  paragraph/sentence segmentation
            ─►  clause classification (rule_engine.clause_hit)
            ─►  red-flag detection (rule_engine.red_flag_hits)
            ─►  severity scoring (scoring profile weights)
            ─►  decision (approve / escalate / block) per thresholds
            ─►  optional: explainability evidence (--why)
            ─►  optional: profile update (--learn-profile)
            ─►  JSON + Markdown output
```

The scoring profile decides:

- **Risk weights** per category (legal/commercial/operational)
- **Severity weights** for high/low findings
- **Decision thresholds** for `approve_max` and `escalate_max`

Tweaking these is the cleanest way to retune output without rewriting rules.

## Where to make common changes

| You want to... | Edit |
|---|---|
| Change clause keywords / red flags | `config/default-policy.json` (or `org-policy.json` locally) |
| Add a new clause type | `default-policy.json` + extend `RED_FLAG_PATTERNS` in `rule_engine.py` |
| Tweak severity / decision thresholds | `config/scoring-profiles.json` |
| Change review output structure | `cmd_review` in `nda_review_cli.py` |
| Change ingest discovery roots | `_build_ingest_roots` and `discover_ingest_files` |
| Change drafted NDA boilerplate | `templates/mutual_nda.md` or `templates/one_way_out_nda.md` |
| Change drafted clause language | The `preferred` field of the clause in `config/org-policy.json` |
| Add a new subcommand | Define `cmd_<name>`, register a parser in `main()` |

## Determinism guarantees

The CLI promises (for the deterministic rule-engine review, i.e. without `--llm`):

- **No clocks in review output.** Any timestamp in a review file lives in a clearly labelled `reviewed_at` field at the top level, never inside a finding body.
- **Stable iteration order.** All dicts that flow into output are sorted or constructed in a fixed order.
- **No randomness.** Nothing in the deterministic review pipeline calls `random` or makes a network request.

If you find a determinism bug in the rule-engine path — same input producing different output across runs — that's a high-priority issue. Please open one with reproducer.

## Optional LLM augmentation

`--llm` on `review` adds a second pass via a user-configured provider (Anthropic, OpenAI, Ollama, or any OpenAI-compatible endpoint). The HTTP transport is `urllib.request` from the stdlib — no `anthropic` or `openai` SDK is imported.

Architectural rules:

- **Opt-in only.** Without `--llm`, the network is never touched.
- **Side-by-side output.** LLM output lands under `result["llm_annotations"]`. The deterministic `result["findings"]` are never modified.
- **Confirmation gate.** Before any send, the CLI prints destination details and waits for Enter (or `--yes-llm-send` / `NDA_LLM_NO_CONFIRM=1`). In a non-interactive context without explicit consent, the call is refused.
- **Provider abstraction.** A single `llm_call(cfg, system, user)` dispatches on `cfg["provider"]` to either `llm_call_anthropic` or `llm_call_openai_compatible`. Adding a new provider means adding one function and an entry to `LLM_PROVIDER_PRESETS`.
- **Defensive parsing.** `_parse_llm_review_response` tolerates code fences and surrounding prose; on failure it returns a `_parse_error` field instead of raising, and preserves the raw text for debugging.

## Non-goals

- Replacing a lawyer's judgement. The CLI flags issues and proposes language; humans decide.
- Multi-tenant SaaS. Single-user, single-machine, by design.
- Cloud sync of policies/profiles. Use git or your own backup story.
- Built-in legal-database lookups, e-signature integration, or matter-management features. Out of scope.
- Always-on LLM behaviour. The model only runs when you explicitly pass `--llm` and confirm the send. There is no implicit "AI mode".
