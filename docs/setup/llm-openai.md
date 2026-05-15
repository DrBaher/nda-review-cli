# OpenAI

For `review --llm openai` and `negotiate counter --agent --llm openai`.

## Configure

```bash
export NDA_LLM_API_KEY=sk-...
```

Or `config/llm.json`:

```json
{
  "provider": "openai",
  "model": "gpt-4o-mini",
  "api_key": "sk-...",
  "base_url": "https://api.openai.com/v1"
}
```

## Verify

```bash
nda-review-cli doctor --check-llm
```

## Use

```bash
nda-review-cli review --file contract.docx --why \
  --llm openai --llm-model gpt-4o-mini --yes-llm-send
```

## NDA-leak warning

Same caveat as Anthropic — read OpenAI's data-handling policy. For on-prem inference, use [llm-ollama.md](llm-ollama.md) or [llm-openai-compatible.md](llm-openai-compatible.md) with a self-hosted endpoint.
