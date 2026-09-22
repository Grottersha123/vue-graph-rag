# Design

## Context

Текущий `application` содержит несколько независимых групп модулей: операции над `GraphSnapshot`, RAG use cases и dump lifecycle. Внешние реализации остаются в `ports/` и `adapters/`; Streamlit composition остаётся в `presentation/`. Proposal фиксирует целевые группы и ограничения изменения.

## Goals / Non-Goals

**Goals:**

- Сгруппировать модули по устойчивым причинам изменения.
- Сохранить имена классов, функций, DTO и поведение use cases.
- Обновить все внутренние импорты без временных compatibility-shims.
- Сделать место для prompt templates однозначным: `rag/agent/prompts.py`.
- Удалить неинформативный контейнер `application/services/`.

**Non-Goals:**

- Изменение ingestion workflow и автоматического RAG preparation.
- Изменение GraphSnapshot, CSV, dump или LadybugDB contracts.
- Переименование `ChatService` в `RagAgent` или создание `agent.py`.
- Изменение model providers, authorization или prompt semantics.

## Decisions

### Decision: разделить application по трём явным контурам

Модули GraphSnapshot operations, RAG и dump lifecycle переносятся в отдельные пакеты. Это предпочтительнее единого `application/services/`, потому что имя пакета должно объяснять причину изменения кода.

Альтернатива — оставить все файлы в корне и только обновить документацию. Она не устраняет импортную и навигационную проблему.

### Decision: `graph_snapshot_operations` не открывает LadybugDB

Пакет содержит use cases, которые читают или изменяют `GraphSnapshot`. CSV, dump и LadybugDB остаются за портами и adapters. `deletion_orchestration.py` остаётся отдельно, потому что координирует несколько внешних эффектов.

Альтернатива — включить orchestration в пакет GraphSnapshot. Это смешало бы чистое изменение snapshot с публикацией dump, rebuild DB и удалением media.

### Decision: RAG agent сохраняет текущие имена реализации

`application/chat.py` переезжает в `rag/agent/chat.py`, а `ChatService` сохраняется. `chat_export.py` переезжает рядом. Prompt templates и builders из `conversation/conf.py` и `conversation/prompting.py` объединяются в `rag/agent/prompts.py`; `conversation/rendering.py` переезжает в `answer_rendering.py`, а `conversation/errors.py` — в `errors.py`.

Альтернатива — сразу ввести `agent.py` и `RagAgent`. Она не подтверждена текущим поведением: код делает retrieval, одну генерацию и ограниченный retry, поэтому такое переименование отложено.

### Decision: dump lifecycle поглощает `services`

`manifests.py`, `publication.py`, `validation.py` и `dump_restamp.py` находятся в `dump_lifecycle/`. `application/services/` удаляется. `json_encoding.py` остаётся отдельно как техническая utility.

Альтернатива — сохранить `services/`. Она не даёт информации о том, какие артефакты обслуживает код.

### Decision: перенос выполняется прямым обновлением импортов

Старые модули-переэкспортёры не создаются. Это предотвращает двойную карту публичных путей и позволяет тестам сразу проверять целевую структуру.

## Risks / Trade-offs

- [Risk] Перенос затронет много импортов в adapters, ports, presentation и tests → Mitigation: выполнять перемещения группами и после каждой группы запускать focused import/tests.
- [Risk] Prompt merge может незаметно изменить текст prompt → Mitigation: сохранить версии шаблонов и покрыть rendered prompt regression tests.
- [Risk] Переезд manifests создаст циклические импорты → Mitigation: менять импорты в dependency order и запускать strict mypy после стабилизации структуры.
- [Risk] Текущие незакоммиченные изменения могут затрагивать те же модули → Mitigation: перед переносом проверить diff/status и не перезаписывать пользовательские изменения.

## Migration Plan

1. Проверить текущие `git status`, diff и import graph.
2. Создать целевые пакеты и переместить модули без изменения тел функций.
3. Обновить imports в ports, adapters, presentation и tests.
4. Объединить prompt modules с сохранением observable output.
5. Удалить пустой `application/services/` и проверить отсутствие старых импортных путей.
6. Обновить module map и OpenSpec references.
7. Запустить focused tests, затем Ruff, mypy и полный pytest.

Rollback выполняется возвратом перемещений и импортов одним сфокусированным изменением; runtime data и published artifacts не затрагиваются.

## Open Questions

Нет.
