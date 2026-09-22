# Design

## Context

В текущем implementation ingestion preview/commit вызывает deterministic chunking непосредственно внутри document workflow. Отдельные функции backfill уже умеют читать source с SHA-256 verification, а model operations умеют выполнять vision и embeddings с explicit authorization. Целевой change использует эти границы, но делает запуск RAG preparation явным.

## Goals / Non-Goals

**Goals:**

- Разделить document/source commit и RAG preparation на независимые application use cases.
- Сохранить GraphSnapshot как единственный in-memory aggregate и не добавлять новую graph schema.
- Сделать отсутствие chunks после ingestion допустимым состоянием до явного preparation action.
- Сохранить deterministic chunking, source verification, model authorization, embedding history и graph validation.
- Обеспечить atomic behavior: ошибка preparation не меняет доступный candidate snapshot.

**Non-Goals:**

- Автоматический запуск chunks, vision или embeddings после Upload Commit.
- Новая очередь задач, фоновые workers или distributed processing.
- Изменение CSV/LadybugDB schema, model adapters или provider transport.
- Требование, чтобы один preparation action одновременно выполнял chunks, vision и embeddings.

## Decisions

### Decision: ingestion больше не вызывает chunking

Ingestion сохраняет source text в `ExtractedInput`, но не строит `chunk_texts` и не вызывает `make_chunks`. Это устраняет скрытую RAG operation из document commit.

Альтернатива — оставить chunks в ingestion и добавить второй backfill path. Она сохраняет смешанную ответственность и не выполняет цель change.

### Decision: preparation работает с уже принятым snapshot

RAG preparation получает snapshot после ingestion commit и verified source resolver. Он возвращает новый validated snapshot либо ошибку; публикация dump остаётся отдельным явным действием.

Альтернатива — запускать preparation внутри persistence adapter. Это связало бы application policy с filesystem/CSV storage.

### Decision: подготовка chunks остаётся отдельной от model operations

Chunk preparation, vision description и embeddings имеют отдельные explicit actions. Это позволяет пользователю пересобрать chunks без немедленного вызова Ollama и повторить неудачную model operation независимо.

Альтернатива — один большой `prepare_rag()` pipeline. Он усложнил бы authorization, retry и partial failure semantics.

### Decision: отсутствие chunks не меняет graph schema

Canonical invariants не требуют, чтобы каждая current text version уже имела Chunk. Готовность определяется прикладным состоянием операции и наличием связанных nodes, без добавления новых graph fields.

Альтернатива — добавить отдельный persisted preparation status. Это расширяет schema и не требуется для первого change.

### Decision: UI не запускает скрытые model calls

После commit UI помечает snapshot dirty и предлагает явные preparation actions. Ошибка chunk/model operation не откатывает уже подтверждённый ingestion commit и не публикует dump автоматически.

Альтернатива — автоматически запускать preparation после upload. Это нарушило бы текущий explicit authorization boundary и усложнило UX при сетевых/model failures.

## Risks / Trade-offs

- [Risk] После commit пользователь может увидеть документ без chunks → Mitigation: UI явно показывает preparation state и отдельную кнопку создания chunks.
- [Risk] Existing tests и UI ожидают `IngestionPreview.chunk_texts` → Mitigation: обновить DTO/tests вместе с change и сохранить extracted text для preview display.
- [Risk] Частично подготовленные snapshots требуют повторного запуска → Mitigation: сохранять deterministic IDs, делать operations idempotent и использовать missing mode.
- [Risk] Пользователь может ожидать embeddings сразу после chunks → Mitigation: явно показывать последовательность `chunks → optional vision → embeddings`.

## Migration Plan

1. Зафиксировать новые ingestion/preparation contracts и acceptance tests.
2. Убрать chunk generation из preview/commit, сохранив source extraction и assets.
3. Подключить явное chunk preparation к существующему verified-source path.
4. Оставить vision и embedding operations отдельными authorized actions.
5. Обновить Upload и Chunks & Embeddings UI, session revision и error reporting.
6. Обновить docs/module map и OpenSpec UI docs.
7. Запустить focused tests, затем Ruff, mypy и полный pytest.

Existing dumps остаются читаемыми: они могут содержать уже подготовленные chunks/embeddings. Новые ingestion commits могут публиковаться без chunks только если publication contract допускает такой промежуточный snapshot; перед изменением publication behavior это проверяется отдельным тестом.

Rollback: откатить application/UI change до версии, где ingestion строит chunks; ранее опубликованные dumps и runtime media не изменяются.

## Open Questions

Нет.
