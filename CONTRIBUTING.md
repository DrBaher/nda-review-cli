# Contributing

Thanks for your interest in `nda-review-cli`. This project is currently maintained by one person (Baher Al Hakim), but contributions are welcome — bug reports, fix PRs, and small focused features are all appreciated.

## TL;DR

```bash
git clone https://github.com/DrBaher/nda-review-cli.git
cd nda-review-cli
python3 -m unittest discover -s tests -p 'test_*.py' -v
```

If tests pass on a fresh clone, you're set up.

## Ground rules

- **Keep changes focused.** One PR = one concern. A bug fix shouldn't ship with a refactor.
- **Don't introduce new runtime dependencies.** The CLI is intentionally a single-file script with stdlib only. The optional LLM adapters (`--llm`) use `urllib.request` directly — no `anthropic` or `openai` SDK. If you genuinely need a third-party package, raise an issue first.
- **Deterministic by default.** The rule-engine review must remain reproducible — no clocks, no randomness, no model calls. The `--llm` second pass is opt-in and its output is stored separately under `llm_annotations`; it must never modify the deterministic findings.
- **No silent network calls.** Network I/O is allowed only behind an explicit `--llm` (or future opt-in) flag, and must show the destination + wait for confirmation unless the user passes `--yes-llm-send` / sets `NDA_LLM_NO_CONFIRM=1`. Telemetry of any kind is not allowed.

## Development workflow

1. **Fork or branch.** External contributors fork; the maintainer uses feature branches.
2. **Branch naming.** `feat/<short>`, `fix/<short>`, `docs/<short>`, or `chore/<short>`.
3. **Run tests early.** `python3 -m unittest discover -s tests -p 'test_*.py' -v` (also runs in CI on Linux + macOS, Python 3.9–3.12).
4. **Compile-check.** `python3 -m py_compile nda_review_cli.py step2_pass2_review.py generate_redline_instructions.py rule_engine.py`
5. **Smoke-test the tutorial.** `./nda_review_cli.py tutorial --no-prompt --run-sample --base /tmp/nda-tut-smoke` should exit clean.
6. **Update CHANGELOG.md.** Add a bullet under the next version's section. Keep it user-facing.
7. **Open a PR.** Target `main`. Reference any related issue.

## Commit messages

Imperative mood, short subject, optional body. Existing history follows this style:

```
feat(review): add --learn-profile for deterministic counterparty memory
fix(ingest): handle empty Drive export folders without raising
docs: clarify policy/profile/playbook distinction in README
chore(ci): expand matrix to Python 3.12
```

No strict prefix taxonomy is required, but `feat`, `fix`, `docs`, `chore`, `refactor`, and `test` cover most cases.

## Code style

- Python 3.9+ stdlib only.
- Standard `python3 -m py_compile` should pass.
- Match surrounding style. The codebase prefers small focused functions over heavy abstraction.
- No unnecessary comments — let identifiers tell the story.

## Testing additions

- Place new tests under `tests/` named `test_<area>.py`.
- Reuse fixtures from `tests/fixtures/` rather than embedding contract text inline.
- For onboarding/CLI changes, add a smoke test that calls the script via `subprocess.check_call` (mirroring `tests/test_onboarding_e2e_smoke.py`).
- For policy/playbook changes, update `tests/fixtures/expected_review_golden.json` deliberately — golden test failures should always be inspected.

## Working with policy and profile changes

- **`config/default-policy.json`** is the committed seed. Change it only when the change applies generically.
- **`config/org-policy.json`** is gitignored — never commit a contributor's local overrides.
- **`profiles/<name>.json`** is generally local-only too. If you add a fixture profile for a test, put it under `tests/fixtures/`.

## Reporting bugs / asking questions

Open a GitHub issue with:

- The command you ran
- Expected vs. actual output
- Output of `./nda_review_cli.py doctor` (redact paths if sensitive)
- Python and OS version

## Releases

The maintainer cuts releases:

```bash
./nda_review_cli.py release-helper --version <semver> --out output/release-notes-<semver>.md
git tag v<semver> -m "v<semver>"
git push origin v<semver>
```

CI handles the rest if a release-on-tag workflow is configured.

## Code of conduct

Be civil, focus on the work, assume good faith. If something feels off, email the maintainer (see `SECURITY.md` for contact).
