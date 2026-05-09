# Changelog

## [0.4.0] - 2026-05-09

- Added `review --why` explainability output with triggered phrases, rule patterns, clause location, and confidence scoring.
- Added `generate-redlines --mode v2` for clause-specific amendment blocks with rationale, severity, and replacement text.
- Added deterministic counterparty profile learning via `review --learn-profile` and `profile-learn`.
- Added `wizard` for guided setup -> ingest -> build -> review flows with non-interactive bypass flags.
- Added configurable scoring profiles and `calibrate-scoring` for labeled validation-set evaluation.
- Added import connector shortcuts for local contracts directories and Google Drive export folders.
- Added `release-helper`, committed scoring profiles, CI matrix expansion, and this changelog.
