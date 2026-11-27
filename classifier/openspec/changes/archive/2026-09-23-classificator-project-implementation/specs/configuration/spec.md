# Spec Delta: Configuration

## Purpose

Определяет загрузку конфигурации и локальный поиск входных данных для classifier pipeline.

## MODIFIED Requirements

### Requirement: Project-root configuration is independent of cwd

The application SHALL resolve `classifier` as the project root from the package location, SHALL load `.env` from that root, and SHALL resolve relative `DATA_DIR`, `LOG_DIR`, `QA_JSON`, `IMAGE_DIR`, `CASES`, `IMG_DESCS` and `RESULTS` paths independently of the process `cwd`.

#### Scenario: Configuration import

- **WHEN** `telegram_classificator.conf` is imported from the project root or from `/tmp`
- **THEN** `PROJECT_ROOT`, `.env` loading and all relative paths resolve to the same `classifier` project paths

#### Scenario: Import from different working directories

- **WHEN** `telegram_classificator.conf` is imported from two supported working directories
- **THEN** relative configuration paths resolve to the same `classifier` project paths

### Requirement: Existing QA input has deterministic fallback discovery

The notebook SHALL prefer an existing configured `QA_JSON` file or directory. If the configured path does not exist, it SHALL recursively discover local `qa.json` files under `DATA_DIR`; the current checkout layout is `classifier/data/students/<student>/qa.json` with neighboring `images/` directories.

#### Scenario: Configured QA_JSON is missing

- **WHEN** `.env` contains `QA_JSON=qa.json` but `classifier/qa.json` does not exist and `DATA_DIR` exists
- **THEN** Cell 3 discovers `qa.json` files under `classifier/data`, prints the fallback root, and does not modify `.env`

#### Scenario: Batch limit and stable case IDs

- **WHEN** Cell 3 discovers multiple student directories with `BATCH_SIZE=30` and a selected `BATCH_INDEX`
- **THEN** it selects the corresponding 30-case range in deterministic file/record order and prefixes batch `case_id` with the student directory while preserving `source_question_id`

### Requirement: Batch artifacts and manifest are resumable

The notebook SHALL write batch-specific cases, image descriptions, results, errors and manifest artifacts. A manifest SHALL record total normalized cases, batch offset, selected count, remaining count, success/error counts and artifact paths.

#### Scenario: Process a later batch

- **WHEN** the user changes `BATCH_INDEX` and reruns the notebook
- **THEN** only that batch is sent to model stages and prior successful batch artifacts are not overwritten

#### Scenario: Inspect failures

- **WHEN** a batch contains malformed, schema-invalid or image-description failures
- **THEN** `errors_batch_*.jsonl` identifies the case/image, stage, error type and bounded diagnostic, while the manifest reports the error count

### Requirement: Attachment paths are rooted at their source QA file

The parser SHALL resolve an attachment `relative_path` against the parent directory of the source `qa.json`, SHALL preserve the original relative path and provenance fields, and SHALL classify the result as existing, missing or unsafe.

#### Scenario: Existing attachment

- **WHEN** `qa_json.parent / relative_path` is an existing file inside the QA directory
- **THEN** the normalized attachment contains `abs_path`, `exists=true`, `path_error=null` and the original metadata

#### Scenario: Missing attachment

- **WHEN** the resolved file does not exist
- **THEN** the attachment contains `exists=false` and an explicit `path_error`, while unrelated records continue

#### Scenario: Unsafe attachment

- **WHEN** `relative_path` resolves outside `qa_json.parent`
- **THEN** the attachment is marked unsafe, no outside file is read, and the path error is logged

### Requirement: Runtime batch limits are notebook-configurable

The notebook SHALL expose `BATCH_SIZE=30`, `BATCH_INDEX`, derived `CASE_OFFSET` and `MAX_WORKERS=4` as editable variables; changing them SHALL NOT require production module or prompt changes. Production defaults SHALL keep `MAX_WORKERS` bounded at 4 unless explicitly overridden.

#### Scenario: User changes the batch index or size

- **WHEN** the user changes `BATCH_INDEX` or `BATCH_SIZE` in Cell 1
- **THEN** the next Cell 3 run selects the corresponding range without changing model or endpoint configuration

#### Scenario: User changes worker count

- **WHEN** the user changes `MAX_WORKERS` to a positive value
- **THEN** image and classification stages use that bound and keep JSONL writes in the main thread

### Requirement: Configuration and progress logs do not expose secrets

The application SHALL log stage progress, durations, counters and errors without logging API keys, full raw chat text or full model payloads.

#### Scenario: Progress is visible

- **WHEN** Cell 3 and Cell 4 run
- **THEN** notebook output and `logs/pipeline.log` show parsing counters, image `started/finished` records with duration, `completed/total`, and classification stage markers
