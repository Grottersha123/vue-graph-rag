# Tasks

## 1. Target package structure

- [x] 1.1 Создать `graph_snapshot_operations/`, `rag/preparation/`, `rag/retrieval/`, `rag/agent/` и `dump_lifecycle/`; проверить наличие package initializers и отсутствие изменений runtime data.
- [x] 1.2 Переместить GraphSnapshot use cases в `graph_snapshot_operations/`; проверить, что содержимое функций не изменилось через focused diff и import check.
- [x] 1.3 Переместить chunking/model preparation модули в `rag/preparation/`; проверить deterministic chunk tests и model operation tests.
- [x] 1.4 Переместить projection/retrieval модули в `rag/retrieval/`; проверить retrieval and projection tests.
- [x] 1.5 Перенести `chat.py` и `chat_export.py` в `rag/agent/`, объединить `conversation/conf.py` и `prompting.py` в `prompts.py`, перенести rendering в `answer_rendering.py` и errors в `errors.py`; проверить prompt/citation tests.
- [x] 1.6 Перенести `manifests.py`, publication, validation и `dump_restamp.py` в `dump_lifecycle/`; удалить `application/services/` и проверить dump tests.

## 2. Import and documentation migration

- [x] 2.1 Обновить импорты в `ports/`, `adapters/`, `presentation/`, bootstrap и notebooks; проверить импорт всего `mvp_v2` без старых module paths.
- [x] 2.2 Обновить импорты и fixtures в tests; проверить отсутствие ссылок на удалённые `application.services` и `application.conversation` paths.
- [x] 2.3 Обновить `docs/module-map.md` и связанные OpenSpec/UI references; проверить соответствие документации фактическому дереву.

## 3. Verification

- [ ] 3.1 Запустить focused tests для graph operations, RAG, dump lifecycle и presentation imports; все выбранные тесты должны пройти.
- [ ] 3.2 Запустить Ruff, strict mypy и полный pytest через окружение `mvp_v2`; все три проверки должны завершиться с exit code 0.
- [ ] 3.3 Проверить `git diff --check`, повторно прочитать изменённые файлы и убедиться, что runtime data, `.env`, `.venv` и `*.lbdb` не изменены.
