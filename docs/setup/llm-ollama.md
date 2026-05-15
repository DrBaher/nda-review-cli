# Ollama (local, on-prem)

For NDAs that can't legally leave your network. No API key required, no third-party calls.

## Install Ollama

[ollama.com/download](https://ollama.com/download) or `brew install ollama` on macOS.

Pull a model:

```bash
ollama pull qwen2.5:14b      # or llama3.1:8b, mistral-nemo, etc.
ollama serve                  # if not already running
```

## Configure

No API key needed. Either pass flags directly:

```bash
nda-review-cli review --file contract.docx --why \
  --llm ollama --llm-model qwen2.5:14b --yes-llm-send
```

Or `config/llm.json`:

```json
{
  "provider": "ollama",
  "model": "qwen2.5:14b",
  "base_url": "http://localhost:11434"
}
```

## Verify

```bash
nda-review-cli doctor --check-llm
```

## Recommended models

For NDA review specifically (legalese, long context, structured output):

| Model | Notes |
|---|---|
| `qwen2.5:14b` | Strong on legal/structured text. Good default. |
| `llama3.1:8b` | Faster, slightly weaker on legalese. |
| `mistral-nemo:12b` | Solid general model. |
| `qwen2.5:32b` | Heavier — better quality if you can spare the GPU/RAM. |

## When to use

- NDAs your counterparty hasn't cleared for third-party AI review.
- High-volume internal review where API costs become prohibitive.
- Air-gapped environments.

If you have a hosted internal LLM endpoint (vLLM, LM Studio, an internal proxy), see [llm-openai-compatible.md](llm-openai-compatible.md) instead.
