# Proposal

## Why

Production по-прежнему режет текст посимвольно и может начинать overlap внутри слова, хотя эксперимент показал лучший retrieval у Markdown-aware recursive strategy. Тот же путь должен применяться к документации и curated FAQ, иначе единая база будет содержать несовместимо подготовленные chunks.

## What Changes

- Заменить default production chunking на детерминированный Markdown-aware recursive split с лимитом 1 200 символов и overlap 120.
- Сохранить существующий контракт `Chunk`, стабильное построение ID и общий ingestion/backfill path для документации и FAQ.
- Пересобрать в изолированном `runtime/debug` chunks, embeddings, dump и LadybugDB для всего текущего корпуса, включая 486 approved FAQ.
- Проверить metadata FAQ/documentation, retrieval, citations и существующий combined FAQ evaluation без изменения production dump, active DB или `.env`.
- Новые test cases не добавлять; использовать существующие suites и свежий runtime smoke.

## Capabilities

### New Capabilities

- `production-markdown-chunking`: единый детерминированный Markdown-aware chunking contract для документации, FAQ и plain-text fallback с изолированной проверкой миграции.

### Modified Capabilities

Нет.

## Impact

Изменяются `application/rag/preparation/chunking.py`, использующие его ingestion/backfill flows и документация chunking contract. Используется уже установленный `langchain-text-splitters`; новые зависимости не добавляются. Новые debug dump/DB и evaluation artifacts создаются только под `runtime/debug`; production artifacts и настройки остаются неизменными.
