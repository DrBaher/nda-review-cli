# Security Policy

## Threat model

`nda-review-cli` is a local, deterministic tool by default. The deterministic rule-engine review never sends contract text, policies, or profiles over the network. An optional second-pass LLM review (`--llm`, opt-in) does call out to the provider you configure — see [LLM data flow](#llm-data-flow-opt-in) below.

The primary security concerns are:

1. **Data exfiltration** — anything that turns the *deterministic* path into one that leaks contract content is a critical bug. The LLM path is opt-in and gated behind explicit flags + a per-call confirmation prompt.
2. **Code execution from inputs** — NDAs are untrusted text. Crafted inputs (especially `.docx` or `.pdf` via shell-out extractors) must not lead to code execution or path traversal.
3. **Policy/profile tampering** — the deterministic guarantee depends on the user owning their `config/` and `profiles/` directories. We document this rather than enforce it in code, but we will not silently merge external policy data.
4. **API key leakage** — `config/llm.json` is gitignored; tests should never exercise real network calls; the CLI does not log the API key to stdout/stderr.

## LLM data flow (opt-in)

The deterministic review pipeline never makes network calls. The `--llm` flag on `review` is the only code path that does. When set, the CLI:

1. Loads provider, model, base URL, and API key from `config/llm.json` (gitignored), then env vars (`NDA_LLM_PROVIDER`, `NDA_LLM_MODEL`, `NDA_LLM_BASE_URL`, `NDA_LLM_API_KEY`), then the CLI flags (`--llm`, `--llm-model`, `--llm-base-url`).
2. Prints a confirmation showing **provider + base URL + model** and waits for Enter, unless `--yes-llm-send` is passed or `NDA_LLM_NO_CONFIRM=1` is set. In a non-interactive context (e.g. CI) without explicit consent, the call is refused.
3. Sends a single HTTPS POST containing the system prompt, the NDA text (truncated to 50,000 characters), and a JSON summary of the deterministic findings. No counterparty profiles, no policies, no API keys belonging to anyone but you.
4. Stores the response under `llm_annotations` in the review JSON, alongside the deterministic findings — the rule-engine output is never modified by the LLM.

**Implications you should know:**

- Choosing `--llm anthropic` sends NDA text to Anthropic. Choosing `--llm openai` sends to OpenAI. Choosing `--llm ollama` (local) sends to your local Ollama server. Choosing `--llm openai-compatible` with a custom base URL sends to whatever endpoint you point at. The CLI shows the destination before sending.
- If the NDA you're reviewing is itself confidential to a third party, sending it to a third-party LLM provider may breach that NDA. Use the `ollama` or local `openai-compatible` presets (e.g. vLLM, LM Studio) for fully on-prem inference.
- API keys live in `config/llm.json` (gitignored) or env vars. They are never written to stdout, not echoed in prompts, not included in review output.

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
