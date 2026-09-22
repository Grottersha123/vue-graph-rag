# Proposal

## Why

RAG preparation modules сейчас находятся в корне `application` рядом с document lifecycle и затрудняют поиск кода, который готовит chunks, vision descriptions и embeddings. Нужен минимальный структурный перенос без изменения ingestion workflow или поведения RAG.

## What Changes

- **BREAKING**: перенести `application/chunking.py`, `application/chunk_backfill.py` и `application/model_operations.py` в `application/rag/preparation/`.
- Обновить application, presentation и test imports на новые module paths.
- Сохранить текущие функции, классы, DTO, authorization, model behavior и graph validation без изменений.
- Проверить `notebooks/rag_batch_debug.ipynb` после переноса; notebook не получает нового preparation workflow и не меняется функционально.
- Не переносить `ingestion.py`, retrieval, chat, dump lifecycle или adapters в рамках этого change.

## Capabilities

### New Capabilities

<!-- Pure structural refactor. No spec-level behavior is introduced. -->

### Modified Capabilities

<!-- No requirement-level behavior changes. -->

## Impact

- Затрагиваются три application-модуля, их imports в `ingestion.py` и `presentation/app.py`, тестовые imports и import verification для notebook.
- Новые зависимости не добавляются.
- Runtime data, `.env`, `.venv`, dumps, databases и LadybugDB artifacts не затрагиваются.
