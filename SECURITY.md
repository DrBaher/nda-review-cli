# Security Policy

## Threat model

`nda-review-cli` is a local, deterministic tool. It does not send contract text, policies, or profiles over the network. The primary security concerns are therefore:

1. **Data exfiltration** — anything that turns this from a local tool into one that leaks contract content is a critical bug.
2. **Code execution from inputs** — NDAs are untrusted text. Crafted inputs (especially `.docx` or `.pdf` via shell-out extractors) must not lead to code execution or path traversal.
3. **Policy/profile tampering** — the deterministic guarantee depends on the user owning their `config/` and `profiles/` directories. We document this rather than enforce it in code, but we will not silently merge external policy data.

## Supported versions

Only the latest minor release receives security fixes. The CLI is single-file and easy to upgrade — pull the latest `main` or the latest tagged release.

| Version | Supported |
|---|---|
| 0.4.x   | Yes       |
| 0.3.x and older | No (please upgrade) |

## Reporting a vulnerability

Please report security issues **privately**:

- Email: **drbaher@gmail.com** with subject prefix `[nda-review-cli security]`
- Or use GitHub's **"Report a vulnerability"** form on the Security tab if enabled

Please include:

- The version (`git rev-parse HEAD` or release tag)
- Reproduction steps and a minimal sample input if possible
- Expected vs. actual behavior and your assessment of impact

You should get an acknowledgement within **5 business days**. We aim to ship a fix within **30 days** for confirmed vulnerabilities, sooner for actively exploited issues.

Please **do not** open a public GitHub issue for vulnerabilities until a fix is available.

## Disclosure preference

Coordinated disclosure is preferred. We're happy to credit the reporter in the changelog and release notes unless they prefer otherwise.

## Out of scope

- Issues that require an attacker to already have local write access to `config/`, `profiles/`, or the user's NDA inputs.
- Crashes from malformed JSON in the user's own policy files (these are config errors, not vulnerabilities — `policy-validate` is the right tool).
- Findings dependent on third-party tools (`pdftotext`, `textutil`) that the CLI invokes — please report those upstream.
