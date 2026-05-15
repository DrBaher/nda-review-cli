# OpenAI-compatible endpoints

For any OpenAI-API-compatible service: Qwen API, Together, Groq, Mistral, vLLM, LM Studio, internal company proxies, etc.

## Configure

Pass `--llm-base-url` directly:

```bash
nda-review-cli review --file contract.docx --why \
  --llm openai-compatible \
  --llm-base-url https://your-endpoint.example.com/v1 \
  --llm-model your-model \
  --yes-llm-send
```

Or `config/llm.json`:

```json
{
  "provider": "openai-compatible",
  "model": "Qwen2.5-72B-Instruct",
  "base_url": "https://api.together.xyz/v1",
  "api_key": "tk-..."
}
```

## Verify

```bash
nda-review-cli doctor --check-llm
```

## Common providers + base URLs

| Provider | Base URL |
|---|---|
| Together AI | `https://api.together.xyz/v1` |
| Groq | `https://api.groq.com/openai/v1` |
| Qwen API | `https://dashscope.aliyuncs.com/compatible-mode/v1` |
| Mistral | `https://api.mistral.ai/v1` |
| vLLM (self-hosted) | `http://your-vllm:8000/v1` |
| LM Studio (local) | `http://localhost:1234/v1` |

For Ollama specifically, prefer the dedicated [llm-ollama.md](llm-ollama.md) setup.

## On-prem inference

This is the path to use when you have an internal LLM endpoint and need to keep NDA text inside your network. Point `--llm-base-url` at the internal endpoint and the CLI will use it like any OpenAI-compatible API. No data leaves your control.
