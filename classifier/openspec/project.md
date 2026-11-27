# Project Context: classifier

## Scope

`classifier` is a local Python pipeline that converts existing support exports into structured classification and RAG examples. It does not download Telegram history or call chat services for input.

## Stack

- Python `>=3.11`.
- `openai` for OpenAI-compatible local/text endpoints.
- `pydantic` v2 for the existing DTO contract.
- `python-dotenv` for `classifier/.env`.
- `tenacity` for bounded invalid-response retries only.
- `tqdm` for final output progress.
- Notebook orchestration in `src/telegram_classificator/run.ipynb`.

## Data layout

```text
classifier/
├── .env
├── data/
│   └── students/<student>/
│       ├── qa.json
│       └── images/...
├── src/telegram_classificator/
└── logs/
```

The configured `QA_JSON` may currently be `qa.json` even when `classifier/qa.json` is absent. Cell 3 falls back to recursive discovery under `DATA_DIR`, builds the deterministic 1944-case manifest and processes `BATCH_SIZE=30` cases selected by `BATCH_INDEX`/`CASE_OFFSET`. Batch case IDs receive a student-directory prefix; the original question ID is stored as `source_question_id`.

## Module map

- `conf.py` — project-root `.env`, paths, limits, logging and fatal model error type.
- `qa_parser.py` — one `qa.json` to normalized cases; reads question/context/answer attachments and resolves paths from `qa_json.parent`.
- `image_describer.py` — preserves `visual_summary`, adds local `detailed_description`, explicitly identifies instruction/screenshots/documents, deduplicates physical files, logs timing/progress and stops on vision `OpenAIError`.
- `promt.py` — prompt semantics, explicit JSON output template and Pydantic DTOs. Prompt may be clarified with schema, but taxonomy semantics and DTO fields stay stable.
- `agent.py` — text classification adapter, flat JSON normalization, `BadJSON` handling, fatal text API handling and ordered JSONL output.
- `run.ipynb` — explicit importlib orchestration: setup, imports, Cell 3 input selection, image stage and classification.

## Runtime conventions

- Notebook variables: `BATCH_SIZE=30`, `BATCH_INDEX`, derived `CASE_OFFSET`, `MAX_CASES=1000`, `MAX_WORKERS=4`.
- Cell 6 exposes `AUTO_START_BATCH_INDEX` and `AUTO_BATCH_COUNT`; it skips only completed batches with all artifacts and stops on fatal endpoint errors.
- Relative attachment paths are resolved against the source `qa.json` directory and checked for containment.
- Question, context and answer attachment roles remain in normalized artifacts.
- Image model calls are deduplicated by resolved physical path.
- Workers never write shared JSONL files; the main thread writes batch-specific cases, image descriptions, results, errors and manifest artifacts.
- `tqdm` bars show each image future and classification case. Progress logs include stage, stable case/path identifier, status, counters and duration. Never log API keys, full raw chat text or full model payloads; schema failures may store a bounded redacted `model_response_preview` up to 4000 characters.
- `OpenAIError` from text or vision endpoints is fatal and is not retried. Invalid/empty model response format uses bounded retry; exhausted `BadJSON` becomes a per-case error.
- `promt.py` taxonomy semantics, notebook prompt cells, configured model names and endpoint values are protected contract; `SYSTEM_PROMPT` may contain the explicit JSON output template.

## Verification workflow

Do not modify the project `.venv`. For runtime checks, create an isolated environment under `/tmp`, install the package editable, and run a fresh interpreter from both the project directory and `/tmp`. Use controlled mocks for model calls; do not require live model access for parser, concurrency, schema-normalization or failure-path checks.
