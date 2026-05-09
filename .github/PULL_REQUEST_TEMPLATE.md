<!--
Thanks for contributing! A few quick checks before you submit:
- One PR = one concern. Bug fix + refactor in the same PR makes review hard.
- Run the test suite locally: `python3 -m unittest discover -s tests -p 'test_*.py' -v`
- Update CHANGELOG.md under the [Unreleased] section
-->

## Summary

<!-- 1-3 sentences: what changed and why. The "what" should be skimmable from the diff;
     the "why" is what reviewers need from you here. -->

## Type of change

- [ ] Bug fix (a defect was making something behave incorrectly)
- [ ] Feature (new functionality, additive)
- [ ] Refactor (no functional change; readability / structure)
- [ ] Docs (README / GETTING_STARTED / ARCHITECTURE / etc.)
- [ ] CI / tooling

## Project-principles checklist

- [ ] **Stdlib-only**: no new runtime dependencies introduced
- [ ] **Deterministic by default**: deterministic output paths remain reproducible; any randomness is opt-in
- [ ] **Local-first**: no new always-on network calls; LLM calls remain behind explicit opt-in flags with confirmation
- [ ] **Backward compatible**: existing state files / policies / playbooks still load correctly (or schema bump is documented)

## Testing

- [ ] All existing tests pass (`python3 -m unittest discover -s tests -p 'test_*.py' -v`)
- [ ] New tests added for new behavior (where applicable)
- [ ] Manual smoke: `./nda_review_cli.py tutorial --no-prompt --run-sample` runs cleanly

## Documentation

- [ ] CHANGELOG.md updated under `[Unreleased]`
- [ ] README / GETTING_STARTED / ARCHITECTURE updated where relevant
- [ ] Help text (`--help`) updated for new flags

## Screenshots / output (optional)

<!-- If a CLI behavior changed, paste a snippet of the new output. -->
