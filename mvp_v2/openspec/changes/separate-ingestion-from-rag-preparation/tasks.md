# Tasks

## 1. Contracts and domain workflow

- [ ] 1.1 Обновить ingestion preview/commit contracts так, чтобы extracted text сохранялся для preview, но `chunk_texts` не был обязательным результатом ingestion; проверить contract tests.
- [ ] 1.2 Добавить/обновить preparation input/output contracts для явных chunk, vision и embedding actions; проверить strict validation и authorization cases.
- [ ] 1.3 Проверить publication validation на snapshot с current text version без chunks и зафиксировать результат regression test до изменения service behavior.

## 2. Ingestion separation

- [ ] 2.1 Удалить вызов chunk policy из ingestion preview/commit, сохранив identity, versioning, source SHA, assets и stale-preview guards; проверить ingestion integration tests.
- [ ] 2.2 Обеспечить atomic commit semantics для source/document snapshot и runtime files; проверить invalid extraction, duplicate identity и graph validation failures.
- [ ] 2.3 Обновить Upload UI/session state так, чтобы commit показывал отдельное состояние RAG preparation и не запускал model calls; проверить Streamlit closure tests.

## 3. Explicit RAG preparation

- [ ] 3.1 Подключить explicit verified-source chunk preparation к current Markdown versions; проверить deterministic, idempotent, changed-source и partial-chunk scenarios.
- [ ] 3.2 Сохранить destructive chunk regeneration/deletion semantics и asset preservation; проверить chunk maintenance tests.
- [ ] 3.3 Сохранить explicit vision authorization и failure atomicity; проверить unauthorized, empty-description и media-hash scenarios.
- [ ] 3.4 Сохранить explicit missing/all embedding semantics, dimensions, finite vectors и embedding history; проверить model operation tests.

## 4. Documentation and verification

- [ ] 4.1 Обновить module map, Upload и Chunks & Embeddings OpenSpec docs с новым порядком `ingestion → chunks → model operations`.
- [ ] 4.2 Запустить focused tests для ingestion и RAG preparation; все acceptance scenarios должны пройти.
- [ ] 4.3 Запустить Ruff, strict mypy и полный pytest через окружение `mvp_v2`; все проверки должны завершиться с exit code 0.
- [ ] 4.4 Проверить `git diff --check`, status и отсутствие изменений runtime data, `.env`, `.venv` и `*.lbdb`.
