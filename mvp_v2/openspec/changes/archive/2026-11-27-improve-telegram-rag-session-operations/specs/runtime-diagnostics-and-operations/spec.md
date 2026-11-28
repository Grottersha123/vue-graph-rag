# Spec Delta

## Purpose

Предоставляет безопасную конфигурацию evidence и logging, а также повторяемый экспорт gateway logs для локальной диагностики и контейнерной эксплуатации.

## ADDED Requirements

### Requirement: Evidence limit is configured and bounded

The system SHALL expose a positive bounded evidence limit through `.env.example`, validate it at startup and apply it consistently to retrieval and prompt construction.

#### Scenario: Invalid evidence limit
- **WHEN** значение отсутствует, не является целым или выходит за допустимый предел
- **THEN** startup завершается понятной configuration error без запуска RAG

### Requirement: Runtime logging level is explicit

The runtime SHALL accept `ERROR` and `DEBUG` logging modes, default to `ERROR`, and redact tokens, authorization headers and secret-file contents in both modes.

#### Scenario: Debug mode in a container
- **WHEN** logging mode is `DEBUG` и сервис обрабатывает запрос
- **THEN** в контейнере видны диагностические request/provider события без секретов и полных credentials

### Requirement: Logs can be exported through a supported operation

The system SHALL provide a documented export operation that collects only allowed gateway logs, creates an access-restricted artifact and reports failures explicitly.

#### Scenario: Successful log export
- **WHEN** оператор запускает export при доступном log volume
- **THEN** создаётся архив с логами без секретов, с ограниченными правами, и выводится его путь
