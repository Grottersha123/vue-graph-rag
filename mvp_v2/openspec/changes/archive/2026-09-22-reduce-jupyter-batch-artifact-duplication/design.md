# Design

## Context

Notebook has two batch execution branches: sequential and async. Both branches currently construct almost identical result dictionaries. The result dictionary contains metadata that is constant for the whole run and generation-call prompts that can be reconstructed from the run prompt contract plus case trace. This makes `results.json` unnecessarily large and duplicates the same database/prompt information once per case.

## Goals / Non-Goals

**Goals:**

- Define `run.json` as the single owner of run-level configuration and identity.
- Keep `results.json` focused on case inputs, retrieval output, model output and operational diagnostics.
- Apply the same serialization contract to sequential and async execution.
- Preserve raw model responses, errors, evidence, latency and result ordering.
- Keep old runtime artifacts immutable.

**Non-Goals:**

- Changing prompt text, prompt profile definitions, model behavior or authorization.
- Changing the production `ChatService`, retrieval services or application contracts.
- Rewriting existing `runtime/debug/rag-runs/` directories.
- Adding a migration reader for old artifacts unless an existing consumer requires it.

## Decisions

### Decision: run-level metadata is canonical in `run.json`

`run.json` retains `database`, `chat_model`, `minimum_similarity_score` and the existing `prompt` metadata, including the selected profile. `results.json` does not repeat these values per case.

### Decision: rendered prompts are not persisted in case results

`generation_calls[*].prompt` is removed from newly written `results.json`. The run keeps the selected prompt profile and prompt policy/template metadata. Raw responses, latency and error details remain available for debugging without storing every rendered prompt.

### Decision: both execution branches use one compact result shape

Sequential and async branches must emit the same case-level keys. The notebook should use a shared result-shaping helper or equivalent explicit construction so the two branches cannot drift.

A result keeps:

- `case` and `request_question`;
- retrieval and embedding diagnostics;
- `retrieved_evidence`;
- generation diagnostics without rendered prompts;
- `answer`, `total_latency_ms`, `error` and `recorded_at`.

A result no longer keeps run-level `database`, `chat_model`, `prompt_profile` or `minimum_similarity_score`.

### Decision: no runtime migration

Existing run directories remain valid historical artifacts. The notebook writes only the new compact schema for future runs. Any consumer that reads both old and new artifacts must treat missing per-case metadata as inherited from `run.json`.

## Risks / Trade-offs

- [Risk] A consumer may expect the old per-case keys → Mitigation: add notebook contract tests and document the new ownership in README.
- [Risk] Removing rendered prompts reduces per-case replay detail → Mitigation: keep complete prompt profile/template metadata in `run.json` and preserve request questions, evidence and raw responses.
- [Risk] Sequential and async serializers may diverge → Mitigation: assert the same forbidden keys are absent from both branches.

## Migration Plan

1. Add contract tests describing run-level and case-level keys.
2. Update sequential result serialization.
3. Update async result serialization.
4. Keep run metadata complete and update notebook/README artifact documentation.
5. Run notebook compile/contract tests, Ruff, mypy and full pytest.

Rollback is a notebook-only revert; existing runtime artifacts are untouched.
