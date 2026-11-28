# Spec Delta

## Purpose

Уточняет безопасный отбор evidence для телефонных данных и обратную совместимость legacy-документов без новых scope metadata.

## ADDED Requirements

### Requirement: Phone numbers are excluded from RAG evidence

The system SHALL exclude evidence containing phone numbers from RAG context and generated answers. This restriction SHALL also apply when the user explicitly asks for an official contact number.

#### Scenario: User asks for an official phone number

- **WHEN** пользователь явно просит официальный телефон или контакт
- **THEN** evidence с телефонными номерами не передаётся в generation context
- **AND** система не возвращает телефон из legacy или metadata-rich документов

### Requirement: Legacy documents remain eligible without scope metadata

The system SHALL NOT exclude a legacy document solely because new retrieval metadata is absent. Existing current-version, active-document and production-policy checks SHALL continue to apply.

#### Scenario: Eligible legacy document has no scope metadata

- **WHEN** current active legacy-документ не содержит новых retrieval metadata и проходит существующие eligibility checks
- **THEN** документ остаётся доступным retrieval
- **AND** отсутствие новых metadata само по себе не является причиной исключения
