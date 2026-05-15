# Setup

Provider + integration setup. You only need to read the ones you'll use.

| File | Use when |
|---|---|
| [llm-anthropic.md](llm-anthropic.md) | Wiring up Anthropic Claude for `review --llm`. |
| [llm-openai.md](llm-openai.md) | Wiring up OpenAI for `review --llm`. |
| [llm-ollama.md](llm-ollama.md) | Local on-prem LLM via Ollama — for NDAs you can't legally send to a third-party model. |
| [llm-openai-compatible.md](llm-openai-compatible.md) | Any OpenAI-compatible endpoint (Qwen, Together, Groq, vLLM, LM Studio, internal proxy). |
| [integrations.md](integrations.md) | `config/integrations.json` hooks for handing off to `docx2pdf-cli` and `sign-cli`. |

Quickest first-run path: `nda-review-cli setup --quick --yes` auto-discovers contracts in the repo, builds a playbook, and writes defaults. Then `nda-review-cli doctor` (add `--check-llm` after configuring an LLM provider) confirms everything is wired up.
