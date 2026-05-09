# 60-second demo

A reproducible walkthrough of the most-used commands. Each block shows the command and the kind of output you should see. To turn this into an animated cast, run [`scripts/record-demo.sh`](../scripts/record-demo.sh) with [asciinema](https://asciinema.org/) installed.

## 0. Install

```bash
# Clone & run (no install needed)
git clone https://github.com/DrBaher/nda-review-cli.git
cd nda-review-cli
./nda_review_cli.py --version
# → nda-review-cli 0.5.0

# Or pipx-install for system-wide use
pipx install .
nda-review-cli --version
```

## 1. First-time hint

```bash
$ ./nda_review_cli.py
NDA Review CLI — local-first NDA review and drafting.

First time? Try one of:
  ./nda_review_cli.py tutorial            # interactive primer + sample review
  ./nda_review_cli.py quickstart          # 14-question guided setup
  ./nda_review_cli.py setup --quick --yes # zero-friction defaults

Common commands:
  review --file <nda>                     # score an NDA against your playbook
  draft --template mutual ...             # generate an outgoing NDA
  doctor                                  # diagnose first-run readiness
```

## 2. Quickstart (14 guided questions, replayable)

```bash
$ ./nda_review_cli.py quickstart
  (1/14) Organization name — appears in playbook + profile metadata.
  Org name [Your Org]: Acme Corp
  (2/14) Org type — picks template clause_preferences (saas/healthcare/enterprise) or annotation only (other).
  Org type (saas/healthcare/enterprise/other) [other]: saas
  ...
  (14/14) Past contracts to ingest now? Enter a directory path or leave blank to skip. We can also run a sample review at the end.
  Contracts dir (blank to skip) []:
  Run a sample review on the bundled NDA fixture? [Y/n]: y

  ━━ Summary ━━
  Org name:                Acme Corp
  Org type:                saas
  Risk posture:            balanced
  ...

  Apply this configuration? [Y/n]: y
{"ok": true, "org_policy": "config/org-policy.json", ...}
```

`config/quickstart-answers.json` is written so you can replay non-interactively:

```bash
$ ./nda_review_cli.py quickstart --no-prompt --yes --answers-file config/quickstart-answers.json
```

## 3. Review an NDA with explainability

```bash
$ ./nda_review_cli.py review --file tests/fixtures/sample_nda.txt --why \
    --out-md output/reviews/sample.md

{
  "decision": "escalate",
  "risk_score": 7.4,
  "findings": [
    {
      "clause": "term_and_survival",
      "severity": "high",
      "concern": "Indefinite survival without trade-secret scoping.",
      "evidence": {
        "triggered_phrases": ["survive indefinitely"],
        "rule_patterns": ["indefinite", "survival"],
        "confidence_score": 0.92
      },
      ...
    }
  ]
}
```

## 4. Add a second-pass LLM (opt-in)

Local Ollama (no cloud, no API key):

```bash
$ ./nda_review_cli.py review --file tests/fixtures/sample_nda.txt --why \
    --llm ollama --llm-model qwen2.5:14b --yes-llm-send \
    --out-md output/reviews/with-llm.md

  About to send NDA text to provider=ollama base_url=http://localhost:11434/v1 model=qwen2.5:14b.
  Press Enter to continue, or Ctrl-C to abort.
```

The review JSON gains an `llm_annotations` block with three things: votes on rule findings (`agree` / `soften` / `escalate` / `drop`), additional findings the rules missed, and suggested replacement language for high-severity items. Deterministic findings are never modified.

## 5. Draft an outgoing NDA

```bash
$ ./nda_review_cli.py draft \
    --template mutual \
    --party-a "Acme Inc." --party-a-address "123 Main St, Vienna, AT" \
    --party-b "Beta LLC"  --party-b-address "10 Market Way, Berlin, DE" \
    --purpose "evaluating a strategic partnership" \
    --out output/drafts/mutual.md \
    --out-docx output/drafts/mutual.docx \
    --review-after

{
  "ok": true,
  "template": "mutual",
  "out_md": "output/drafts/mutual.md",
  "out_docx": "output/drafts/mutual.docx",
  ...
}
```

Clause text comes straight from `config/org-policy.json` `clause_rules[*].preferred`, so anything you set in `quickstart` (term length, return-vs-destroy, residual stance, trade-secret carve-out, affiliate scope) flows in automatically.

## 6. Doctor

```bash
$ ./nda_review_cli.py doctor

  Doctor report
  ────────────────────────────────
  [OK  ] policy_files
  [SKIP] build_playbook_paths — Corpus-free setup detected (no data/raw_strict). Skipping gmail/drive path checks — review still works against config/org-policy.json clause rules.
  [WARN] ingest_candidates

  Next steps:
    1. Add documents under `knowledge/inbox`, `knowledge/contracts`, ...
```

## What you didn't see

- `tutorial` — interactive primer that walks the three concepts (policy / profile / playbook) and runs a sandboxed sample review
- `wizard` — combined setup → ingest → build → review pipeline
- `playbook-snapshot` / `playbook-diff` / `playbook-lock` — version your playbook
- `generate-redlines` — produce clause-ready amendment text from a saved review
- `step3` / `step4` / `step5` shell pipeline — Word-ready tracked-change packs

See [GETTING_STARTED.md](../GETTING_STARTED.md) for scenario-based onboarding (solo lawyer, in-house team, Word-redline migration, Drive Takeout, SaaS rollout, drafting, LLM-augmented).
