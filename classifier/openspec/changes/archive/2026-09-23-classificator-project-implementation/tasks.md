# Tasks: Реализация проекта classificator

## 1. Input and configuration

- [x] 1.1 Определить `classifier` root в `conf.py` и загружать `.env` из root; URL и model values не менять.
- [x] 1.2 Сделать relative config paths независимыми от `cwd`; для attachment использовать `qa_json.parent`.
- [x] 1.3 Добавить fallback discovery под `DATA_DIR`, если настроенный `QA_JSON` отсутствует; текущий layout — `classifier/data/students/*/qa.json`.
- [x] 1.4 Добавить containment check для `relative_path` и явный статус missing/unsafe.
- [x] 1.5 Задать в notebook `BATCH_SIZE=30`, `BATCH_INDEX`/`CASE_OFFSET` и `MAX_WORKERS=4`; batch variables должны изменяться без правок production-модулей.

## 2. QA parser

- [x] 2.1 Нормализовать root metadata, student metadata, question fields, answers, statuses, closure fields и source fields.
- [x] 2.2 Сохранить роли и тексты question/answers/context без потери исходной provenance.
- [x] 2.3 Включить attachments из question, context и `answers[].attachments`.
- [x] 2.4 Сохранить original attachment metadata, `visual_summary`, `relative_path`, resolved path и error status.
- [x] 2.5 Добавить deterministic `limit`/`offset`, чтобы первый запуск обрабатывал только первые 30 обращений.
- [x] 2.6 Записывать `cases.jsonl` и диагностировать malformed records через logging.
- [x] 2.7 Для batch discovery добавлять student-directory prefix к `case_id`, сохраняя исходный `source_question_id` и избегая collision между разными `qa.json`.

## 3. Local image descriptions

- [x] 3.1 Использовать существующий local OpenAI-compatible client и текущие `.env` URL/model values.
- [x] 3.2 Для каждого unique resolved file выбранных cases строить detailed description ровно из 5 предложений; если это инструкция/screenshot/document, явно называть тип фотографии, экран/документ и содержание инструкции.
- [x] 3.3 Кэшировать descriptions по resolved path; переиспользовать результат для повторных references.
- [x] 3.4 Сохранять рядом исходный `visual_summary`, новый `detailed_description`, provenance и `desc_error`.
- [x] 3.5 Запускать image description tasks через `ThreadPoolExecutor(max_workers=4)` с deduplication до submit.
- [x] 3.6 Записывать `image_descriptions.jsonl` только из main thread; ошибки одного изображения не останавливают pipeline.

## 4. Classification and logging

- [x] 4.1 Исправить package import в `agent.py` на существующий `promt.py`; сохранить model/endpoint values и добавить явный classifier JSON template.
- [x] 4.2 Настроить central file/stream logging с stage, item id, error и traceback; исключить secrets и raw chat text.
- [x] 4.3 Ограничить `tenacity` повтором пустого/невалидного model response; `OpenAIError` не retry-ить и логировать final failure сразу.
- [x] 4.4 Передавать в classifier image context каждого выбранного вопроса: path/provenance, `visual_summary`, `detailed_description`/`desc_error`; сохранять role в artifacts.
- [x] 4.5 Записывать `results.jsonl` с classification, generated RAG questions, answers, confidence и sources; запись выполнять из main thread.
- [x] 4.6 Останавливать pipeline при API/network/auth ошибке vision или text endpoint-а, чтобы пользователь мог проверить URL/model; локальные file/record errors сохранять per-item.
- [x] 4.7 Добавить progress logging и `tqdm` bars для image/case processing с длительностью image calls, `completed/total`, parsing counters и classification stage markers; не выводить raw chat text и secrets.
- [x] 4.8 Нормализовать flat classification JSON в `CaseResult.classification`; при missing required fields обработать `BadJSON`/Pydantic mismatch через bounded retry и per-case validation error; полный raw model payload не логировать; сохранять bounded redacted `model_response_preview` до 4000 символов.

## 5. Notebook

- [x] 5.1 Убрать absolute machine-specific path и найти root проекта переносимым способом.
- [x] 5.2 Сделать `importlib` helper с явным порядком: `conf` → `qa_parser`/`image_describer`/`promt` → `agent`.
- [x] 5.3 Добавить cells для single `QA_JSON` и batch discovery под `DATA_DIR` (`classifier/data/students/*/qa.json`) без вызова chat services; batch добавляет student prefix к `case_id`.
- [x] 5.4 Показать counts, errors и side-by-side `visual_summary`/`detailed_description`.
- [x] 5.5 Показать и передать `BATCH_SIZE=30`, `BATCH_INDEX`, derived `CASE_OFFSET` и `MAX_WORKERS=4`; значения можно менять прямо в notebook.
- [x] 5.6 Не изменять notebook prompt cells; обновить `SYSTEM_PROMPT` только явным JSON output template, не меняя taxonomy semantics, модели и URL.

## 6. Verification

- [x] 6.1 В свежем interpreter проверить imports и root/path resolution из двух `cwd`.
- [x] 6.2 На batch из 30 реальных cases проверить все три attachment locations, image provenance и попадание image context в classifier input.
- [x] 6.3 Проверить bounded concurrency: максимум 4 worker-а и корректный JSONL при controlled failure.
- [x] 6.4 Проверить deduplication и отсутствие необработанных/missing paths для выбранных cases.
- [x] 6.5 Проверить logging/error artifacts на controlled local failure без внешнего API.
- [x] 6.6 Проверить, что изменён только JSON output template в `SYSTEM_PROMPT`; taxonomy semantics, prompt cells, model names и endpoint values сохранены.
- [x] 6.7 Запустить Ruff для изменённых Python-файлов и `git diff --check`.
- [x] 6.8 Controlled API failure должен остановить image/classification stage и не маскироваться per-item error row.
- [x] 6.9 Controlled schema mismatch (`classification` missing) должен дать bounded `BadJSON` retry, per-case validation error и bounded `model_response_preview`.


## 7. OpenSpec artifacts

- [x] 7.1 Добавить `openspec/project.md` со stack, data layout, module map, conventions и `/tmp` verification workflow.
- [x] 7.2 Добавить MODIFIED delta `specs/configuration/spec.md` с root/env resolution, DATA_DIR fallback, containment statuses, limits и progress logging.
- [x] 7.3 Добавить MODIFIED delta `specs/classification-output/spec.md` с image context, flat JSON normalization, `BadJSON` и fatal `OpenAIError` behavior.
- [x] 7.4 Добавить acceptance/verification section с dataset fixtures, 30-case batch, bounded concurrency и protected contract checks.


## 8. Batch processing and error inventory

- [x] 8.1 Build one deterministic normalized manifest for all 1944 cases and select a batch by `BATCH_INDEX`/`CASE_OFFSET`/`BATCH_SIZE=30`.
- [x] 8.2 Write batch-specific cases, image descriptions, results, errors and manifest artifacts without overwriting successful batches.
- [x] 8.3 Add classification `errors_batch_*.jsonl` rows with stage, case ID, error type, validation details and bounded response preview.
- [x] 8.4 Add image error inventory with relative path, source role, case ID and `desc_error`.
- [ ] 8.5 Verify the final short batch and summarize unprocessed/error cases from manifests.
