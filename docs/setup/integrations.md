# Integrations: docx2pdf-cli and sign-cli hooks

`negotiate finalize` can hand off to your own PDF and signing tools via configured shell commands.

## Configure

Copy the example file (gitignored):

```bash
cp config/integrations.json.example config/integrations.json
$EDITOR config/integrations.json
```

The shape:

```json
{
  "docx2pdf_cmd": "docx2pdf --strict-fidelity {input_docx} {output_pdf}",
  "sign_cli_cmd": "sign request create --title {negotiation_id} --document {output_pdf} --signer 'name:{party_a_name},email:...,order:1' --signer 'name:{party_b_name},email:...,order:2'"
}
```

Placeholders the CLI substitutes:

- `{input_docx}` — path to the finalized `.docx` from `negotiate finalize`
- `{output_pdf}` — desired PDF output path
- `{negotiation_id}` — the negotiation's ID
- `{party_a_name}`, `{party_b_name}` — names from the negotiation state

## Use

```bash
nda-review-cli negotiate finalize \
  --state negotiation.json \
  --out-md output/agreed.md \
  --out-docx output/agreed.docx \
  --to-pdf --sign
```

`--to-pdf` invokes `docx2pdf_cmd`; `--sign` invokes `sign_cli_cmd`. Both are optional and independent.

## Suite cross-links

If you have all three CLIs installed:

- [docx2pdf-cli](https://github.com/DrBaher/docx2pdf-cli) — `docx2pdf` binary, `--strict-fidelity` recommended
- [sign-cli](https://github.com/DrBaher/sign-cli) — `sign` binary, offline PAdES signer or hosted providers

Or use LibreOffice + your own signing tool — the hooks are intentionally provider-agnostic. The substitution just runs whatever shell command you configure.

## Failure handling

If the configured command exits non-zero, `negotiate finalize` reports the error and returns exit `3`. The `.md` and `.docx` outputs are still written, so you can re-invoke the hook manually after fixing the underlying issue.
