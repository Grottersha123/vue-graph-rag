# Design: Реализация проекта classificator

## Context

Входные данные уже выгружены. Каждый текущий `qa.json` содержит `schema_version`, `review_method`, `student` и массив `questions`. Вопрос содержит `question_text`, `answers`, chat/time/status/source metadata, а также необязательные `attachments` и `context_attachments`. Ответ содержит `answer_text`, `answer_time`, `answered_by` и необязательные attachments. Attachment содержит `relative_path`, `media_kind`, caption, precomputed `visible_text`, precomputed `visual_summary`, provenance fields и `sensitive_data_detected`.

Проверка набора показала:

- 1944 вопроса, 1834 ответа;
- 897 question attachments, 979 context attachments и 773 answer attachments;
- media kinds: `image`, `rendered_pdf_page`, `video_frame`;
- 2649/2649 paths существуют при разрешении от `qa.json.parent`;
- 224 физических paths повторяются от 2 до 3 раз.

Слово `ticket` в проектной терминологии не используется. Нормализованная единица называется `case`/«обращение».

## Goals / Non-Goals

**Goals:**

- Получить из локального `qa.json` полноценный контекст обращения: вопрос, ответы, context, статусы, источники, attachment metadata и image descriptions.
- Передать image context в classification для каждого вопроса, сохраняя связь изображения с ролью `question`, `context` или `answer`.
- Использовать `relative_path` как источник истины для поиска изображения.
- Сохранить оба summary: готовый `visual_summary` из выгрузки и новое `detailed_description` от локальной модели.
- Подготовить JSONL-артефакты для повторного анализа и RAG-примеров.
- Показать в notebook side-by-side результаты и ошибки.
- Загружать каждый модуль через отдельный `importlib` helper после загрузки `conf`.
- Уточнить `SYSTEM_PROMPT` явным JSON output template, не меняя taxonomy semantics и model configuration.

**Non-Goals:**

- Не выгружать Telegram/чат-историю и не подключаться к чатам.
- Не менять Pydantic DTO fields, taxonomy semantics, URL или model names; разрешено добавить в `SYSTEM_PROMPT` явную JSON schema/template.
- Не обучать модель и не строить в этом change vector database.
- Не изменять содержимое `.env` без отдельного согласования.
- Не менять `.venv` и не устанавливать пакеты в него агентом.
- Не печатать API keys, raw chat text или полные sensitive diagnostics в общий лог.

## Decisions

### Decision: one input contract, optional batch discovery

Основной API принимает путь `QA_JSON` к одному файлу. Для текущего набора notebook может собрать список всех `qa.json` под `DATA_DIR` (`classifier/data/students/*/qa.json`), если настроенный `QA_JSON` отсутствует; parser и image describer работают с одним input file за вызов. Это сохраняет простой контракт и позволяет повторить pipeline для одного ученика. Текущий запуск ограничен первыми 30 обращениями (`BATCH_SIZE=30`), чтобы не отправлять сразу весь набор в локальную модель. Значение `BATCH_SIZE` остаётся изменяемым в notebook без редактирования production-модулей. При batch discovery notebook передаёт имя student-каталога как `case_id_prefix`, а исходный вопрос сохраняется в `source_question_id`; это устраняет одинаковые `q_0001` между разными студентами.

### Decision: image root is `qa_json.parent`

Для attachment `relative_path` строится `qa_json.parent / relative_path`. Перед чтением проверяются `resolve()` и containment внутри разрешённого input root, чтобы не допустить path traversal. `IMAGE_DIR` не переопределяет provenance path молча; если он остаётся в `.env`, его значение используется только как явно согласованный fallback.

### Decision: parser preserves source, does not flatten away evidence

Нормализованный record содержит `case_id`, `student`, question/answer turns, question metadata, `source`, `context`, список всех attachments и для каждого `relative_path`, resolved path, media kind, original fields и existence/error status. Тексты из question и answers не смешиваются без указания роли.

### Decision: two-summary image artifact

`image_describer.py` не заменяет готовый `visual_summary`. Он добавляет `detailed_description` и `desc_error`, сохраняя исходный attachment object. Выход `image_descriptions.jsonl` содержит оба поля и provenance. Notebook показывает их рядом для ручной проверки.

Описание выполняется один раз на уникальный resolved file path и кэшируется в памяти/JSONL. Это сокращает повторные локальные вызовы на повторяющихся ссылках. Prompt обязан назвать тип изображения; для инструкции, screenshot или документа он отдельно указывает, что именно изображено, какой это экран/документ и о чём инструкция.

### Decision: image summaries are part of the question context

Для каждого normalized case строится `image_context` из всех его attachments. Для каждого элемента сохраняются `source_role`, `relative_path`, `visual_summary` и `detailed_description` (или `desc_error`). Этот список передаётся в существующий `build_case_message` вместе с текстом вопроса и ответами. Если local description недоступно, `visual_summary` всё равно передаётся. Поэтому вопрос с картинкой не превращается в текстовый вопрос без visual evidence.

### Decision: bounded four-worker execution

Независимые вызовы image describer запускаются через `ThreadPoolExecutor(max_workers=4)`. `MAX_WORKERS` имеет default `4`, но остаётся конфигурируемым. В worker выполняются только чтение одного файла, model call и формирование результата; общий JSONL-файл не изменяется из worker. Main thread собирает `Future` через `as_completed`, пишет результаты и обновляет агрегаты.

Если classification также запускается параллельно, один worker возвращает готовый per-case result, а запись `results.jsonl` выполняется в main thread в порядке входных cases. Logging thread-safe, но каждый log record обязан содержать stage и `case_id`/`relative_path`. Для первого запуска классификация получает только первые `BATCH_SIZE` обращений и только связанные с ними изображения.

### Decision: local model adapter is OpenAI-compatible

Текущий `OpenAI` client и `.env` values для local endpoint/model используются как есть. Для локального endpoint API key может быть dummy value, если конкретный server требует только наличие аргумента. Значения URL и model name не меняются этим change.

### Decision: classifier schema mismatch

`CaseResult` requires a top-level `classification`. If the model returns a flat classification object with all required fields, the adapter moves those fields into `classification` before Pydantic validation. If required fields are missing, such as an image description with only `category`, Pydantic raises `ValidationError`, which the adapter wraps as `BadJSON`. Tenacity retries this bounded response-format failure; after exhaustion the case receives an `error` row with validation details, while other cases continue. The full raw model payload is never logged; a bounded redacted `model_response_preview` up to 4000 characters is stored on schema failure for diagnosis.

### Decision: progress observability

Notebook prints the selected input root, each parsed student `qa.json`, parsed/selected case counters, and image-stage totals before model calls. `tqdm` bars show each image future and classification case as it completes; production loggers additionally emit image start/finish records with relative path and duration, aggregate `completed/total`, and classification stage markers. Logs never include raw chat text, API keys, or full model payloads.

### Decision: tenacity only around remote model calls

`tenacity` — библиотека для retry/backoff. Decorator делает bounded retry только для пустого/невалидного model response. `OpenAIError` не retry-ится: ошибка API, auth, URL или network сразу логируется и останавливает pipeline. Ошибки входного файла, отсутствующего изображения и программные ошибки не должны повторяться бесконечно или скрываться.

После исчерпания попыток API/network/auth ошибка логируется с traceback и поднимается как fatal `ModelAPIError`: pipeline останавливается, чтобы пользователь проверил URL и model. Ошибки чтения файла, отсутствующего изображения и malformed record не относятся к endpoint failure и остаются per-item (`desc_error`/`error`).

### Decision: deterministic batch processing and failure inventory

The 1944 normalized cases are treated as one deterministic manifest ordered by sorted `qa.json` path and source record order. Notebook variables `BATCH_SIZE=30` and `BATCH_INDEX` define the selected range; `CASE_OFFSET` is derived as `BATCH_INDEX * BATCH_SIZE` and may be overridden for targeted reruns. Only the selected cases call image/text models. Every batch writes independent artifacts with a stable suffix (`batch_0000`, `batch_0001`, ...), so successful batches are not overwritten.

Each batch also writes an error inventory. Classification errors include `case_id`, stage, error type, validation details and bounded `model_response_preview` when available. Image errors include case/image provenance and `desc_error`. A batch manifest records total normalized cases, offset, selected count, remaining count, artifact paths, successful count and error counts. API errors remain fatal for the current stage; the manifest and existing successful batch artifacts are not silently replaced.

### Decision: output artifacts

Логические результаты разделяются на batch-specific JSONL:

```text
cases_batch_0000.jsonl                 # нормализованные обращения из qa.json
image_descriptions_batch_0000.jsonl    # оба summary + detailed_description/error
results_batch_0000.jsonl               # classification + generated RAG questions/answers
errors_batch_0000.jsonl                # per-case/per-image failures and diagnosis
manifest_batch_0000.json               # batch range, counts, artifact paths
logs/pipeline.log                      # этапы, warnings, errors и tracebacks
```

Каждая строка содержит идентификатор обращения и достаточную provenance для возврата к исходному `qa.json` и `relative_path`. Batch suffix увеличивается вместе с `BATCH_INDEX`; успешные batch artifacts не перезаписываются следующим batch.

### Decision: notebook is explicit orchestration

Notebook cells выполняются в таком порядке:

1. найти project root и добавить `classifier/src` в `sys.path`;
2. импортировать `conf` через `importlib` и создать directories/logging;
3. импортировать `qa_parser`, `image_describer`, `promt`, `agent` отдельными вызовами helper;
4. прочитать один или несколько `qa.json` и выбрать первые `BATCH_SIZE` обращений;
5. построить/записать cases;
6. построить/записать image descriptions через 4 worker-а только для выбранных cases и показать два summary;
7. передать подготовленные данные в существующий classifier;
8. показать counts, errors и sample output без секретов.

## Project Structure

```text
classifier/
├── .env                         # локальный, не коммитится
├── example.env                 # имена настроек без секретов
├── pyproject.toml
├── openspec/
│   ├── config.yaml
│   └── changes/classificator-project-implementation/
├── data/
│   └── students/<student>/
│       ├── qa.json
│       └── images/...
├── src/
│   └── telegram_classificator/
│       ├── conf.py              # env, paths, logging
│       ├── qa_parser.py         # qa.json -> normalized cases
│       ├── image_describer.py   # local detailed descriptions
│       ├── promt.py              # DTO + explicit JSON output template
│       ├── agent.py              # existing classifier adapter
│       └── run.ipynb            # importlib orchestration
└── logs/                        # generated, ignored
```

## Risks / Trade-offs

- [Risk] В одном checkout много `qa.json` → Mitigation: поддержать single-file contract и явный notebook batch list.
- [Risk] Повторная генерация descriptions дорогая даже локально → Mitigation: cache by resolved path and persist JSONL.
- [Risk] В attachment есть sensitive metadata → Mitigation: preserve it in local artifacts, но не писать raw values в application logs.
- [Risk] Retry маскирует ошибку → Mitigation: retry-ить только invalid model response; `OpenAIError` сразу останавливает pipeline и показывает traceback для проверки URL/model.
- [Risk] Notebook kernel загружает старые module objects → Mitigation: отдельный `importlib` helper и явный reload order: сначала `conf`, затем dependants.

## Migration Plan

1. Согласовать этот change и пути `QA_JSON`/input data.
2. Реализовать root env loading и portable imports.
3. Обновить parser для answer attachments, provenance и parent-relative paths.
4. Обновить image describer для cache, двух summary и per-item errors.
5. Настроить central logging и ограниченный tenacity retry.
6. Обновить `SYSTEM_PROMPT` явным JSON output template и notebook orchestration/importlib cells; prompt cells не менять; добавить notebook variables `BATCH_SIZE=30`, `BATCH_INDEX`, `CASE_OFFSET` и `MAX_WORKERS=4`.
7. Проверить первый batch из 30 cases, затем последовательно расширять `BATCH_INDEX` до полного набора из 1944 cases, анализируя `errors_batch_*.jsonl`.
8. Запустить focused tests, Ruff, fresh-interpreter import check и `git diff --check`.

Rollback — вернуть точечные изменения production modules и notebook cells; входные `qa.json`, images, `.env` и generated artifacts не удаляются.
