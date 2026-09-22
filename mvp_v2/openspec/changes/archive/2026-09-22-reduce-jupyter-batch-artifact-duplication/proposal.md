# Proposal

## Why

`notebooks/rag_batch_debug.ipynb` сохраняет одинаковые данные в каждом элементе `results.json`. В частности, каждый case повторяет database identity, chat model settings, `prompt_profile`, similarity threshold и полный rendered prompt из `generation_calls`. Это увеличивает размер JSON и смешивает run-level metadata с case-level results.

`run.json` уже является естественным местом для данных, общих для всего запуска. Нужно сделать его canonical source для run metadata, а `results.json` оставить компактным case-level trace.

## What Changes

- Сохранить в `run.json` общие для запуска database identity, chat model settings, similarity threshold и prompt profile/template metadata.
- Удалить из каждого элемента `results.json` повторяющийся `database` block.
- Удалить из каждого элемента `results.json` повторяющийся `chat_model`, `prompt_profile` и `minimum_similarity_score`.
- Не записывать полный rendered prompt в `results.json`; `generation_calls` должны сохранять raw response, latency и error trace без поля `prompt`.
- Сохранить case question, retrieved evidence, answer, generation/embedding call counts and diagnostics, latency и errors.
- Обновить sequential и async branches notebook одинаково.
- Обновить notebook contract tests и документацию о разделении `run.json` и `results.json`.
- Существующие runtime runs не переписывать и не удалять; новый формат применяется только к новым запускам.

## Capabilities

### New Capabilities

<!-- No new application capability; this changes the Jupyter artifact contract. -->

### Modified Capabilities

- `jupyter-rag-batch-artifacts`: run-level metadata is stored once in `run.json`; `results.json` contains compact case-level traces without repeated prompts and identities.

## Impact

- Затрагивается `mvp_v2/notebooks/rag_batch_debug.ipynb` и `mvp_v2/tests/test_rag_notebook_contracts.py`.
- Возможно обновление README/notebook documentation describing artifact contents.
- Application services, graph schema, database files, prompts semantics и runtime data не изменяются.
- Новые зависимости не добавляются.
