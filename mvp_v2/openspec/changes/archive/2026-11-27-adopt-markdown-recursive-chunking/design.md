# Design

## Context

См. `proposal.md`. Текущий `split_text` — собственный character-window algorithm 1200/120; ingestion и backfill используют его, но FAQ evaluation создаёт отдельный `faq_sections` path и ожидает ровно один chunk на FAQ. Эксперимент `chunking_strategies_experiments.ipynb` показал лучший сбалансированный deterministic retrieval у LangChain Markdown recursive 1200/120. `langchain-text-splitters` уже закреплён в проекте.

## Goals / Non-Goals

**Goals:**

- Сохранить один production entry point `split_text` и существующий `ChunkPolicy`/`make_chunks` contract.
- Сделать default splitter структурным, детерминированным и ограниченным 1 200 символами.
- Перевести FAQ evaluation/ingestion на default production path и разрешить несколько chunks на длинный FAQ.
- Доказать миграцию всего combined корпуса в изолированных debug artifacts.

**Non-Goals:**

- Semantic/agentic/parent-child chunking.
- Изменение retrieval reranking, context window, prompts или citation format.
- Новые test cases, зависимости, graph fields или schema version.
- Изменение production dump/DB, `.env` или Telegram deployment.

## Decisions

### Decision: сохранить `split_text`, заменить только реализацию default policy

`split_text(text, policy)` создаёт `RecursiveCharacterTextSplitter.from_language(Language.MARKDOWN)` с `chunk_size=policy.max_characters` и `chunk_overlap=policy.overlap_characters`. Результат дополнительно фильтруется от blank chunks и проверяется на лимит. `make_chunks` остаётся единственным местом построения position/hash/stable ID.

Так ingestion, Streamlit backfill и regeneration получают новое поведение без дублирования. Альтернатива — новый strategy registry — отклонена: сейчас выбран один production algorithm, а registry добавит контракт и ветвление без второго поддерживаемого production варианта.

### Decision: FAQ остаётся отдельным Document, но не отдельным chunking algorithm

Curation exporter по-прежнему делит агрегированный curated файл на FAQ sections и создаёт отдельный source/document с `external_id=faq:<id>`. После этого каждый FAQ preview использует `chunking_strategy="default"`; предположение «один FAQ = один chunk» удаляется. Длинный answer может иметь несколько chunks, а FAQ identity и metadata остаются на Document.

Совместимый `faq_sections` request option пока не удаляется, чтобы не расширять изменение Pydantic API. Он не используется новым combined build и не выбирается автоматически по metadata.

### Decision: evaluation notebook вызывает production splitter

`faq_rag_llm_judge_evaluation.ipynb` не создаёт собственный LangChain splitter. Он импортирует свежий production `split_text`, использует его для documentation preview и default ingestion для FAQ. После изменения notebook выполняется полный module reset, fresh DTO construction и Run All в защищённом external environment.

### Decision: миграция только в debug

От current verified dump/DB создаётся копия snapshot. Для всех выбранных current Markdown versions старые chunk-owned embeddings удаляются вместе со старыми chunks; Documents, versions, metadata, assets и прочие graph data сохраняются. Новые chunks получают embeddings в том же manifest embedding space. Dump, DB и evaluation записываются только под новым `runtime/debug` run directory.

До сборки проверяются число approved FAQ (486), доступность source files, отсутствие дубликатов `external_id`, metadata gate и snapshot validation. После сборки dump повторно загружается, DB manifest/counts проверяются, затем запускается существующая combined evaluation с неизменным gate.

## Risks / Trade-offs

- [Risk] LangChain version меняет separators или merge behavior → dependency закреплена lock-файлом; run metadata фиксирует strategy и параметры, deterministic smoke выполняется в свежем interpreter.
- [Risk] Несколько chunks одного FAQ изменят ranking → combined evaluation отдельно проверяет FAQ cases и judge; FAIL не публикуется.
- [Risk] Повторная нарезка меняет chunk IDs и инвалидирует embeddings → debug migration намеренно пересоздаёт все chunk-owned embeddings; document/version/FAQ IDs не меняются.
- [Risk] Notebook сохраняет stale classes → полный сброс `vue_rag*`, повторный import и construction-path assertions до дорогих операций.
- [Risk] Source недоступен → run останавливается как `NOT VERIFIED`; старые chunks не смешиваются с новыми.

## Migration Plan

1. Реализовать production splitter и выполнить deterministic fresh-interpreter smoke.
2. Обновить combined FAQ notebook на единый production path без запуска дорогих ячеек.
3. Выполнить существующие Ruff, mypy и relevant pytest suites без добавления tests.
4. Запустить notebook pipeline в новый `runtime/debug` run, пересобрав documentation и 486 FAQ.
5. Проверить dump/DB identities, metadata counts и combined quality gate.
6. Остановиться с отчётом; production artifacts и `.env` не изменять.

Rollback для кода — вернуть прежнюю реализацию `split_text`; debug artifacts можно удалить отдельно после review, так как они не активируются.
