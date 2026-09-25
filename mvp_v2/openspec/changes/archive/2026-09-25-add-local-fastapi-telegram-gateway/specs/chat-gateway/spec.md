# Spec Delta

## Purpose

Локальный FastAPI backend принимает вопрос от клиента и возвращает готовый
текстовый ответ существующего RAG pipeline. Ingestion и сборка БД в backend
не входят: используются только заранее подготовленные immutable
LadybugDB artifacts.


## ADDED Requirements

### Requirement: Health endpoint
Система SHALL предоставлять `GET /health` для проверки доступности backend.

#### Scenario: health check
- **WHEN** клиент вызывает `GET /health`
- **THEN** backend возвращает статус 200 и признак готовности

### Requirement: Reuse of existing RAG logic

Система SHALL переиспользовать `SearchService`, `ChatService`, prompt templates, citation rendering и существующие Ollama/Ladybug adapters.

#### Scenario: Backend формирует ответ через существующий pipeline

- **WHEN** backend обрабатывает валидный вопрос
- **THEN** retrieval и generation выполняются существующими RAG-компонентами без отдельной Telegram-реализации RAG

### Requirement: Gateway uses Telegram chat_id as conversation_id

Система SHALL принимать `request_id` и `conversation_id` в запросе `POST /v1/messages`. Для Telegram adapter `request_id` создаётся на границе adapter, а `conversation_id` SHALL быть равен Telegram `chat_id`. Backend сохраняет переданный `request_id` без замены и может использовать оба идентификатора для correlation и audit, но SHALL NOT сохранять постоянную историю диалога в immutable LadybugDB. Для служебных endpoints без сообщения пользователя эти поля могут отсутствовать.

#### Scenario: Telegram sends a message

- **WHEN** backend получает запрос с `conversation_id`, равным Telegram `chat_id`
- **THEN** backend сохраняет этот идентификатор в correlation и audit metadata и обрабатывает вопрос без записи conversation history в LadybugDB

### Requirement: Backend returns an answer for a valid question

Система SHALL принимать вопрос через `POST /v1/messages` и возвращать готовый текстовый ответ.

#### Scenario: Пользователь отправил обычный вопрос

- **WHEN** backend получает корректный запрос от разрешённого пользователя
- **THEN** backend вызывает RAG pipeline и возвращает готовый ответ

#### Scenario: Запрос имеет неверный формат

- **WHEN** backend получает запрос без обязательного текста вопроса
- **THEN** backend возвращает ошибку валидации и не вызывает RAG pipeline

### Requirement: Backend exposes available prompt profiles

Система SHALL предоставлять `GET /v1/profiles`, который возвращает список доступных `profile_id` из `PROMPT_TEMPLATES` и global default profile. Endpoint SHALL возвращать только идентификаторы и metadata, но не полный текст prompt.

#### Scenario: Получение списка профилей

- **WHEN** Telegram adapter вызывает `GET /v1/profiles`
- **THEN** backend возвращает доступный `support-v1` и помечает его как default

#### Scenario: В RAG-код добавлен новый профиль

- **WHEN** новый `PromptTemplate` добавлен в `PROMPT_TEMPLATES`
- **THEN** он появляется в ответе `GET /v1/profiles` без отдельного hardcoded списка в Telegram adapter

### Requirement: Backend resolves the user's prompt profile

Система SHALL использовать проверенный `profile_id` для текущего вопроса. `profile_id` SHALL соответствовать имени `PromptTemplate` из `PROMPT_TEMPLATES` в существующем RAG-коде. Если `profile_id` передан в запросе, backend SHALL использовать его. Если `profile_id` не передан, backend SHALL прочитать per-user default из Redis по `telegram_user_id`; если сохранённого значения нет, SHALL использовать `DEFAULT_PROMPT_TEMPLATE`. Начальный доступный и default `profile_id` — `support-v1`.

#### Scenario: Запрос содержит доступный profile_id

- **WHEN** backend получает вопрос с доступным `profile_id`
- **THEN** RAG pipeline формирует ответ с использованием выбранного профиля

#### Scenario: Запрос не содержит profile_id

- **WHEN** backend получает вопрос без `profile_id` от пользователя с сохранённым default-профилем
- **THEN** backend читает профиль пользователя из Redis и использует его для ответа

#### Scenario: У пользователя нет сохранённого профиля

- **WHEN** backend получает вопрос без `profile_id` от пользователя без сохранённого выбора
- **THEN** backend использует `support-v1`

#### Scenario: Запрос содержит неизвестный profile_id

- **WHEN** backend получает вопрос с `profile_id`, которого нет в конфигурации
- **THEN** backend возвращает ошибку валидации и не вызывает RAG pipeline

### Requirement: Backend updates the user's default profile

Система SHALL принимать `PUT /v1/users/{telegram_user_id}/profile`, проверять `profile_id` и сохранять его в Redis как default для указанного пользователя.

#### Scenario: Пользователь выбирает доступный профиль

- **WHEN** backend получает доступный `profile_id` для Telegram user ID
- **THEN** backend сохраняет `telegram_user_id` и `profile_id` в Redis

#### Scenario: Пользователь выбирает неизвестный профиль

- **WHEN** backend получает `profile_id`, которого нет в `PROMPT_TEMPLATES`
- **THEN** backend возвращает ошибку и не изменяет сохранённый default

### Requirement: Backend limits concurrency per model

Система SHALL применять отдельный настроенный concurrency limit для каждой chat model и SHALL NOT создавать неограниченную очередь запросов для одной модели.

#### Scenario: Достигнут лимит конкретной модели

- **WHEN** limit для выбранной модели уже достигнут
- **THEN** новый запрос к этой модели получает ответ HTTP 503, а запрос к другой модели при наличии свободной capacity может быть обработан

### Requirement: Backend accepts Telegram feedback

Система SHALL принимать `POST /v1/feedback` с `request_id`, Telegram user ID, rating от 1 до 5 и необязательным comment.

#### Scenario: Валидный feedback

- **WHEN** backend получает rating для существующего `request_id`
- **THEN** backend принимает feedback и возвращает успешный результат

#### Scenario: Невалидная оценка

- **WHEN** backend получает rating меньше 1 или больше 5
- **THEN** backend возвращает ошибку валидации и не записывает feedback

### Requirement: Backend limits concurrent requests

Система SHALL одновременно обрабатывать не более шести запросов.

#### Scenario: Достигнут лимит запросов

- **WHEN** шесть запросов уже обрабатываются
- **THEN** новый запрос получает ответ HTTP 503 о временной перегрузке и не ставится в неограниченную очередь

### Requirement: Ollama request timeout is configurable

Gateway SHALL read `MVP_V2_OLLAMA_TIMEOUT_SECONDS`, default it to 120 seconds, reject non-finite, non-positive, or greater-than-600 values during startup, and pass the validated value to the local Ollama transport.

#### Scenario: Model responds within the configured timeout

- **WHEN** local generation completes before `MVP_V2_OLLAMA_TIMEOUT_SECONDS`
- **THEN** gateway returns the generated answer

#### Scenario: Model exceeds the configured timeout

- **WHEN** local generation exceeds `MVP_V2_OLLAMA_TIMEOUT_SECONDS`
- **THEN** gateway returns a generic `service_unavailable` response
- **AND** local `errors.jsonl` contains bounded exception diagnostics without exposing them to the client

#### Scenario: Timeout configuration is invalid

- **WHEN** `MVP_V2_OLLAMA_TIMEOUT_SECONDS` is non-numeric, non-finite, non-positive, or greater than 600
- **THEN** backend startup fails with a safe configuration error
