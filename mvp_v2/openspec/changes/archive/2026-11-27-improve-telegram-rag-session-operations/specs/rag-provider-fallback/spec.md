# Spec Delta

## Purpose

Определяет основной Selora RAG provider, безопасный fallback на Gemma и прозрачное сообщение пользователю при временной деградации внешнего провайдера.

## ADDED Requirements

### Requirement: Selora is the primary provider

When Selora URL and API key are configured, RAG SHALL call Selora first for supported generation requests. Credentials MUST be loaded from secret configuration and MUST NOT be logged.

#### Scenario: Selora succeeds
- **WHEN** Selora возвращает валидный ответ
- **THEN** пользователю возвращается ответ Selora без вызова Gemma и с audit identity провайдера

### Requirement: Provider failure falls back to Gemma

On a timeout, transport failure or valid provider error from Selora, the system SHALL try configured Gemma as fallback when it is available. The response MUST retain provider and fallback status.

#### Scenario: Selora is unavailable
- **WHEN** Selora не отвечает или возвращает ошибку провайдера
- **THEN** система выполняет разрешённый fallback на Gemma, пишет причину в audit без секретов и не подменяет результат молча

### Requirement: User is informed about fallback

Telegram SHALL tell the user that a reserve model was used when fallback changes the generation path, without exposing internal error details, URLs or credentials.

#### Scenario: Fallback response is delivered
- **WHEN** ответ успешно создан Gemma после ошибки Selora
- **THEN** Telegram сообщает о временном использовании резервной модели и показывает ответ
