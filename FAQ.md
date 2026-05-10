# FAQ

Common questions about `nda-review-cli`.

## What is this, in one sentence?

A local-first CLI for reviewing, drafting, and *negotiating* NDAs against your own house policy — deterministic by default, with optional LLM-augmented adjudication via the model of your choice. Nothing leaves your machine unless you explicitly opt in.

## Is this legal advice?

**No.** This is a tool. Output is a starting point, not a final answer. Have qualified counsel review anything before signing. Every drafted NDA includes a "starting point, not legal advice" disclaimer; the negotiate flow has a mandatory human sign-off step before finalize.

## How is this different from [LegalGPT / Ironclad / Lawvu / etc.]?

Three things that I haven't seen combined in one tool:

1. **Local-first by default.** The deterministic rule-engine review, draft, and negotiate flows make zero network calls. LLM calls are opt-in and clearly marked. Counterparty data stays on your machine.
2. **Rules-first, model-second.** A deterministic rule engine + your house policy is the primary mechanism. The LLM is opt-in adjudication, not a black box doing all the work. Same review run twice gives the same answer.
3. **Two-party negotiation.** Most tools review what *they* sent you. This one also drafts what *you* send out, and runs a structured back-and-forth between two CLI installs (each with their own policy + LLM agent) until convergence.

## Does it work without an LLM?

Yes — fully. The deterministic `--auto` mode of `negotiate counter`, plus rule-engine review, plus draft, plus the entire negotiate state machine all work with zero LLM calls. The `--agent` flag adds an opt-in LLM second pass.

## Which LLM providers are supported?

Two adapters cover everything in practice:

- **Anthropic** (native `/v1/messages` integration)
- **OpenAI-compatible** (any endpoint speaking the OpenAI Chat Completions API)

The OpenAI-compatible adapter has presets for OpenAI itself, Ollama (`http://localhost:11434/v1`), and a generic `openai-compatible` mode where you supply your own base URL — works with Together, Groq, OpenRouter, vLLM, LM Studio, Qwen-DashScope, and anything else that speaks the protocol. Configure via `config/llm.json` (see `config/llm.json.example`) or `NDA_LLM_*` env vars.

## How do I keep contract text private?

- **Default mode.** Nothing leaves the box. Don't pass `--llm` and you're guaranteed local-only.
- **LLM mode with on-prem inference.** Use `--llm ollama` (default base URL `http://localhost:11434/v1`) or `--llm openai-compatible --llm-base-url http://localhost:8000/v1` pointed at your local vLLM/LM Studio. NDA text never leaves your network.
- **LLM mode with cloud providers.** Anthropic / OpenAI / etc. — the CLI prints the destination + waits for confirmation before sending. See `SECURITY.md → LLM data flow` for the exact list of what's sent.

## Why a single 5000-line Python file?

Deliberate. See `CONTRIBUTING.md → Why a single file?` for the full rationale. Short version: it's auditable end-to-end, has zero install complexity, runs anywhere with stdlib Python, and makes "what does this tool actually do?" answerable by reading one file.

## Can I extend this to my industry / clause set?

Yes. Three layers of customization:

1. **Edit `config/org-policy.json`** to add/modify clause keywords, preferred language, red flags, or risk weights. Quickstart-augmented fields (term length, residual stance, etc.) are also editable here.
2. **Add new clause types** by extending the `clause_rules` dict in `config/default-policy.json` and the `RED_FLAG_PATTERNS` table in `rule_engine.py`.
3. **Bring your own templates** for `draft` via `--template-file path/to/your-template.md` with `{{placeholders}}`.

## Can I use this for MSAs / DPAs / employment agreements?

Not directly out of the box — the bundled templates and clause rules are NDA-specific. The architecture (policy + rule engine + draft templates + negotiate flow) is generic, though, and could be retargeted by replacing the templates and clause rules. If you want to fork for an adjacent contract type, the customization path above is the entry point.

## What happens if both parties have different `org-policy.json` files?

That's exactly the negotiate flow: each party uses their own `config/org-policy.json` to drive their agent. Stance + clause priorities + non-negotiable clauses are all per-party. The state file bouncing between them carries amendments, not policies — your policy never leaves your workspace.

## What's the deal with priorities and stance?

Stance defines *how many* clauses your agent insists on; priorities define *which* ones. Conservative insists on top 70%, middleground on top 40%, compromising on top 15% — by your own priority ranking. With 11 clauses there are 11! ≈ 40M possible orderings, so two real teams essentially never have identical priority lists, which means logrolling resolves most cross-party deadlocks. See `ARCHITECTURE.md → Game-theoretic foundations` for the formal model.

## What's "fatigue concession"?

The deadlock-breaker for the rare case where two stance-rigid parties have overlapping priorities. After a clause has been amended back-and-forth `max_clause_bounces` times (default 4), the next round's proposer is deterministically forced to concede that clause. Tagged `auto:<stance>+fatigue` in the audit trail and surfaced in the sign-off step for human review. Set `max_clause_bounces: 0` in your policy to disable it (negotiation will block instead — useful when stalemates need human escalation).

## What if the LLM agent does something dumb?

`negotiate counter --dry-run` shows you the agent's proposed amendments without writing them to the state file. Always preview before committing. The deterministic rule path (`--auto`) is also available if you want full reproducibility.

If the LLM produces malformed JSON, the defensive parser captures `_parse_error` + the raw text in the round, applies no amendments, and lets the negotiation continue — you can hand-edit if needed.

## How do I integrate with our existing PDF / signature tooling?

`negotiate finalize --to-pdf --sign` reads `config/integrations.json` (gitignored, see `config/integrations.json.example`) and shells out to user-configured commands. Placeholders like `{input_docx}`, `{output_pdf}`, `{negotiation_id}`, `{party_a_name}`, `{party_b_name}` are substituted. Fallback configs for LibreOffice headless and Pandoc are in the example file.

## Does it support multi-party negotiations (>2 parties)?

Not currently. The protocol assumes exactly two parties. Three-way negotiations would be a meaningful design extension — open an issue if you have a concrete use case.

## Where do I report a security issue?

`SECURITY.md` covers the disclosure policy. Use GitHub's "Report a vulnerability" form (Security tab → Report a vulnerability) for private disclosure rather than a public issue.

## How do I uninstall?

If you `pipx install`-ed: `pipx uninstall nda-review-cli`. If you cloned the repo: `rm -rf nda-review-cli`. Your local `config/`, `profiles/`, `output/`, and any negotiation state files live in your workspace and are deleted with the directory — no system-wide artifacts.
