# Changelog

## [Unreleased]

- Added `quickstart` subcommand: 14-question guided setup that wires answers (NDA term length, return-vs-destroy preference, residual-knowledge stance, trade-secret indefinite carve-out, affiliate-disclosure scope) into clause-rule `preferred` text and `red_flags` lists. Writes a replayable `config/quickstart-answers.json`.
- Added `tutorial` subcommand: interactive primer that explains policy/profile/playbook and runs a sandboxed sample review.
- Added human-readable summaries to stderr for `init`, `setup`, `ingest`, and `doctor` (stdout JSON unchanged). Suppressible via `NDA_CLI_QUIET=1`.
- Added top-level `LICENSE` (MIT), `CONTRIBUTING.md`, `SECURITY.md`, `ARCHITECTURE.md`, and a tiered onboarding doc (`GETTING_STARTED.md`).
- Expanded CI matrix to Python 3.9–3.12 with pip caching, added a tutorial smoke job, and added a release-on-tag workflow.

## [0.4.0] - 2026-05-09

- Added `review --why` explainability output with triggered phrases, rule patterns, clause location, and confidence scoring.
- Added `generate-redlines --mode v2` for clause-specific amendment blocks with rationale, severity, and replacement text.
- Added deterministic counterparty profile learning via `review --learn-profile` and `profile-learn`.
- Added `wizard` for guided setup -> ingest -> build -> review flows with non-interactive bypass flags.
- Added configurable scoring profiles and `calibrate-scoring` for labeled validation-set evaluation.
- Added import connector shortcuts for local contracts directories and Google Drive export folders.
- Added `release-helper`, committed scoring profiles, CI matrix expansion, and this changelog.
