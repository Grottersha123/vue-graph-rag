# Spec Delta

## Purpose

Telegram adapter получает сообщения пользователя, передаёт их в FastAPI backend и отправляет полученный ответ обратно в Telegram.

## ADDED Requirements

### Requirement: Adapter creates request_id before access checks

Telegram adapter SHALL создавать локальный `request_id` при получении каждого Telegram update до проверки allowlist. Этот ID SHALL использоваться в Telegram audit event и передаваться в backend для разрешённых сообщений.

#### Scenario: Пользователь не входит в allowlist

- **WHEN** adapter получает update от неразрешённого пользователя
- **THEN** adapter записывает событие отказа с локальным `request_id`, не вызывает backend и не оставляет `request_id` пустым

#### Scenario: Пользователь входит в allowlist

- **WHEN** adapter получает update от разрешённого пользователя
- **THEN** adapter сохраняет локальный `request_id` в transport log и передаёт тот же ID в `POST /v1/messages`

### Requirement: Adapter maps Telegram chat_id to conversation_id

Telegram adapter SHALL передавать Telegram `chat_id` в API как `conversation_id` для каждого сообщения.

#### Scenario: Пользователь отправляет сообщение в Telegram chat

- **WHEN** adapter отправляет вопрос в `POST /v1/messages`
- **THEN** JSON request содержит `conversation_id`, равный исходному Telegram `chat_id`

### Requirement: Adapter forwards an allowed text message

Система SHALL передавать текстовое сообщение разрешённого пользователя в FastAPI backend.

#### Scenario: Разрешённый пользователь отправил вопрос

- **WHEN** Telegram polling получает текстовое сообщение от пользователя из allowlist
- **THEN** adapter отправляет вопрос в backend и передаёт пользователю готовый ответ

#### Scenario: Пользователь отсутствует в allowlist

- **WHEN** сообщение поступает от пользователя, которого нет в allowlist
- **THEN** adapter не вызывает backend и отправляет сообщение об отказе в доступе

### Requirement: Telegram adapter renders profiles from backend metadata

Telegram adapter SHALL получать список доступных profiles через `GET /v1/profiles` и строить Telegram buttons из полученных `profile_id`, без hardcoded списка профилей.

#### Scenario: Adapter builds the profile selection buttons

- **WHEN** backend возвращает список доступных profiles
- **THEN** adapter показывает пользователю кнопку для каждого `profile_id`

#### Scenario: Profile list is unavailable

- **WHEN** adapter не может получить список profiles
- **THEN** adapter не показывает устаревший список и сообщает о временной недоступности выбора профиля

### Requirement: Telegram adapter changes the user's default profile through the backend

Telegram adapter SHALL предоставлять пользователю выбор доступного `profile_id` из существующего chat prompt registry и отправлять выбор в backend через `PUT /v1/users/{telegram_user_id}/profile`. Adapter SHALL NOT хранить per-user default profile самостоятельно.

#### Scenario: Пользователь выбрал доступный профиль

- **WHEN** пользователь выбирает доступный `profile_id`
- **THEN** adapter отправляет выбранный `profile_id` в backend, а backend сохраняет его в Redis

#### Scenario: Пользователь меняет профиль

- **WHEN** пользователь выбирает другой доступный `profile_id`
- **THEN** adapter отправляет новый выбор в backend, а backend заменяет сохранённый default

#### Scenario: Пользователь выбрал неизвестный профиль

- **WHEN** пользователь выбирает `profile_id`, которого нет в списке доступных
- **THEN** adapter сообщает об ошибке и не изменяет default-профиль

### Requirement: Telegram offers answer feedback

Telegram adapter SHALL показывать пользователю feedback controls после отправки готового ответа.

#### Scenario: Ответ успешно отправлен

- **WHEN** Telegram adapter отправляет пользователю готовый ответ
- **THEN** adapter показывает шкалу оценки от 1 до 5 и действие для пропуска feedback

### Requirement: User can submit a rating and optional comment

Telegram adapter SHALL передавать оценку и необязательный комментарий в backend через `POST /v1/feedback`, связывая feedback с исходным `request_id`.

#### Scenario: Пользователь выбрал оценку

- **WHEN** пользователь выбирает значение от 1 до 5
- **THEN** adapter отправляет `request_id`, Telegram user ID и rating в backend

#### Scenario: Пользователь написал комментарий

- **WHEN** пользователь отправляет текст после оценки
- **THEN** adapter отправляет comment вместе с `request_id` и rating в backend

#### Scenario: Пользователь пропустил feedback или комментарий

- **WHEN** пользователь выбирает действие пропуска
- **THEN** feedback или комментарий не отправляется, и пользователь может продолжить задавать вопросы

### Requirement: Feedback state does not block the next question

Feedback flow SHALL иметь ограниченное время ожидания и SHALL NOT permanently block new questions from the same user.

#### Scenario: Пользователь не завершил feedback

- **WHEN** пользователь не отправляет оценку или комментарий в течение срока ожидания
- **THEN** pending feedback state expires and the next question is processed normally

### Requirement: Adapter does not execute RAG logic

Telegram adapter SHALL передавать запросы в backend и не выполнять retrieval или generation самостоятельно.

#### Scenario: Telegram adapter processes a question

- **WHEN** adapter получает вопрос пользователя
- **THEN** RAG processing выполняется backend, а adapter только доставляет результат

### Requirement: Adapter handles /start without invoking RAG

The Telegram adapter SHALL register an explicit `/start` command handler before the generic text-message handler.

#### Scenario: Allowed user sends /start

- **WHEN** an allowed user sends `/start`
- **THEN** adapter returns short usage help
- **AND** it does not call `POST /v1/messages`
- **AND** it does not start retrieval or generation

#### Scenario: Disallowed user sends /start

- **WHEN** a user outside the allowlist sends `/start`
- **THEN** adapter applies the existing access-denied behavior
- **AND** it does not call the backend
