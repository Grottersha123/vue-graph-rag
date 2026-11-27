# Proposal: Реализация проекта classificator

## Why

Проект `classifier` должен на Python обработать уже выгруженные данные поддержки: прочитать `qa.json`, сохранить полезный контекст вопросов и ответов, описать прикреплённые изображения локальной vision-моделью и получить структурированные RAG-примеры. Новые чаты выгружать не нужно.

В текущем наборе найдено 132 файла `qa.json`, 1944 вопроса, 1834 ответа и 2649 ссылок на attachments. Все 2649 `relative_path` разрешаются относительно каталога конкретного `qa.json`; 2355 физических файлов уникальны. Поэтому путь к картинке должен строиться от `qa_json.parent`, а не от общей корневой папки.

Фактическая структура входа в checkout — `classifier/data/students/<student>/qa.json` и соседние каталоги `images/`. Текущее значение `QA_JSON=qa.json` может указывать на отсутствующий файл в корне `classifier`. В этом случае notebook Cell 3 не изменяет `.env`, а безопасно использует `DATA_DIR` как fallback, рекурсивно находит локальные `qa.json` и выбирает из них только первые `BATCH_SIZE=30` обращений.

## What Changes

- Загружать `.env` из корня проекта `classifier`; не менять URL и названия моделей.
- Если настроенный `QA_JSON` отсутствует, искать `qa.json` рекурсивно под `DATA_DIR` (текущий layout: `classifier/data/students/*/qa.json`); не менять `.env` автоматически.
- В notebook Cell 3 строить deterministic manifest, выбирать диапазон `BATCH_SIZE=30` по `BATCH_INDEX`/`CASE_OFFSET` и записывать его в batch-specific `cases_batch_XXXX.jsonl` до image/classification stages.
- При batch discovery добавлять к `case_id` префикс каталога student, чтобы одинаковые локальные `question_id` из разных `qa.json` не перетирали image descriptions и results.
- Обрабатывать весь набор из 1944 cases батчами по 30: `BATCH_INDEX`/`CASE_OFFSET` выбирают диапазон, каждый batch пишет собственные cases/images/results/errors artifacts и manifest, чтобы необработанные cases и причины ошибок можно было найти без перезапуска успешных batch-ей.
- Разделить pipeline на независимые модули:
  - `qa_parser.py` — извлечение вопросов, ответов, метаданных, источников и всех attachments из `qa.json`;
  - `image_describer.py` — описание файлов по `relative_path` локальной моделью ровно в 5 предложениях;
  - `promt.py` — существующий DTO и prompt semantics; добавить явный JSON output template без изменения taxonomy semantics;
  - `agent.py` — классификация и генерация канонических RAG-вопросов/ответов;
  - `run.ipynb` — orchestration через `importlib` для каждого модуля.
- Учитывать attachments из `question`, `context_attachments` и `answers[].attachments`.
- Сохранять исходный `visual_summary` и добавлять к нему `detailed_description`, чтобы notebook показывал оба варианта рядом.
- Перед классификацией прикладывать к контексту каждого вопроса все связанные изображения с обоими summary; картинка должна влиять на формируемые classification, RAG-вопрос и ответ.
- По умолчанию обрабатывать только первые 30 обращений (`BATCH_SIZE=30`); значение `BATCH_SIZE` должно изменяться прямо в notebook без правок production-модулей.
- Запускать независимые описания изображений через `ThreadPoolExecutor(max_workers=4)`; запись JSONL и итоговую агрегацию выполнять безопасно в основном потоке.
- Кэшировать описания по физическому файлу, чтобы повторяющиеся 294 ссылки не вызывали повторную генерацию.
- Логировать этапы, предупреждения и ошибки с traceback в файл и notebook output; ошибка одного файла или вопроса не должна молча теряться.
- Показывать живой progress: какой `qa.json` парсится и сколько cases выбрано, `tqdm` progress для каждого image description и classification case, старт/завершение с длительностью, aggregate `completed/total`, а также старт/завершение classification stage.
- Уточнить image-description prompt: если изображение содержит инструкцию, screenshot инструкции или другой документ, description SHALL назвать, что именно изображено, какой это экран/документ и о чём инструкция, а не только переписать текст.
- При API/network/auth ошибке endpoint-а локальной vision или текстовой модели немедленно останавливать pipeline с понятной ошибкой; URL и model пользователь проверяет вручную. Ошибки чтения файла и отдельного malformed record остаются per-item ошибками.
- Зафиксировать в `SYSTEM_PROMPT` полный JSON template: flat classification fields запрещены, `needs_human` выражается через `classification.answerability`, а `sources` всегда являются строками.
- Если text model возвращает flat JSON с обязательными полями классификации (`category`, `intent`, `urgency`, `sentiment`, `resolution`, `answerability`), нормализовать его в вложенный `CaseResult.classification`. Если обязательные поля отсутствуют (например, пришёл только объект image description), вернуть `BadJSON` (`classifier schema validation failed`), выполнить bounded retry, затем записать per-case error с Pydantic validation details и bounded redacted `model_response_preview` (до 4000 символов), не выводя полный raw model payload.
- Объяснить и корректно ограничить использование `tenacity`: повторять только пустой/невалидный model response, но не retry-ить `OpenAIError`; API failure должен сразу остановить pipeline.
- Исправить package imports и убрать machine-specific абсолютный путь из notebook.

## Capabilities

### New Capabilities

- `qa-to-rag-examples`: из `qa.json` создаётся нормализованный набор обращений с текстом, ответами, контекстом, source metadata и image metadata.
- `local-image-descriptions`: для доступных файлов строится дополнительное подробное описание, сохраняя исходное summary.
- `image-aware-rag-examples`: classification получает текст вопроса, ответы и связанные image summaries, а не только текстовый контекст.
- `jupyter-pipeline`: notebook запускает parser, image descriptions и classification по шагам и показывает промежуточные результаты.

### Modified Capabilities

- `configuration`: `.env` выбирается из корня `classifier`, а relative image paths разрешаются относительно `qa.json`.
- `classification-output`: существующие DTO и taxonomy используются с явным JSON output template; prompt schema может быть уточнён без изменения моделей и endpoint values.

## Impact

- Scope: `classifier/src/telegram_classificator/conf.py`, `qa_parser.py`, `image_describer.py`, `agent.py`, `run.ipynb`, а также при необходимости `classifier/pyproject.toml`.
- `MAX_WORKERS` имеет default `4` и может быть задан в конфигурации; URL и имена моделей остаются без изменений.
- `promt.py` может получить явный JSON output template; taxonomy semantics, DTO fields, модели и endpoint values не меняются.
- `classifier/.env` не выводится и не коммитится; менять в нём можно только согласованные пути после отдельной проверки.
- Внешние Telegram/чат-сервисы не вызываются: вход — только локальные `qa.json` и files по `relative_path`.


## Acceptance / Verification

Change is accepted when all of the following checks pass:

- A fresh interpreter imports the five production modules from both the project directory and `/tmp`; relative paths still resolve to `classifier`.
- With the current data layout, Cell 3 discovers 132 local `qa.json` files under `classifier/data`, builds the deterministic 1944-case manifest, selects exactly `BATCH_SIZE=30` cases for the chosen batch and writes unique prefixed `case_id` values.
- Each batch writes separate cases/image/result/error JSONL artifacts and a manifest containing total, offset, selected count, remaining count and error counts; 1944 cases are reachable through 65 batches of size 30 or a shorter final batch.
- The input fixture remains consistent with the observed dataset: 1944 questions, 1834 answers, 2649 attachment references, 2649/2649 paths resolving from each `qa.json.parent`, and 224 repeated physical paths.
- A real fixture covering question, context and answer attachments preserves roles, provenance, `visual_summary`, and resolved paths; the current selected batch is 30 cases.
- Controlled image execution makes one model call per unique physical path, uses no more than four workers, reports `started/finished` duration and `completed/total`, and writes valid JSONL from the main thread.
- A valid flat classification response is normalized into `CaseResult.classification`; a missing required field produces bounded `BadJSON` retry, a per-case validation error and a redacted `model_response_preview` no longer than 4000 characters.
- A controlled `OpenAIError` from either endpoint stops the relevant stage and prevents misleading output; the user can inspect the configured URL/model.
- Ruff, Python compilation, notebook AST validation and `git diff --check` pass. `promt.py` contains the explicit JSON output template; taxonomy semantics, model names and endpoint values remain unchanged.
