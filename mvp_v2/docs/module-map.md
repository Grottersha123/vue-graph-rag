# MVP v2 module map

This map describes the current source tree by stable responsibility. It is a
routing aid, not a duplicate of implementation details.

```text
src/mvp_v2/
  domain/                         pure graph types, schema, indexes and invariants
    base.py                       strict IDs, hashes and shared Pydantic base types
    graph.py                      persisted node and relation models
    snapshot.py                   typed graph aggregate
    schema_registry.py            canonical graph schema
    field_extensions.py           optional-field configuration
    invariant_indexes.py          reusable validation indexes
    invariants.py                 cross-row graph validation

  application/                    use cases and immutable workflow contracts
    graph_snapshot_operations/    ingestion, editing, deletion and relations
    dump_lifecycle/               manifest, validation and publication lifecycle
    rag/preparation/              chunking and explicit model preparation
    rag/retrieval/                active projection and evidence retrieval
    rag/agent/                    prompts, chat, citations and answer rendering
    dto/                          application request/result DTOs
    deletion_orchestration.py     dump-first deletion and replacement switching
    ingestion_contracts.py        ingestion and model-authorization contracts
    json_encoding.py              canonical artifact JSON encoding

  ports/                          narrow protocols consumed by application services

  adapters/                       external technology implementations
    csv/                          deterministic typed CSV codec
    filesystem/                   confined media, dump and runtime-file access
    ingestion.py                  URL, Markdown, HTML, PDF and image extraction
    ladybug.py                    database build, read-only access and ANN retrieval
    ollama.py                     bounded local Ollama transport and model adapter

  streamlit_ui/                   local operator UI and safe click-gated workflows
    app.py                        tab composition and session orchestration
    controller.py                 upload, path and manifest boundary adapters
    logging.py                    local UI logging configuration

  backend/                        authenticated local FastAPI gateway
    app.py                        routes, authentication and exception boundary
    contracts.py                  strict HTTP DTOs
    service.py                    chat/profile/feedback application service
    runtime.py                    settings-based dependency composition
    profiles.py                   Redis-backed prompt profile selection
    audit.py                      structured gateway JSONL events

  telegram_bot/                   Telegram polling transport adapter
    app.py                        commands, questions and feedback handlers
    client.py                     authenticated backend HTTP client
    state.py                      pending-feedback state
    audit.py                      Telegram JSONL events
    runner.py                     aiogram composition and polling lifecycle

  analytics/                      offline DuckDB analysis of gateway JSONL streams
  bootstrap/                      explicit composition and vector provisioning
  graph_fields.json               optional node-field declarations
```

Top-level runtime entry points:

- `streamlit_app.py` starts the local Streamlit UI;
- `python -m mvp_v2.backend` starts the FastAPI gateway;
- `python -m mvp_v2.telegram_bot` starts Telegram polling;
- `notebooks/rag_batch_debug.ipynb` runs production retrieval/chat batch diagnostics.

Tests mirror these responsibilities under `tests/`. Gateway-focused tests live under
`tests/backend/` plus the gateway storage and integration contract files at the test
root.

## Dependency direction

```text
streamlit_ui/backend/bootstrap -> application -> domain
              |                       |
              v                       v
           adapters --------------> ports

telegram_bot -> authenticated backend HTTP API
analytics    -> local gateway JSONL files
```

Domain modules do not import Streamlit, FastAPI, Telegram, filesystem, Ollama, or
Ladybug. Application services depend on narrow ports. Adapters implement external
I/O. Entry-point packages own configuration and composition.

## Data boundaries

- `runtime/mvp_data/` is mutable operator workspace data.
- `runtime/dumps/dump-NNNNNN/` is an immutable validated CSV snapshot.
- `runtime/databases/<name>.lbdb/` is an immutable query artifact containing
  `database.lbug` and `_mvp_v2_manifest.json`.
- Redis contains mutable prompt-profile and feedback state.
- Gateway JSONL streams are local diagnostic data and may contain sensitive prompts
  or bounded tracebacks.
- Database builds and vector provisioning are explicit operations; normal read paths
  do not ingest, build, install extensions, or mutate database artifacts.
