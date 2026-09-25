# Proposal

## Why

Сейчас RAG-логика доступна через Streamlit UI и не имеет отдельного API,
который можно было бы использовать из разных Telegram-ботов,
но будет подключен только один TG bot.

Нужен локальный FastAPI read-path backend. Он должен принимать вопрос,
вызывать существующий RAG pipeline, возвращать готовый ответ и сохранять
структурированный JSONL-аудит.

Telegram polling реализуется как отдельный транспортный адаптер. Адаптер
передаёт сообщения в backend и отправляет полученный ответ пользователю.
Код адаптера не содержит RAG-логики и может быть повторно использован для
других Telegram-ботов без копирования application-кода.
структурированный JSONL-аудит.
отдельно для error, отдельно для тг + backend

## What Changes

- Добавить локальный FastAPI backend.
- Добавить `POST /v1/messages`.
- Добавить `GET /v1/profiles` для получения доступных prompt profiles.
- Добавить `PUT /v1/users/{telegram_user_id}/profile` для изменения default-профиля пользователя.
- Добавить `GET /health`.
- Загружать только заранее подготовленные immutable LadybugDB artifacts.
- Читать nodes и relations напрямую из `.lbdb` через read-only backend reader.
- Выбирать database artifact по `MVP_V2_DATABASE_NAME`, без автоматического выбора самой новой базы.
- Не добавлять ingestion и database build workflow в backend.
- Переиспользовать `SearchService`, `ChatService`, prompt templates,
  citation rendering и существующие Ollama/Ladybug adapters.
- Добавить временный allowlist Telegram user IDs в .env
- Записывать каждый запрос, готовый ответ, ошибки, prompt version,
  database name, database build version, model metadata и latency в JSONL.
- Добавить один переиспользуемый Telegram polling adapter.
- Добавить выбор prompt profile пользователем в Telegram; выбранный профиль
  становится профилем по умолчанию для этого пользователя до следующего изменения.
- Использовать идентификаторы из `application/rag/agent/prompts.py`.
  Для chat pipeline текущий доступный и default `profile_id` — `support-v1`.
- После ответа показывать в Telegram шкалу оценки от 1 до 5.
- Позволить пользователю необязательно написать, что он хотел получить
  или что нужно улучшить, с возможностью пропустить комментарий.
- Передавать feedback в backend через `POST /v1/feedback` и связывать его
  с исходным ответом через `request_id`.
- Передавать Telegram IDs и `source_message_id`.
- Передавать `conversation_id`, равный Telegram `chat_id`, но не добавлять
  постоянную историю диалога в immutable LadybugDB.
- Ограничить одновременную обработку 6 запросами и добавить
  per-model concurrency limit.
- Добавить Python-модуль `analytics/chat_logs.py` на DuckDB для анализа JSONL.
- Добавить стандартные отчёты по дням, статусам, latency, ошибкам,
  `profile_id` и Telegram-пользователям.
- Добавить отладочный запрос по `request_id`, который возвращает вопрос,
  ответ, использованный `profile_id`, prompt version и полный rendered prompt.
- Сохранять полный rendered prompt один раз в `prompt_traces.jsonl`,
  а в остальных логах хранить только `request_id`, `profile_id`, version и hash.
- Не использовать DuckDB в критическом пути обработки вопроса.
  FastAPI и Telegram работают напрямую с JSONL-аудитом.
- Добавить тесты чтения JSONL и отладки запроса через DuckDB.
- Добавить `docker-compose.yml` в стандартном Compose-формате для запуска backend,
  Telegram polling и Redis через Podman как отдельных сервисов.
- Сделать timeout локальных Ollama-запросов явной bounded-настройкой с безопасным default.
- Обрабатывать Telegram `/start` отдельным transport handler без запуска RAG.
- Provision-ить Ladybug `vector` extension на этапе image build и загружать его read-only в runtime.
- Добавить безопасный `make clean`, удаляющий Compose containers/network без удаления persistent data.
- Передавать backend и Telegram только необходимые secrets и configuration;
  не встраивать Telegram token в image.
- Подключать подготовленные `.lbdb` artifacts к backend в read-only режиме;
  `runtime/dumps/` не является runtime-зависимостью gateway.
## Capabilities
### New Capabilities

- `chat-gateway`: локальный API для отправки вопроса и получения ответа от RAG.
- `telegram-polling-adapter`: Telegram-бот, который принимает вопрос,
  вызывает backend и отправляет пользователю готовый ответ.
- `chat-audit-log`: сохранение запросов, ответов, ошибок и feedback в JSONL
  с последующим анализом через DuckDB.
- `local-container-runtime`: воспроизводимый и безопасный Podman Compose workflow
  для build, readiness, диагностики, очистки containers и сохранения runtime data.

 ### Modified Capabilities

- Нет. Существующая RAG-логика и Streamlit workflow не изменяются.

 ## Impact
- Добавится локальный FastAPI backend.
- Добавится отдельный Telegram polling процесс.
- Добавятся JSONL-файлы `backend.jsonl`, `telegram.jsonl`, `errors.jsonl`, `prompt_traces.jsonl` и `feedback.jsonl`.
- Добавится Python-модуль `analytics/chat_logs.py` для анализа JSONL через DuckDB.
- Потребуются зависимости FastAPI, Uvicorn, Telegram client, DuckDB и Redis client.
- Потребуются `Containerfile`/`Dockerfile` для backend и Telegram и `docker-compose.yml`, совместимый с Podman Compose.
- Существующие Streamlit, RAG pipeline, LadybugDB schema и ingestion изменяться не будут.
