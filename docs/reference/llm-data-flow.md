# LLM data flow

What leaves your machine when you pass `--llm`, and what doesn't.

## Without `--llm`

Nothing leaves your machine. `nda-review-cli` is stdlib-only at runtime — no network calls in the deterministic code path.

## With `--llm`

The CLI prepares a prompt and sends it to the configured provider. The prompt includes:

- **The full text of the document being reviewed** (for `review --llm`) or **the full text of the current round of the negotiation** (for `negotiate counter --agent --llm`).
- **Your house policy** (clause rules, red flags, preferred language) — needed for the agent to produce stance-aligned amendments.
- **Your stance + clause priorities** — for `negotiate counter --agent --llm`.
- **The rule engine's findings** — so the LLM can vote on each one.

The CLI does NOT send:

- Your other documents (only the one in `--file <path>`).
- Past negotiations (only the active state file's rounds).
- Other counterparty profiles.
- Anything from `data/raw_strict/` or `knowledge/`.

## Consent prompt

Before any send, the CLI prints:

```
LLM augmentation about to send to:
  provider: anthropic
  base URL: https://api.anthropic.com
  model: claude-sonnet-4-6
  payload: ~3.2 KB

Send? [y/N]
```

Pressing anything other than `y` (or `yes`) aborts with `LLM_DECLINED` (exit `3`). The CLI never silently sends.

`--yes-llm-send` or `NDA_LLM_NO_CONFIRM=1` skips the prompt in CI / batch contexts. Use only after auditing the destination configuration once.

## Configuring an on-prem destination

If your counterparty hasn't cleared third-party AI review (or you're working with a document that legally can't leave your network), point the CLI at a local or internal endpoint:

- [docs/setup/llm-ollama.md](../setup/llm-ollama.md) — local inference via Ollama.
- [docs/setup/llm-openai-compatible.md](../setup/llm-openai-compatible.md) — any OpenAI-compatible endpoint (vLLM, LM Studio, internal proxy).

Both options keep contract text inside your control. The consent prompt still fires (good practice — even for internal endpoints, you're saying "yes, this NDA can be processed by this model").

## Provider data-handling

Each third-party provider has its own data-handling policy:

- [Anthropic](https://www.anthropic.com/legal/commercial-terms) — opt-in for training; zero-retention available on certain plans.
- [OpenAI](https://openai.com/policies/business-terms/) — opt-out for training by default on API plans.
- [Together](https://www.together.ai/privacy), [Groq](https://groq.com/privacy-policy), [Mistral](https://mistral.ai/terms) — each has its own policy.

Read them before sending. The CLI doesn't enforce anything beyond the consent prompt — that's a human responsibility.

## Audit trail

When `--llm` is used:

- The review JSON includes `llm_annotations` with the model name and provider.
- The negotiation state file records `source: "agent:<stance>"` on each amendment.

You can grep for `source: agent` across your negotiation history to find every round where an LLM proposed amendments.

## See also

- [SECURITY.md](../../SECURITY.md) — the full security disclosure.
- [docs/setup/](../setup/) — per-provider configuration.
