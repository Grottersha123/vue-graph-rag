# production-markdown-chunking Specification

## Purpose

Определяет единый воспроизводимый Markdown-aware chunking contract для документации и curated FAQ и безопасную проверку пересборки индекса до production-публикации.

## Requirements

### Requirement: Детерминированное структурное разбиение

Система SHALL преобразовывать одинаковый нормализованный непустой текст в одинаковую упорядоченную последовательность непустых chunks, предпочитая границы Markdown и абзацев, с максимальным размером 1 200 символов и настроенным overlap 120 символов. Для plain text и структурного блока длиннее лимита система MUST применять детерминированный fallback и не создавать chunk длиннее лимита.

#### Scenario: Markdown с заголовками и абзацами

- **WHEN** текущая версия документа содержит Markdown-заголовки, списки или разделённые пустой строкой абзацы
- **THEN** система предпочитает эти структурные границы перед посимвольным fallback и создаёт только непустые chunks длиной не более 1 200 символов

#### Scenario: Повторная обработка одинакового текста

- **WHEN** один текст обрабатывается повторно с той же chunking policy
- **THEN** тексты, порядок, позиции, hashes и IDs chunks совпадают

#### Scenario: Длинный plain-text фрагмент

- **WHEN** во входе нет подходящей Markdown или абзацной границы до лимита
- **THEN** система завершает разбиение детерминированным fallback без chunk длиннее 1 200 символов

### Requirement: Единый путь для документации и FAQ

Система SHALL применять один production chunking contract к текущим Markdown-версиям official documentation и approved curated FAQ. Классификация источника, review metadata и `external_id` MUST NOT выбирать другой splitter или изменяться при пересборке chunks.

#### Scenario: Approved FAQ

- **WHEN** документ имеет `knowledge_kind=curated_faq`, `review_status=approved` и `reusable_as_policy=true`
- **THEN** его Markdown question/answer проходит тот же chunking path, а metadata и стабильная FAQ identity сохраняются

#### Scenario: Official documentation

- **WHEN** документ имеет `knowledge_kind=official_documentation`
- **THEN** его текущая Markdown-версия обрабатывается той же policy без изменения document/version identity

### Requirement: Изолированная миграционная проверка

Перед production-публикацией система SHALL уметь пересоздать chunks и их embeddings для всего выбранного текущего корпуса в отдельном debug snapshot, dump и LadybugDB. Проверка MUST сохранять Documents, current DocumentVersions, FAQ/document metadata и non-chunk graph data и MUST NOT менять production dumps, databases или active configuration.

#### Scenario: Полная debug-пересборка

- **WHEN** выбраны текущая verified DB/dump и доступны исходники всех включённых текущих версий и Ollama embedding model
- **THEN** debug artifact содержит заново созданные chunks/embeddings документации и всех 486 approved FAQ, проходит snapshot/database validation и явно связан с исходным dump identity

#### Scenario: Недоступен исходник или embedding model

- **WHEN** хотя бы один обязательный исходник недоступен либо embedding-вызов завершается ошибкой
- **THEN** миграция помечается `NOT VERIFIED`, не подменяет отсутствующие данные старыми chunks и не изменяет production artifacts

### Requirement: Проверка объединённого RAG

Новый debug index SHALL проходить существующую combined documentation/FAQ evaluation с неизменными golden cases, retrieval/generation settings и quality gate. Retrieval evidence MUST сохранять различие `documentation` и `faq`, а generated citations MUST ссылаться на фактически найденные источники.

#### Scenario: Quality gate проходит

- **WHEN** debug DB полностью построена и evaluation завершена без generation errors
- **THEN** отчёт отдельно показывает documentation и FAQ cases, retrieval metrics, judge result и итог существующего quality gate

#### Scenario: Quality gate не проходит

- **WHEN** существующий quality gate получает `FAIL` или метрика регрессирует относительно принятого combined baseline
- **THEN** debug artifact не считается кандидатом на публикацию, а production DB и `.env` остаются неизменными
