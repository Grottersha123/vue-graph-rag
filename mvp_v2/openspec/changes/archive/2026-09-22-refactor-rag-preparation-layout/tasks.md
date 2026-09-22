# Tasks

## 1. Move preparation modules

- [x] 1.1 Создать `src/mvp_v2/application/rag/preparation/` package и проверить его импорт без side effects.
- [x] 1.2 Переместить `application/chunking.py`, `chunk_backfill.py` и `model_operations.py` в `rag/preparation/` без изменения содержимого; проверить focused diff.
- [x] 1.3 Обновить imports в `application/ingestion.py`, `presentation/app.py` и tests; проверить repository-wide search не находит старые preparation paths.

## 2. Notebook verification

- [x] 2.1 Проверить, что `notebooks/rag_batch_debug.ipynb` не содержит прямых imports старых preparation paths и production imports notebook собираются после переноса; notebook content не изменять.

## 3. Verification

- [x] 3.1 Запустить chunk backfill, document context, phase 78 и presentation focused tests; все выбранные тесты должны пройти.
- [x] 3.2 Запустить Ruff и strict mypy для `mvp_v2/src` и `mvp_v2/tests`; обе проверки должны завершиться с exit code 0.
- [x] 3.3 Запустить полный pytest и проверить `git diff --check` по файлам change; runtime data, `.env`, `.venv`, dumps, databases и `*.lbdb` остались нетронутыми.
