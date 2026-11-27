# MVP v2 module map

This map routes contributors to the current `vue_rag` package. It describes stable
ownership boundaries rather than every class or function.

```text
src/vue_rag/
  domain/                         graph types, schema, indexes and invariants
    base.py                       strict IDs, hashes and shared Pydantic types
    graph.py                      persisted node and relation models
    snapshot.py                   immutable graph aggregate
    schema_registry.py            canonical tables and schema profile
    field_extensions.py           graph_fields.json validation and loading
    invariant_indexes.py          reusable validation indexes
    invariants.py                 cross-row graph validation
    validation.py                 snapshot validation reports

  application/                    framework-independent use cases and DTOs
    curation/                     FAQ classification, redaction and export
    dto/                          chat, retrieval, deletion and validation DTOs
    graph_snapshot_operations/    ingestion, editing, deletion and relations
    dump_lifecycle/               manifests, validation and publication
    rag/preparation/              chunking and model preparation
    rag/retrieval/                active projection, ANN results and evidence
    rag/agent/                    prompts, generation, citations and rendering
    deletion_orchestration.py     dump-first deletion and database replacement
    ingestion_contracts.py        ingestion and model-authorization contracts
    json_encoding.py              canonical artifact JSON encoding

  ports/                          protocols consumed by application services
    dumps.py                      dump discovery and publication ports
    ingestion.py                  content/model adapter ports
    ladybug.py                    database build and query ports
    media.py                      confined media storage port
    models.py                     shared model operation ports

  adapters/                       external technology implementations
    csv/                          deterministic typed CSV codec
    filesystem/                   dump, media and runtime-file adapters
    ingestion.py                  URL, Markdown, HTML, PDF and image extraction
    ladybug.py                    database build, hydration and ANN retrieval
    ollama.py                     bounded local Ollama transport

  backend/                        authenticated FastAPI gateway
    app.py                        routes, auth and HTTP error boundary
    config.py                     strict environment settings
    contracts.py                  HTTP request/response DTOs
    service.py                    message, profile and feedback operations
    runtime.py                    Ladybug, Ollama, Redis and audit composition
    profiles.py                   Redis-backed prompt-profile state
    audit.py                      JSONL audit and prompt tracing

  telegram_bot/                   aiogram polling adapter
    app.py                        command, message and callback handlers
    client.py                     authenticated backend client
    config.py                     Telegram environment settings
    feedback.py                   feedback callback contracts
    keyboards.py                  inline keyboard construction
    state.py                      Redis-backed pending state
    audit.py                      Telegram audit events
    runner.py                     dependency composition and polling lifecycle

  streamlit_ui/                   local operator UI
    app.py                        tabs and session orchestration
    controller.py                 upload, path and manifest boundaries
    logging.py                    UI logging configuration

  analytics/                      offline DuckDB reports for gateway JSONL
  bootstrap/                      Streamlit composition and vector provisioning
  graph_fields.json               optional node-field declarations
```

## Entry points

Run from the repository root with the `mvp_v2` project environment:

- `python -m vue_rag.backend` — FastAPI gateway;
- `python -m vue_rag.telegram_bot` — Telegram polling;
- `mvp_v2/streamlit_app.py` — Streamlit operator UI;
- `mvp_v2/notebooks/rag_batch_debug.ipynb` — production retrieval diagnostics.

Container startup uses the same `vue_rag.backend` and `vue_rag.telegram_bot` module
entry points.

## Dependency direction

```text
backend / streamlit_ui / bootstrap -> application -> domain
                  |                      |
                  v                      v
               adapters -------------> ports

telegram_bot -> authenticated backend HTTP API
analytics    -> exported gateway JSONL files
```

`domain` does not import FastAPI, Streamlit, Telegram, Ladybug, Ollama, or filesystem
adapters. Application services depend on ports and typed contracts. Adapters own
external I/O. Entry-point packages own configuration and dependency composition.

## Runtime boundaries

- `runtime/databases/<name>.lbdb/` is the read-only gateway query artifact.
- `runtime/mvp_data/` is mutable ingestion workspace data used by operator flows.
- `runtime/dumps/dump-NNNNNN/` contains immutable validated CSV snapshots.
- `runtime/debug/` and `notebooks/` contain offline diagnostics and evaluation data.
- Redis stores mutable profile, feedback and Telegram state.
- Gateway JSONL files can contain sensitive prompts or bounded tracebacks.

The deployed gateway reads the selected database artifact. It does not read source
workspace files, dumps, notebooks, or evaluation datasets while answering messages.
Database builds, ingestion and vector provisioning remain explicit operator actions.
