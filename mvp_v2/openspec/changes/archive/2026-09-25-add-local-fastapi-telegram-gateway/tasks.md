# Tasks

## 1. Contracts and project setup

- [x] 1.0 Создать внешний venv `/tmp/vue-graph-rag-mvp-v2/.venv`, настроить использование через `UV_PROJECT_ENVIRONMENT` и выполнить `uv sync` для `mvp_v2`; проверить, что Python и pytest запускаются из `/tmp`, а `mvp_v2/.venv` не изменяется.
- [x] 1.1 Провести финальный `/opsx:explore` без изменения файлов и сверить реальные entrypoints RAG, LadybugDB, Ollama и Streamlit с текущим design; результатом должен быть список подтверждённых путей и ограничений.
- [x] 1.3 Создать простые Pydantic API-модели для JSON запросов и ответов `/v1/messages`, `/v1/profiles`, profile selection и `/v1/feedback`; отдельно преобразовать `MessageRequest` во внутренний RAG `ChatRequest`; проверить модели в свежем Python interpreter.
- [x] 1.4 Создать единый typed Settings для backend, Telegram и Redis, включая `MVP_V2_DATABASE_NAME`. Читать настройки из environment, проверять обязательные значения при старте и завершать запуск с понятной безопасной ошибкой при неверной конфигурации; проверить fail-fast сценарии без вывода секретов.
- [x] 1.5 Добавить bounded `MVP_V2_OLLAMA_TIMEOUT_SECONDS` с default 120 в typed backend settings, передать его в Ollama transport и проверить default, valid override и invalid fail-fast cases.

## 2. FastAPI backend and RAG gateway

- [x] 2.1 Добавить backend composition/lifespan, который по `MVP_V2_DATABASE_NAME` открывает `runtime/databases/{database_name}.lbdb` и существующие RAG services; проверить startup без ingestion/build workflow, без auto-select latest и без `runtime/dumps/`.
- [x] 2.1a Валидировать `DatabaseManifest` выбранного `database_name` при загрузке и передавать в audit context `database_name`, `database_build_id` из `build_id` и `graph_schema_version`; проверить отказ при несовпадении имени/manifest, несовместимом artifact и отсутствие absolute paths в logs.
- [x] 2.1b Добавить read-only `LadybugEvidenceReader` и `EvidenceHydrator` protocol для nodes/relations/evidence; обновить `SearchService` без изменения `ChatService` behavior и проверить через fake hydrator.
- [x] 2.2 Реализовать `GET /health`; проверить HTTP 200 для готового runtime и безопасную ошибку при неготовой базе.
- [x] 2.3 Реализовать `GET /v1/profiles` из `PROMPT_TEMPLATES`; проверить наличие `support-v1`, global default и отсутствие полного текста prompt и MCP profile.
- [x] 2.4 Реализовать `POST /v1/messages` с вопросом, Telegram IDs, `source_message_id`, `conversation_id` и optional `profile_id`; проверить готовый ответ через fake RAG pipeline.
- [x] 2.5 Реализовать `PUT /v1/users/{telegram_user_id}/profile`; проверить валидацию profile ID и сохранение только доступного профиля.
- [x] 2.6 Реализовать `POST /v1/feedback` с `request_id`, Telegram user ID, rating 1..5 и optional comment; проверить invalid rating, unknown request и успешное принятие.
- [x] 2.7 Сохранить allowlist в .env и безопасные generic error responses; проверить отказ неразрешённому Telegram user ID без запуска RAG.
- [x] 2.8 Ограничить общий backend concurrency шестью запросами; проверить HTTP 503 при исчерпании capacity и отсутствие неограниченной очереди.
- [x] 2.9 Добавить отдельный per-model concurrency limit; проверить, что перегруженная модель получает HTTP 503, а другая модель при свободной capacity продолжает работать.
- [x] 2.10 Поддержать `conversation_id` в backend API model и audit context: backend должен сохранить полученное значение в связанных JSONL-записях и не записывать историю в LadybugDB; Mapping Telegram `chat_id -> conversation_id` остаётся transport contract без отдельного integration-теста.

## 3. Prompt profiles and Redis state

- [x] 3.1 Получать доступные chat `profile_id` из `PROMPT_TEMPLATES`; проверить, что текущий default равен `support-v1`, а `mcp-readonly-v1` не попадает в Telegram список.
- [x] 3.2 Реализовать backend Redis store для `telegram_user_id -> profile_id`; проверить configuration contract и runtime-поведение без добавления Redis-тестов.
- [x] 3.3 Использовать Redis key namespace для проекта и не сохранять в Redis текст prompt; проверить значение ключа и отсутствие prompt body.
- [x] 3.4 Реализовать effective profile resolution: explicit request profile → Redis user default → global default; проверять бизнес-логику через in-memory profile-store stub, без Redis.

## 4. Telegram polling adapter

- [x] 4.1 Добавить Telegram polling runner, backend client и configuration из environment; проверить запуск без импорта LadybugDB или RAG services в adapter.
- [x] 4.2 Принимать Telegram update, создавать `request_id` до allowlist, передавать разрешённые текстовые сообщения в backend и отправлять готовый ответ; проверить `telegram_user_id`, `chat_id`, `source_message_id`, `conversation_id` и сохранение одного `request_id`.
- [x] 4.3 Загружать profile list через `GET /v1/profiles` и строить buttons без hardcoded profile IDs; проверить обновление списка после добавления нового `PromptTemplate`.
- [x] 4.4 Отправлять выбор профиля через `PUT /v1/users/{telegram_user_id}/profile`; проверить замену default и обработку неизвестного profile ID.
- [x] 4.5 Показывать rating controls 1..5 после ответа и отправлять rating через `/v1/feedback`; проверить кнопку пропуска.
- [x] 4.6 Реализовать необязательный comment flow и pending feedback state в Redis с ограниченным TTL; бизнес-логику проверять через in-memory state stub, без Redis.
- [x] 4.7 Проверить, что Telegram adapter не выполняет retrieval/generation и при недоступном backend сообщает безопасную ошибку пользователю.
- [x] 4.8 Добавить отдельный `/start` handler до generic message handler; проверить usage response для allowed user и отсутствие backend/RAG вызова для allowed и denied users.

## 5. JSONL audit and DuckDB analytics

- [x] 5.1 Зафиксировать JSONL schemas для `backend.jsonl`, `telegram.jsonl`, `errors.jsonl`, `prompt_traces.jsonl` и `feedback.jsonl`; проверить обязательные envelope fields, обязательный `status`, локальный `request_id` для каждого Telegram update, nullable correlation fields только для независимых событий, database identity fields и nullable bounded `error`/`traceback` для локальной диагностики пойманных exceptions.
- [x] 5.2 Реализовать process-owned writers: Telegram пишет transport events, backend пишет API/RAG events, ошибки обоих компонентов содержат `component`; проверить отсутствие записи Telegram-событий backend writer-ом.
- [x] 5.3 Записывать полный rendered prompt только один раз в `prompt_traces.jsonl`; проверить, что backend/Telegram/error logs содержат только metadata, profile ID, version, hash и ссылку.
- [x] 5.4 Записывать accepted feedback в `feedback.jsonl`; проверить связь с `request_id`, `conversation_id`, Telegram user ID, rating и optional comment.
- [x] 5.5 Реализовать `analytics/chat_logs.py` на DuckDB; проверить отчёты по дням и статусам, average/p95 latency, ошибкам, `profile_id`, Telegram users и feedback ratings.
- [x] 5.6 Реализовать debug query по `request_id`; проверить объединение вопроса, ответа, Telegram event, effective profile, prompt version, latency, errors и полного rendered prompt.
- [x] 5.7 Проверить поведение debug query при отсутствующем prompt trace и повреждённой/пустой JSONL-записи.

## 6. Podman Compose runtime

- [x] 6.1 Добавить Containerfile/Dockerfile для FastAPI backend с build-time provisioning Ladybug `vector` extension; проверить health и реальный ANN message path внутри image без runtime install.
- [x] 6.2 Добавить Containerfile/Dockerfile для Telegram polling adapter; проверить подключение к backend и Redis по service names.
- [x] 6.3 Добавить `docker-compose.yml` со службами `backend`, `telegram` и `redis` и корневой `Makefile` для secrets/config/deploy/verify/down; проверить `make deploy` через Podman без вывода credentials.
- [x] 6.4 Добавить Redis healthcheck, service dependencies и внутреннюю network; проверить порядок готовности backend/Telegram.
- [x] 6.5 Подключить только `runtime/databases/` read-only и JSONL/prompt traces отдельным writable volume; не монтировать `runtime/dumps/` в gateway и проверить отсутствие write access к database artifacts.
- [x] 6.6 Передавать Telegram token и другие secrets через локальную secret boundary; проверить отсутствие token в image, repository и logs.
- [x] 6.7 Настроить внешний Ollama URL для контейнера и проверить, что backend не использует container-local `127.0.0.1` для host Ollama.
- [x] 6.8 Выполнить container-security audit и dry-run compose; проверить отсутствие home, SSH/GPG, Docker/Podman socket mounts и лишних опубликованных портов.
- [x] 6.9 Проверить `make clean`: Compose containers/network удалены, а images, Redis/log named volumes, secrets, `.env` и Ladybug database artifacts сохранены.

## 7. Verification and acceptance

- [x] 7.1 Добавить только backend unit tests для DTO, profile registry, profile resolution через in-memory stub, concurrency limits, redacted errors и JSONL writer; все tests должны пройти без подключения к реальным сервисам.
- [x] 7.2 Добавить изолированные backend endpoint tests с fake RAG, in-memory profile store и fake audit writer; проверить `/health`, `/v1/profiles`, `/v1/messages`, profile update и feedback без интеграционного запуска сервисов.
- [x] 7.3 Проверить runtime construction DTO/services в свежем interpreter и явно зафиксировать reload/import order для long-lived processes.
- [x] 7.4 Выполнить `make clean && make deploy`, затем проверить `make health`, Telegram `/start`, `/profiles`, обычный вопрос и экспортированный audit по одному request ID.
- [x] 7.6 Запустить `openspec validate add-local-fastapi-telegram-gateway --strict`; validation должна завершиться успешно.
- [x] 7.7 Проверить `git diff --check`, повторно прочитать изменённые файлы и убедиться, что `.env`, `.venv`, runtime data и `*.lbdb` не изменены.

## 8. Repository layout

- [x] 8.1 Создать если нет `src/mvp_v2/backend/`, `src/mvp_v2/analytics/` и модули внутри существующего `src/mvp_v2/telegram_bot/`; проверить импорт backend entrypoint.
- [x] 8.2 Создать только `tests/backend/` для новых gateway-тестов; проверить, что тесты не импортируют production secrets и не запускают Telegram, Redis, Ollama или LadybugDB.
- [x] 8.3 Не добавлять integration/e2e tests в этот change; Podman Compose и реальные сервисы проверяются отдельным ручным smoke run после реализации.
