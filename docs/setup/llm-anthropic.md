# Anthropic Claude

For `review --llm anthropic` and `negotiate counter --agent --llm anthropic`.

## Configure

Either set an env var:

```bash
export NDA_LLM_API_KEY=sk-ant-...
```

Or write `config/llm.json` (gitignored — see `config/llm.json.example` for the schema):

```json
{
  "provider": "anthropic",
  "model": "claude-sonnet-4-6",
  "api_key": "sk-ant-...",
  "base_url": "https://api.anthropic.com"
}
```

## Verify

```bash
nda-review-cli doctor --check-llm
```

Sends a minimal 1-token round-trip to confirm reachability, model name, and auth. Exits `0` if everything is OK, `3` on auth/reachability failure.

## Use

```bash
nda-review-cli review --file contract.docx --why \
  --llm anthropic --llm-model claude-sonnet-4-6 --yes-llm-send \
  --out-json output/reviews/with-llm.json
```

Or, with `config/llm.json` populated:

```bash
nda-review-cli review --file contract.docx --why --llm --yes-llm-send
```

## NDA-leak warning

Sending NDA text to Anthropic means the text passes through their infrastructure. Read their data-handling policy and confirm with your counterparty before using on confidential material. For NDAs that can't legally leave your network, use [llm-ollama.md](llm-ollama.md) instead.

The CLI prints the destination (provider + model + base URL) and asks for confirmation before sending. Pass `--yes-llm-send` or set `NDA_LLM_NO_CONFIRM=1` to skip the prompt in CI.
