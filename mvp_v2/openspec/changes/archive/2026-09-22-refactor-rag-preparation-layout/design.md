# Design

## Context

`chunking.py`, `chunk_backfill.py` и `model_operations.py` реализуют preparation policy и model enrichment, но находятся рядом с общими application use cases. Их consumers включают ingestion, Streamlit presentation и tests. Notebook batch debugger использует retrieval/chat paths и не импортирует эти три модуля напрямую.

## Goals / Non-Goals

**Goals:**

- Создать минимальный package boundary `application/rag/preparation/`.
- Перенести только три preparation-модуля и обновить consumers.
- Сохранить module API внутри проекта и observable behavior.
- Выполнить import/static verification notebook без функционального изменения его cells.

**Non-Goals:**

- Отделение ingestion от preparation.
- Перенос retrieval, chat, conversation, dump lifecycle или adapters.
- Переименование функций, классов или DTO.
- Добавление compatibility-shims.

## Decisions

### Decision: прямой перенос трёх файлов

Файлы перемещаются в `rag/preparation/` с сохранением содержимого. Это минимизирует behavioral risk и отражает уже существующую ответственность модулей.

Альтернатива — оставить старые re-export modules. Она сохраняет неоднозначную карту imports и не нужна для внутренних consumers.

### Decision: imports обновляются во всех consumers

`ingestion.py` остаётся в application root, но импортирует chunk policy из `rag/preparation`. `presentation/app.py` и tests используют новые paths.

Альтернатива — переместить ingestion одновременно. Это расширяет scope и относится к отдельному ingestion/RAG workflow change.

### Decision: notebook только проверяется

Notebook не импортирует preparation modules напрямую, поэтому структурная проверка должна подтвердить, что его текущие imports и production pipeline по-прежнему загружаются. Его content не меняется.

Альтернатива — добавить preparation cells. Это уже новая функциональность notebook и отдельный evaluation scope.

## Risks / Trade-offs

- [Risk] Остаточная ссылка на старый module path ломает import → Mitigation: repository-wide search и import tests.
- [Risk] Notebook содержит пользовательские незакоммиченные изменения → Mitigation: не переписывать notebook, только выполнять безопасную import/static check.
- [Risk] Новая package path создаёт cycle → Mitigation: проверить Ruff, mypy и focused test collection.

## Migration Plan

1. Создать `rag/preparation/__init__.py`.
2. Переместить три модуля.
3. Обновить consumers и tests.
4. Проверить отсутствие старых imports.
5. Выполнить notebook import/static verification.
6. Запустить focused tests, Ruff и mypy.

Rollback — вернуть три файла и imports на исходные пути; runtime artifacts не затрагиваются.

## Open Questions

Нет.
