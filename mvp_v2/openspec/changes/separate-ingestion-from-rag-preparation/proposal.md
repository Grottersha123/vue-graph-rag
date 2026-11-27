# Proposal

## Why

Сейчас `application/ingestion.py` одновременно создаёт версию документа и строит text chunks. Из-за этого загрузка исходного материала не отделена от последующей подготовки данных для RAG, а изменение chunking policy затрагивает основной ingestion workflow. Нужно сделать RAG preparation отдельным явным процессом.

## What Changes

- Отделить ingestion source/document workflow от RAG preparation workflow.
- Сделать ingestion ответственным за preview/commit документа, `DocumentVersion`, source files и assets.
- Перенести создание и пересборку chunks в явный RAG preparation use case.
- Сохранить отдельные операции vision description и embeddings в RAG preparation контуре.
- Определить порядок и условия запуска подготовки chunks после успешного ingestion commit.
- Сохранить deterministic chunk IDs, source SHA verification, embedding history, model authorization и graph invariants.
- Обновить UI/session workflow, чтобы состояние документа и состояние RAG preparation отображались раздельно.
- Добавить acceptance tests для ingestion без chunks, явной chunk preparation, changed source, partial chunks и model failures.
- Не менять доменную graph schema, LadybugDB storage contract и Ollama adapter contract без отдельного решения.

## Capabilities

### New Capabilities

- `ingestion-lifecycle`: ingestion создаёт документ, версию, source и assets отдельно от RAG preparation.
- `rag-preparation`: явная подготовка chunks, vision descriptions и embeddings из уже принятого GraphSnapshot.

### Modified Capabilities

<!-- No existing OpenSpec capabilities are present; both contracts are new. -->

## Impact

- Затрагиваются `graph_snapshot_operations/ingestion.py`, `rag/preparation/`, DTO ingestion/preparation, Streamlit workflow и связанные tests.
- Изменяется пользовательский workflow после Upload Commit.
- Потребуется явно определить snapshot revision, статусы подготовки и поведение при частично подготовленном документе.
- Новые зависимости не добавляются.
