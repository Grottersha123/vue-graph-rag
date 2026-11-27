# Jupyter Runtime

## Purpose

Базовые требования к последовательному запуску локального pipeline из notebook.

## Requirements

### Requirement: Notebook orchestrates the local pipeline

The notebook SHALL run local parsing, image description and classification stages in explicit order.

#### Scenario: Pipeline run

- **WHEN** the notebook cells are executed in order
- **THEN** each stage receives the previous stage's local artifacts

### Requirement: QA input is local and complete

The application SHALL read local `qa.json` files and SHALL preserve question text, answer text, roles, timestamps, statuses, source metadata, context and attachment provenance.

#### Scenario: Parse a question with answers and attachments

- **WHEN** a question contains `question_text`, `answers`, `attachments` or `context_attachments`
- **THEN** the normalized record contains each available text, role, metadata and source reference without silently dropping answer attachments

#### Scenario: Parse one QA file

- **WHEN** `QA_JSON` points to one valid file
- **THEN** the parser processes that file without requiring chat service access or additional export formats

### Requirement: Initial batch processing is limited and notebook-configurable

The notebook SHALL start with `BATCH_SIZE=30`, `BATCH_INDEX=0`, derived `CASE_OFFSET=0` and SHALL allow changing these variables in notebook code without editing production modules.

#### Scenario: Initial batch run

- **WHEN** the notebook runs with the default batch settings
- **THEN** only the first 30 cases in deterministic input order are normalized, described and classified into batch-specific artifacts

#### Scenario: User changes the batch

- **WHEN** the user changes `BATCH_INDEX` or `BATCH_SIZE` in the notebook
- **THEN** the next run processes the selected range without changing prompt or model configuration or overwriting prior batch artifacts

### Requirement: Relative image paths are resolved from the QA file

The application SHALL resolve every attachment `relative_path` relative to the directory containing its source `qa.json` and SHALL reject paths escaping that directory.

#### Scenario: Existing attachment

- **WHEN** `relative_path` points to an existing image or rendered page
- **THEN** the record contains its resolved local path and original relative path

#### Scenario: Missing or unsafe attachment

- **WHEN** the target is missing or escapes the QA directory
- **THEN** the item receives an explicit error status, the error is logged, and unrelated items continue

### Requirement: Image evidence is included in each question context

The classification stage SHALL include all image attachments associated with a question, including question, context and answer attachments, together with both available summaries.

#### Scenario: Question has an image

- **WHEN** a normalized case contains an attachment with `relative_path`
- **THEN** the classifier input contains the attachment path/provenance, `visual_summary` and `detailed_description` when available; the attachment role remains preserved in normalized artifacts

#### Scenario: Detailed description is unavailable

- **WHEN** a local file read/format error occurs
- **THEN** the classifier still receives the existing `visual_summary` and the image error status is logged

#### Scenario: Vision API failure

- **WHEN** the vision endpoint returns an `OpenAIError` or cannot be reached
- **THEN** the image stage stops immediately and exposes the URL/model configuration error without starting classification

### Requirement: Existing and local image summaries are both preserved

The application SHALL retain the exported `visual_summary` and SHALL add a local `detailed_description` for each unique existing file when the local endpoint is available.

#### Scenario: Local description succeeds

- **WHEN** a unique image is readable and the local model responds
- **THEN** output stores both summaries, provenance and the generated detailed description

#### Scenario: Local description fails

- **WHEN** a local file read or non-API image call fails
- **THEN** output stores `desc_error`, logs traceback and continues with other images without deleting `visual_summary`

### Requirement: Notebook supports resumable batches

The notebook SHALL expose `BATCH_SIZE=30`, `BATCH_INDEX` and derived `CASE_OFFSET`, and SHALL write batch-specific artifacts plus a manifest and error inventory.

#### Scenario: Run one batch of a large dataset

- **WHEN** the dataset contains 1944 normalized cases and `BATCH_SIZE=30`
- **THEN** the selected batch processes at most 30 cases, writes its own artifact suffix and records remaining cases in the manifest

#### Scenario: Continue after a completed batch

- **WHEN** the user increments `BATCH_INDEX`
- **THEN** the next range is processed without overwriting prior successful batch artifacts

### Requirement: Image descriptions identify the photographed material

The image-description prompt SHALL describe what the image is. When the image is an instruction, instruction screenshot or document, the description SHALL name the photographed screen/document and explain what the instruction is about, not only transcribe visible text.

#### Scenario: Instruction screenshot

- **WHEN** an image contains an instruction or setup screen
- **THEN** the five-sentence description identifies the image type, screen/document and instruction subject

### Requirement: Processing progress is visible per item

The notebook SHALL show `tqdm` progress for each image-description future and each classification case, while logs SHALL retain stage, stable identifier, duration and `completed/total` counters.

#### Scenario: Long local model stage

- **WHEN** image or classification calls take noticeable time
- **THEN** the notebook shows completed progress instead of appearing idle, and the log identifies the current stage and item

### Requirement: Description generation is deduplicated

The image describer SHALL avoid duplicate model calls for the same resolved physical file within one pipeline run.

#### Scenario: Same image appears in several attachments

- **WHEN** one file is referenced more than once
- **THEN** one description result is reused and each attachment keeps its own provenance occurrence

### Requirement: Four-worker execution is bounded

The application SHALL support `ThreadPoolExecutor(max_workers=4)` for independent image-description tasks and SHALL keep file writes and shared aggregation outside worker functions.

#### Scenario: Parallel image descriptions

- **WHEN** multiple unique image files from the selected cases are submitted
- **THEN** no more than four description calls run concurrently, every future produces a result or logged error, and output remains valid JSONL

### Requirement: Errors are observable

The application SHALL log pipeline stages, warnings and exceptions to the configured log file and notebook stream without logging API keys or full raw chat content.

#### Scenario: Per-item failure

- **WHEN** one QA record, image read, or schema validation fails
- **THEN** the error includes stage and stable item identifier, a traceback is available in the log, and the next item is processed

#### Scenario: API failure

- **WHEN** a vision or text endpoint raises `OpenAIError`
- **THEN** the pipeline stops instead of writing a misleading per-item success/error result

### Requirement: Retry is bounded and scoped

The application SHALL use bounded `tenacity` retries only for empty/invalid model responses and SHALL NOT retry `OpenAIError`; API failures SHALL stop the pipeline.

#### Scenario: Transient local model failure

- **WHEN** a model returns empty/invalid JSON or a flat classification response with missing required fields
- **THEN** it is retried a bounded number of times and then emits a logged item error; a valid flat classification is normalized into `CaseResult.classification`

### Requirement: Existing prompt and model contract is stable

The change SHALL NOT modify taxonomy semantics, DTO fields, configured model names or endpoint values; `SYSTEM_PROMPT` MAY add an explicit JSON output template, while notebook prompt cells remain unchanged.

#### Scenario: Review implementation diff

- **WHEN** the change is reviewed
- **THEN** only the explicit `SYSTEM_PROMPT` JSON output template may be added; notebook prompt cells, taxonomy semantics and model configuration remain unchanged

### Requirement: Notebook imports modules explicitly

The notebook SHALL use `importlib` to import `conf`, `qa_parser`, `image_describer`, `promt` and `agent` in dependency order and SHALL support reload in a fresh or reused kernel.

#### Scenario: Fresh notebook kernel

- **WHEN** setup and import cells run in a fresh kernel
- **THEN** all five modules import through the package without `ModuleNotFoundError` or stale top-level imports

### Requirement: RAG examples are structured

The classification stage SHALL write structured JSONL results containing the existing classifier output, generated canonical questions, answers, confidence and source references.

#### Scenario: Classified support record

- **WHEN** a normalized record is sent to the existing classifier
- **THEN** the output is one inspectable JSON object linked to the original `case_id` and sources
