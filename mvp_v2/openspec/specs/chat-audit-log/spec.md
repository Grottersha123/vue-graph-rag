# chat-audit-log Specification

## Purpose
Система сохраняет запросы, ответы и ошибки в JSONL-файлы, чтобы историю работы backend и Telegram можно было анализировать через DuckDB.

## Requirements

### Requirement: Backend and Telegram events are logged

Система SHALL записывать события backend, Telegram adapter и ошибки в структурированные JSONL-файлы.

#### Scenario: Успешный запрос

- **WHEN** backend возвращает ответ пользователю
- **THEN** в JSONL сохраняются вопрос, готовый ответ, статус, latency, общий request ID и `conversation_id`

#### Scenario: Ошибка обработки

- **WHEN** backend или Telegram adapter завершается ошибкой
- **THEN** в error JSONL сохраняются request ID, компонент, тип ошибки и статус

### Requirement: Audit records are separated by component

Система SHALL сохранять события backend, Telegram adapter, ошибки, полные prompt traces и feedback в разные JSONL-файлы: `backend.jsonl`, `telegram.jsonl`, `errors.jsonl`, `prompt_traces.jsonl` и `feedback.jsonl`.

#### Scenario: Backend обработал запрос

- **WHEN** backend принимает и обрабатывает вопрос
- **THEN** событие запроса и готового ответа записывается в `backend.jsonl`

#### Scenario: Telegram обработал сообщение

- **WHEN** Telegram adapter получает сообщение или отправляет ответ
- **THEN** событие Telegram записывается в `telegram.jsonl`

#### Scenario: Произошла ошибка

- **WHEN** backend или Telegram adapter завершает операцию ошибкой
- **THEN** ошибка записывается в `errors.jsonl` и содержит общий request ID

#### Scenario: Поймано runtime exception

- **WHEN** backend или Telegram adapter преобразует пойманное exception в безопасную внешнюю ошибку
- **THEN** `errors.jsonl` сохраняет bounded `error` и `traceback` для локальной диагностики, а API и Telegram продолжают возвращать generic error без traceback

#### Scenario: Ошибка не имеет exception context

- **WHEN** система записывает самостоятельный отказ авторизации или validation event без пойманного exception
- **THEN** `error` и `traceback` равны `null`

#### Scenario: Сформирован полный rendered prompt

- **WHEN** backend формирует prompt для генерации ответа
- **THEN** полный prompt записывается один раз в `prompt_traces.jsonl` и связывается с audit records через `request_id`

#### Scenario: Пользователь отправил feedback

- **WHEN** backend принимает оценку или комментарий для ответа
- **THEN** feedback записывается в `feedback.jsonl` и содержит `request_id`, Telegram user ID, rating и необязательный comment

### Requirement: JSONL records use a stable envelope

Каждая JSONL-запись SHALL быть одним UTF-8 JSON object на одной строке и SHALL содержать общий envelope:

- `schema_version` — обязательное число;
- `event_id` — обязательный уникальный идентификатор записи;
- `event_ts` — обязательное UTC ISO-8601 время;
- `component` — обязательное значение `backend` или `telegram`;
- `event_type` — обязательное имя события;
- `request_id` — строка или `null`, если событие не относится к конкретному запросу;
- `conversation_id` — строка или `null`, если событие не относится к Telegram chat;
- `status` — обязательное состояние события.

`request_id` и `conversation_id` SHALL быть заполнены для обычных message, prompt trace и feedback records. Каждое Telegram update, включая отказ по allowlist, SHALL иметь локально созданный `request_id`; `conversation_id` для transport-only update равен Telegram `chat_id`. Для health, profile-list, startup и самостоятельных error events оба поля могут быть `null`. `status` SHALL присутствовать в каждой записи независимо от файла. Допустимые значения зависят от типа файла: backend — `success`, `error`, `rejected`, `overloaded`; Telegram — `received`, `sent`, `failed`, `skipped`; errors — `error`; prompt traces — `captured` или `failed`; feedback — `accepted` или `error`.

File-specific fields SHALL быть следующими:

- `backend.jsonl`: `question`, `answer` когда ответ сформирован, `profile_id`, `prompt_version`, `database_name`, `database_build_id`, `graph_schema_version`, `model_id`, `latency_ms` и retrieval metadata;
- `telegram.jsonl`: Telegram `user_id`, `chat_id`, `source_message_id`, transport action и delivery status;
- `errors.jsonl`: `operation`, `error_code`, safe user-facing `message`, nullable `error`, nullable `traceback` и correlation fields; `component` показывает источник ошибки. Для пойманного exception `error` содержит bounded exception summary, а `traceback` — bounded formatted traceback; для независимой ошибки без exception оба поля равны `null`. Эти поля остаются только в локальном audit-файле, не возвращаются через API/Telegram и не должны содержать tokens или authorization headers;
- `prompt_traces.jsonl`: generation attempt, `profile_id`, `prompt_version`, `database_name`, `database_build_id`, `prompt_hash` и полный `rendered_prompt`;
- `feedback.jsonl`: Telegram user ID, rating от 1 до 5 и nullable `comment`.

#### Scenario: Backend record contains the answer

- **WHEN** backend успешно обрабатывает вопрос
- **THEN** одна JSONL-запись содержит вопрос, готовый ответ, effective `profile_id`, database identity, latency и общий `request_id`

#### Scenario: Telegram record contains transport metadata

- **WHEN** Telegram adapter получает update или отправляет ответ
- **THEN** запись содержит Telegram IDs и transport status без полного rendered prompt

#### Scenario: Prompt trace is stored separately

- **WHEN** backend вызывает generation attempt
- **THEN** полный prompt записывается в `prompt_traces.jsonl` один раз для этого attempt и не дублируется в других JSONL-файлах

#### Scenario: Optional feedback comment is absent

- **WHEN** пользователь отправляет только rating без comment
- **THEN** `feedback.jsonl` содержит `comment: null` или эквивалентное nullable значение

### Requirement: Audit records the selected prompt profile

Система SHALL записывать фактически использованный `profile_id` в audit record вместе с request ID.

#### Scenario: Вопрос обработан с выбранным профилем

- **WHEN** backend обрабатывает вопрос с `profile_id`
- **THEN** audit record содержит имя использованного профиля

#### Scenario: Вопрос обработан с профилем по умолчанию

- **WHEN** пользователь не выбирал профиль для вопроса
- **THEN** audit record содержит имя профиля по умолчанию

### Requirement: Python analytics module provides standard reports

Система SHALL предоставлять Python-модуль на DuckDB для анализа JSONL-файлов и формирования стандартных отчётов.

#### Scenario: Анализ prompt profiles

- **WHEN** аналитический модуль группирует записи по `profile_id`
- **THEN** он возвращает количество запросов для каждого `profile_id`

#### Scenario: Анализ ошибок по компонентам

- **WHEN** аналитический модуль читает `errors.jsonl`
- **THEN** он возвращает количество ошибок по компоненту и типу ошибки

#### Scenario: Анализ пользовательских оценок

- **WHEN** аналитический модуль читает `feedback.jsonl`
- **THEN** он возвращает распределение оценок и среднюю оценку по prompt profile или периоду

### Requirement: Python analytics module supports request debugging

Система SHALL предоставлять запрос по `request_id`, который объединяет события backend, Telegram, ошибки и prompt trace.

#### Scenario: Отладка ответа пользователя

- **WHEN** аналитический модуль получает существующий `request_id`
- **THEN** он возвращает вопрос, ответ, Telegram user ID, `profile_id`, prompt version, latency и полный rendered prompt

#### Scenario: Prompt trace отсутствует

- **WHEN** для существующего `request_id` нет записи в `prompt_traces.jsonl`
- **THEN** модуль возвращает доступные данные и явно указывает, что полный prompt недоступен

### Requirement: Logs can be analyzed with DuckDB

Система SHALL позволять читать JSONL-файлы через DuckDB без изменения исходных логов.

#### Scenario: Анализ количества ошибок

- **WHEN** аналитический запрос читает указанный нами JSONL через DuckDB
- **THEN** запрос возвращает все поля из DuckDB для полного анализа по request_id
