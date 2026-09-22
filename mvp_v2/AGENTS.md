# AGENTS.md — mvp_v2

Правила для агентов, работающих в этой папке. Корневой `/AGENTS.md` тоже действует.

## Границы

- Менять можно только файлы внутри `mvp_v2/`. Папки `mvp/` и `agentic_mvp/` — read-only референсы.
- Запрещено трогать: `.venv`, `.env*`, `*.lbdb`, `runtime/dumps/`, `runtime/databases/`,
  `runtime/mvp_data/` (это данные пользователя; дампы иммутабельны).
- Корневые `pyproject.toml`/`uv.lock` не менять. Зависимости ставить только через
  согласие пользователя в `mvp_v2/pyproject.toml`.

## Окружение и проверки

Рабочее окружение — `/tmp/vue-graph-rag-mvp-v2/.venv` (не репозиториевский .venv):

```bash
UV_PROJECT_ENVIRONMENT=/tmp/vue-graph-rag-mvp-v2/.venv uv run --project mvp_v2 ruff check mvp_v2/src mvp_v2/tests
UV_PROJECT_ENVIRONMENT=/tmp/vue-graph-rag-mvp-v2/.venv uv run --project mvp_v2 pytest mvp_v2/tests -q
UV_PROJECT_ENVIRONMENT=/tmp/vue-graph-rag-mvp-v2/.venv uv run --project mvp_v2 mypy mvp_v2/src mvp_v2/tests
```

Зелёные ruff + mypy (strict) + pytest обязательны перед каждым коммитом.

## Архитектура (не ломать)

- `src/mvp_v2/domain/` — pydantic-модели и контракты (`ContractModel`,
  `RequiredMultilineText`/`NonBlankText` для текстовых полей ответов).
- `src/mvp_v2/application/` — сценарии; чистые функции, без streamlit.
- `src/mvp_v2/adapters/` — filesystem, CSV, ingestion, LadybugDB и Ollama; legacy-import отсутствует.
- `src/mvp_v2/presentation/app.py` — весь UI; каждое действие и долгая операция —
  через `_run_visible_operation` (spinner + Loguru в `runtime/logs/app.log`),
  ошибки — через `_record_error`, никаких «сырых» traceback в UI.
- LadybugDB: `LOAD vector` на каждом connection; дампы иммутабельны
  (manifest.json с sha256; run_id меняется при пере-подписи).

## Известные ловушки

- Новое обязательное поле `UiRuntimeConfig` требует синхронного обновления
  `parse_runtime_config` в `presentation/controller.py`, иначе UI падает в
  "Invalid runtime configuration. Every operation is blocked."
- Дампы правятся либо через приложение (Publish), либо через фичу
  "Repair manually edited dump" (LadybugDB) — не руками вслепую.
- Chat отвечает по-русски; цитаты парсятся leniently (см. `application/chat.py`).

## Процесс

- Коммиты — только с явного разрешения пользователя, сфокусированные,
  после независимого review и `git diff --check`.
- Новый рабочий день в репозитории — git tag `work-YYYY-MM-DD`.
- Стиль: минимум лишних валидаторов и усложнений; сомнительное решение —
  спросить пользователя до реализации.
- Долгосрочный план изменений: `openspec/changes-2026-09-15.md`.
