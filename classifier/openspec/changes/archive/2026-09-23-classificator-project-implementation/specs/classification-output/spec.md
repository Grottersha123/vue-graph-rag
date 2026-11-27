# Spec Delta: Classification Output

## Purpose

Определяет контракт между локальным text model response, существующими DTO из `promt.py` и JSONL classification/RAG artifacts.

## MODIFIED Requirements

### Requirement: Existing classifier contract remains stable

The application SHALL use the existing `promt.py` taxonomy semantics and Pydantic DTOs, and SHALL include an explicit JSON output template in `SYSTEM_PROMPT`. The template SHALL express insufficient answers through `classification.answerability="needs_human"` and SHALL represent `sources` as strings. The change SHALL NOT alter DTO fields, model names or endpoint values. Every successful result SHALL validate as the existing `CaseResult`.

#### Scenario: Valid classification

- **WHEN** the implementation diff is reviewed and the model returns a valid classifier response
- **THEN** taxonomy semantics, DTO fields, configured model names and endpoint values remain unchanged, `SYSTEM_PROMPT` contains the explicit JSON template, and the result validates as `CaseResult`

#### Scenario: Review protected contract

- **WHEN** the implementation diff is reviewed
- **THEN** only the explicit JSON output template may differ in `SYSTEM_PROMPT`; taxonomy semantics, configured model names and endpoint values remain unchanged

### Requirement: Image-aware case context is passed to the classifier

The classifier SHALL receive the question turns, answers, context and all related image occurrences from question, context and answer attachments. Each image context item SHALL preserve `relative_path`, provenance role, `visual_summary` and `detailed_description` or `desc_error`.

#### Scenario: Case contains image evidence

- **WHEN** a selected case contains a question, context or answer attachment
- **THEN** `build_case_message` includes the image path, both summaries and the original attachment role in the JSON user message

#### Scenario: Detailed description is unavailable

- **WHEN** an image has a missing/read error or no local description
- **THEN** the classifier still receives `visual_summary` and the image error status; no visual evidence is silently removed

### Requirement: Flat valid classification JSON is normalized

The adapter SHALL accept a flat JSON response containing all required classification fields (`category`, `intent`, `urgency`, `sentiment`, `resolution`, `answerability`) and SHALL move those fields into the nested `CaseResult.classification` object before Pydantic validation.

#### Scenario: Model returns flat classification

- **WHEN** the response contains required classification fields at the JSON top level and optional `questions`
- **THEN** the adapter produces a valid `CaseResult` with the fields under `result.classification`

#### Scenario: Model returns already nested classification

- **WHEN** the response already contains top-level `classification`
- **THEN** the adapter validates it without changing its structure

### Requirement: Invalid schema responses are bounded and observable

The adapter SHALL convert Pydantic schema mismatch into `BadJSON`, SHALL retry invalid response format a bounded number of times, and SHALL write a per-case error after exhaustion with a bounded redacted `model_response_preview` of up to 4000 characters, without logging the full raw model payload.

#### Scenario: Missing classification object

- **WHEN** a response contains only an image/category object and lacks required classification fields
- **THEN** the adapter raises `BadJSON` with validation details after bounded retry, writes the case error, and continues unrelated cases

### Requirement: API endpoint failures are fatal

The adapter SHALL NOT retry `OpenAIError`. A vision or text endpoint/auth/network failure SHALL raise a fatal pipeline error so the user can inspect URL and model settings.

#### Scenario: Text endpoint failure

- **WHEN** the text client raises `OpenAIError`
- **THEN** classification stops, pending work is cancelled where possible, and no misleading successful result is written

#### Scenario: Vision endpoint failure

- **WHEN** the vision client raises `OpenAIError`
- **THEN** image description stops before classification output is written and the error points the user to the local vision URL/model configuration

### Requirement: Batch errors are inspectable

The classification stage SHALL write `errors_batch_*.jsonl` for non-fatal per-case failures with `case_id`, stage, error type, validation details and bounded `model_response_preview` when available. Fatal endpoint failures SHALL stop the current batch without masquerading as completed results.

#### Scenario: Inspect schema failures after a batch

- **WHEN** a case exhausts `BadJSON` retries
- **THEN** its error row is available without scanning full logs, and successful cases remain inspectable in the batch results file

### Requirement: Structured output is linked to provenance

The classification stage SHALL write one JSON object per input case to `results.jsonl` containing the validated classification, canonical questions, answers, confidence, sources, `case_id` and image descriptions used.

#### Scenario: Successful case

- **WHEN** a case receives a valid `CaseResult`
- **THEN** its JSONL row is linked to the stable batch `case_id` and preserves source/image context needed for inspection

#### Scenario: Parallel classification output

- **WHEN** multiple cases are classified with bounded workers
- **THEN** workers return per-case results and the main thread writes valid JSONL in input order
