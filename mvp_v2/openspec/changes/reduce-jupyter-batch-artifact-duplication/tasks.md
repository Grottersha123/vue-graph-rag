# Tasks

## 1. Artifact contract

- [ ] 1.1 Define and test the new ownership boundary: run-level database/model/prompt metadata in `run.json`, case-level diagnostics in `results.json`.
- [ ] 1.2 Add contract assertions that new result records do not contain `database`, `chat_model`, `prompt_profile`, `minimum_similarity_score` or rendered generation prompts.

## 2. Notebook serialization

- [ ] 2.1 Update sequential batch serialization to omit duplicated run metadata and `generation_calls[*].prompt`.
- [ ] 2.2 Update async batch serialization to emit the same compact result shape as sequential execution.
- [ ] 2.3 Preserve complete `run.json` metadata and document that old runtime artifacts are not rewritten.

## 3. Verification

- [ ] 3.1 Run notebook compile and contract tests for both serialization branches.
- [ ] 3.2 Update and verify README/notebook artifact documentation.
- [ ] 3.3 Run Ruff, strict mypy and full pytest through the `mvp_v2` environment.
- [ ] 3.4 Check `git diff --check` and confirm no runtime data, `.env`, `.venv` or `*.lbdb` files changed.
