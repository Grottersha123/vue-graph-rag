# Design

## Context

 - сейчас есть Streamlit, существующий RAG, Snapshot который делает дамп всех данных для Ladybug;
 - backend только читает готовую LadybugDB;
 - Нужен локальный FastAPI read-path backend: принимает вопрос, вызывает
 - Telegram и FastAPI — разные процессы;
 - Redis хранит выбранный profile_id
 - DuckDB используется только для аналитики.
 - feedback.
 - Redis для пользовательского выбора;


## Goals / Non-Goals

**Goals:**
Goals:
- `POST /v1/messages` с готовым ответом RAG
- `GET /health`
- `GET /v1/profiles` для Telegram-кнопок (подтвердить).
- Один polling adapter, allowlist Telegram user IDs в .env
- Выбор prompt profile в Telegram и оценка ответа 1 до 5
- JSONL-аудит: запрос, ответ, ошибки, feedback


**Non-Goals:**
   - Ingestion и database build в backend.
   - Изменения Streamlit и существующей RAG-логики.
   - Хранение постоянной истории диалога в immutable LadybugDB.
   - Одновременная работа нескольких Telegram-ботов.
     Adapter остаётся переиспользуемым, но в этом change подключается один бот.
   - Telegram webhook mode — используется только long polling.
   - Обработка voice messages, файлов и документов.
     В MVP поддерживаются текстовые сообщения изображение (но они в todo).
   - Streaming-ответы.
   - Изменение содержимого существующих prompt templates.
   - Изменение Ollama и LadybugDB adapters.
   - Production deployment, cloud hosting, horizontal scaling и high availability.
   - Создание отдельного web dashboard для аналитики.
     Аналитика выполняется Python-модулем через DuckDB.
   - Перенос или преобразование существующих runtime logs и database artifacts.
   - Тесты для телеграма не пишем
   - Для аналитической части тоже тесты не пишем

## Decisions

### Decision: Gateway API DTOs are separate from internal RAG DTOs

HTTP API models описывают только внешний контракт gateway: вопрос, Telegram IDs, `conversation_id`, optional `profile_id`, ответ и feedback. Они не требуют от Telegram клиента внутренних `SearchRequest`, `ModelAuthorization`, embedding space или model version. Backend mapper преобразует validated API `MessageRequest` во внутренний `ChatRequest`, который уже используется существующим `ChatService`.

Альтернатива — принимать внутренний `ChatRequest` напрямую из HTTP. Она отклоняется, потому что внешний API стал бы зависеть от деталей RAG implementation и был бы неудобен для Telegram adapter.

### Decision: conversation_id equals Telegram chat_id

Для текущего Telegram adapter `conversation_id` напрямую равен Telegram `chat_id`. Это даёт стабильный идентификатор для correlation, feedback и audit без добавления отдельного conversation registry. На этой фазе идентификатор не означает сохранённую историю: backend не пишет conversation messages в LadybugDB.

### Decision: Database is selected by database_name

Backend не выбирает автоматически самую новую базу. Typed Settings получает `MVP_V2_DATABASE_NAME`, например `clean-v7`, и разрешает только путь `runtime/databases/{database_name}.lbdb`. После открытия backend валидирует, что `DatabaseManifest.database_name` совпадает с настройкой и что artifact соответствует текущей schema contract.

### Decision: Backend hydrates evidence directly from LadybugDB

Backend не загружает `GraphSnapshot` из Streamlit session state и не требует `runtime/dumps/`. Он открывает подготовленный `.lbdb` read-only и использует `DatabaseManifest` для проверки artifact identity. Для результата ANN backend использует отдельный read-only `LadybugEvidenceReader`, который получает nodes и relations из базы и собирает `Evidence`: Chunk/Asset, владельца DocumentVersion, Document, current/active checks, context chunks, statuses, roles, processes и links.

`SearchService` получает узкий `EvidenceHydrator` protocol вместо прямой зависимости от `GraphSnapshot`. Существующий search/chat behavior и `ChatService` сохраняются, а Ladybug-specific queries остаются в adapter. Это позволяет backend работать только с `runtime/databases/` и не смешивать read-path gateway с dump lifecycle.

Альтернатива — загрузить `GraphSnapshot` из verified dump. Она отклоняется, потому что добавляет gateway зависимость от dump artifacts и не использует уже опубликованную database projection как read source.

### Decision: Podman Compose runs separate local services

`docker-compose.yml` в стандартном Compose-формате SHALL запускать три отдельных сервиса: FastAPI backend, Telegram polling adapter и Redis через Podman Compose. Backend и Telegram общаются по внутренней compose-сети; backend публикует на host только локальный API-порт, необходимый для health checks и локальной диагностики. Telegram token передаётся через локальную secret/configuration boundary и не записывается в image или repository. Корневой `Makefile` предоставляет единый host workflow для безопасного создания secret-файлов, Compose validation, build/start, readiness, end-to-end message smoke check, runtime mount/port audit, экспорта логов и остановки без удаления named volumes.

LadybugDB artifacts подключаются к backend как read-only volume. Ladybug `vector` extension явно provision-ится на этапе build в image-owned HOME; runtime выполняет только `LOAD vector`, не скачивает extension и не изменяет database artifact. Runtime JSONL logs и prompt traces подключаются отдельным writable volume. Docker/Podman socket, домашний каталог и SSH/GPG credentials в контейнеры не монтируются.

Ollama не входит в compose на этой фазе: backend получает его адрес через конфигурацию. При запуске контейнеров адрес Ollama не должен указывать на container-local `127.0.0.1`; используется доступный для Podman host/service address.

Альтернатива — запускать все процессы вручную на host. Она отклоняется, потому что не даёт воспроизводимого локального запуска backend, Telegram и Redis одной командой.

### Decision: Repository and runtime paths are separated

Код gateway располагается в существующем `src/mvp_v2`:

- `src/mvp_v2/backend/` — FastAPI app, routes, DTO и backend composition;
- `src/mvp_v2/telegram_bot/` — polling runner, handlers и backend client;
- `src/mvp_v2/analytics/` — Python DuckDB module `chat_logs.py`.

Runtime logs не смешиваются с существующими Streamlit logs и записываются в `runtime/logs/gateway/`:

- `backend.jsonl`;
- `telegram.jsonl`;
- `errors.jsonl`;
- `prompt_traces.jsonl`;
- `feedback.jsonl`.

Prepared databases остаются в `runtime/databases/` и подключаются read-only. Новые автоматические тесты располагаются только в `tests/backend/`; Telegram, Redis, DuckDB analytics, Podman и Ollama не запускаются в test suite этого change.

### Decision: Database identity comes from DatabaseManifest

Backend при загрузке LadybugDB SHALL читать и валидировать существующий `DatabaseManifest`. В `backend.jsonl` и `prompt_traces.jsonl` записываются `database_name`, `database_build_id` (значение manifest `build_id`) и `graph_schema_version`. Эти поля идентифицируют конкретный immutable artifact. Абсолютный путь к базе и внутренние filesystem paths в логи не записываются.

Отдельное произвольное поле `database_version` не вводится: точной версией artifact является `build_id`, а человекочитаемым именем — `database_name`.

### Decision: JSONL uses a common envelope and file-specific payload

Каждая запись — отдельная JSON-строка с общим envelope: `schema_version`, `event_id`, `event_ts`, `component`, `event_type`, nullable `request_id`, nullable `conversation_id` и обязательным `status`. Для каждого Telegram update adapter создаёт локальный `request_id` до allowlist check; backend сохраняет тот же ID. Для health, profile-list, startup и самостоятельных error events correlation fields могут быть `null`. `status` обязателен во всех файлах и имеет file-specific controlled values. Backend не дублирует transport events, Telegram не дублирует полный answer/prompt, а prompt trace хранится отдельно по generation attempt. DuckDB читает все пять файлов через `read_json_auto(..., union_by_name=true)` и связывает их по `request_id`, когда он не равен `null`.

### Decision: Logging ownership is split by process

Telegram adapter пишет только transport events в `telegram.jsonl`: входящие Telegram updates, user/chat/message IDs, profile selection, feedback actions и результат доставки ответа. Backend пишет только API и RAG events в `backend.jsonl`: вопрос, готовый ответ, effective `profile_id`, prompt version, model metadata, retrieval metadata, status и latency. Полный rendered prompt пишется отдельно в `prompt_traces.jsonl`, а принятый feedback — в `feedback.jsonl`.

Ошибки backend и Telegram записываются в `errors.jsonl` с обязательным полем `component`, равным `backend` или `telegram`. Связанные записи используют общий `request_id` и `conversation_id`; независимые startup/error events могут иметь `null`. Пойманные runtime exceptions дополнительно сохраняют bounded `error` и `traceback` только в локальном audit volume; события без exception context сохраняют оба поля как `null`. Traceback не возвращается через API или Telegram и не должен содержать tokens либо authorization headers. Python analytics module объединяет связанные события, но ни один процесс не пишет логи другого компонента.

Альтернатива — отправлять все события Telegram в backend для централизованной записи. Она отклоняется, потому что ошибка или недоступность backend не должна лишать нас Telegram transport log.

### Decision: DuckDB analytics is a separate Python module

Аналитика реализуется в `analytics/chat_logs.py` и запускается отдельно от FastAPI request path. Модуль читает JSONL напрямую через DuckDB и предоставляет стандартные отчёты: запросы по дням и статусам, среднее и p95 latency, ошибки по компонентам, использование `profile_id` и запросы по Telegram-пользователям.

Альтернатива — выполнять аналитические SQL-запросы внутри FastAPI. Она отклоняется, потому что DuckDB не должен влиять на обработку пользовательского вопроса.

### Decision: GET /v1/profiles is the profile source of truth

Telegram adapter не хранит hardcoded список profiles. Backend строит ответ `GET /v1/profiles` из `PROMPT_TEMPLATES`, возвращая `profile_id`, display metadata и global default, но не полный текст prompt. MCP profiles остаются за пределами Telegram chat profile list.

### Decision: profile_id is taken from the existing RAG prompt registry

`profile_id` не является новым независимым справочником. Backend и Telegram adapter используют имена `PromptTemplate` из `application/rag/agent/prompts.py` и mapping `PROMPT_TEMPLATES`. Для текущего chat pipeline доступен `support-v1`, а `DEFAULT_PROMPT_TEMPLATE` указывает на него. Backend владеет per-user default и хранит только выбранный идентификатор в Redis, например `support-v1`; Telegram adapter не хранит это состояние и не хранит текст prompt в Redis. Выбор изменяется через `PUT /v1/users/{telegram_user_id}/profile`, а при обработке сообщения backend разрешает effective profile из явного `profile_id`, Redis default или global default.

### Decision: Request debugging uses a separate prompt trace log

`backend.jsonl`, `telegram.jsonl` и `errors.jsonl` сохраняют metadata и общий `request_id`, но не полный prompt. Полный rendered prompt сохраняется один раз в `prompt_traces.jsonl` и связывается с остальными событиями через `request_id`.

Это позволяет получить вопрос, ответ, `profile_id`, prompt version и полный prompt одним debug-запросом без дублирования prompt во всех JSONL-файлах.

Альтернатива — хранить полный prompt в каждой записи backend, Telegram и errors. Она отклоняется из-за дублирования и увеличения размера логов.

### Decision. Concurrency: 6 запросов, перегрузка это HTTP 503
При достижении лимита новый запрос получает HTTP 503 и не ставится
в неограниченную очередь.
Отдельный per-model limit: при заполненной модели запрос к ней получает 503,
а запрос к другой модели при свободной capacity обрабатывается.

### Decision: Ollama timeout is explicit and bounded

Backend читает `MVP_V2_OLLAMA_TIMEOUT_SECONDS` через typed `GatewaySettings`, использует default 120 секунд и отклоняет non-finite, non-positive и значения больше 600. Composition root передаёт timeout в `LocalHttpTransport`; adapter не читает environment напрямую. HTTP client получает только generic `service_unavailable`, а bounded exception diagnostics остаются в локальном `errors.jsonl`.

### Decision: `/start` is a transport command

Telegram adapter регистрирует отдельный `/start` handler до generic message handler. Команда возвращает короткую справку, применяет тот же allowlist boundary и не вызывает backend message endpoint, retrieval или generation.

### Decision: Container cleanup preserves persistent state

`make clean` использует Compose `down` без `--volumes` и не удаляет images, secrets, `.env` или database artifacts. Для полного удаления persistent state отдельная автоматическая цель намеренно не предоставляется. `make deploy` использует force-recreate, чтобы long-lived процессы не сохраняли stale Python contracts.

## Risks / Trade-offs

- Один процесс и лимит 6: при пиковой нагрузке пользователь получит 503,
  зато без очереди и без роста latency.
- Раздельные JSONL: проще читать, но связывание только по `request_id`.
- Профиль в теле: один эндпоинт, но логирование и валидация ложатся на backend.

## Migration Plan

<!-- Опишите порядок внедрения и rollback strategy, если она нужна. -->

## Open Questions
- Зачем нужен dto и почему он так называется
