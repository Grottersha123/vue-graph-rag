# Proposal

## Why

RAG-код, операции над изменяемым `GraphSnapshot` и dump lifecycle сейчас распределены по корню `application` и технической папке `services`. Это затрудняет навигацию и смешивает независимые причины изменения. Нужен структурный перенос модулей без изменения поведения приложения.

## What Changes

- **BREAKING**: добавить `application/graph_snapshot_operations/`, `application/rag/` и `application/dump_lifecycle/` и обновить внутренние импорты без compatibility-shims.
- Перенести операции над изменяемым `GraphSnapshot` в `graph_snapshot_operations/`:
  - `ingestion.py`;
  - `document_editing.py`;
  - `document_view.py`;
  - `reference_data.py`;
  - `relations.py`;
  - `deletion.py`;
  - `title_backfill.py`.
- Разделить RAG-код на:
  - `rag/preparation/` — `chunking.py`, `chunk_backfill.py`, `model_operations.py`;
  - `rag/retrieval/` — `projection.py`, `retrieval.py`;
  - `rag/agent/` — `chat.py`, `chat_export.py`, `prompts.py`, `answer_rendering.py`, `errors.py`.
- Объединить `conversation/conf.py` и `conversation/prompting.py` в `rag/agent/prompts.py`, перенести `conversation/rendering.py` в `rag/agent/answer_rendering.py`, а `conversation/errors.py` в `rag/agent/errors.py`.
- Не создавать `agent.py` и не переименовывать `ChatService` на этой фазе.
- Перенести dump lifecycle в:
  - `dump_lifecycle/manifests.py`;
  - `dump_lifecycle/publication.py`;
  - `dump_lifecycle/validation.py`;
  - `dump_lifecycle/dump_restamp.py`.
- Удалить общий контейнер `application/services/` и старую папку `application/conversation/`.
- Оставить `deletion_orchestration.py`, `json_encoding.py`, `application/dto/` и `ingestion_contracts.py` отдельно.
- Обновить module map, внутренние импорты и тестовые import paths.
- Сохранить существующее поведение, доменные модели, DTO, CSV contract, LadybugDB schema, Ollama transport и runtime-данные.

## Capabilities

### New Capabilities

<!-- Pure structural refactor. No behavior capability is introduced. -->

### Modified Capabilities

<!-- No requirement-level behavior changes. -->

## Impact

- Затрагиваются только расположение application-модулей, импорты, composition points и тестовые import paths. Также проверяются импорты в Jupyter notebooks.
- Новые зависимости и внешние сервисы не добавляются.
- После переноса должны пройти существующие Ruff, mypy и pytest checks.
