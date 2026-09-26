# MVP v2 implementation plan

Status: implemented baseline and historical delivery map. New behavior changes are
planned through OpenSpec. The canonical graph contract is `docs/schema.md`.

## Boundaries

- Code, project configuration, tests, and runtime documentation live under `mvp_v2/`.
- Legacy repository prototypes and their data artifacts are unsupported and are not imported.
- Runtime Python lives in `/tmp/vue-graph-rag-mvp-v2/.venv`; repository-owned `.venv` is not used or modified.
- `.env`, secrets, database artifacts, dumps, logs, and other runtime state remain untracked.
- Existing `.lbdb` artifacts are immutable inputs unless an explicit lifecycle operation builds a replacement.

## Delivered phases

1. Pydantic v2 graph/application/manifest models and executable schema registry.
2. Deterministic typed CSV codec, graph snapshot and invariant validator.
3. Strict current-schema dump publication with 22 canonical tables and schema-profile identity.
4. Text ingestion and deterministic chunking without persisted Section nodes.
5. Image collection ingestion and vision descriptions.
6. Missing/all embedding workflows with the exact nine-field Embedding model.
7. LadybugDB staged publication, one selected embedding identity and retrieval.
8. Deletion preview/cascade, immediate active-DB rebuild and stale-DB blocking.
9. Streamlit UI: Upload, Documents, Relations, Chunks & Embeddings, LadybugDB, Search, Chatbot and Errors.
10. Offline release checks and opt-in external integration/evaluation.

Each completed phase added focused tests and was checked with Ruff, strict mypy,
and the relevant offline pytest scope in the isolated runtime. Future work uses a
separate OpenSpec change with its own acceptance evidence.

## Defaults selected for implementation

- Strict immutable Pydantic v2 models with `extra="forbid"`.
- IDs are nonblank/control-free opaque strings; new IDs are deterministic.
- IDs are unique within a node table; relations supply type context.
- Retrieval purposes: `chunk_retrieval` and `asset_description_retrieval`.
- `non_semantic_hash_dev` is test-only and never published.
- Asset type is `image` in the current schema.
- Version numbers and IDs are never renumbered after deletion.
- Rebuild-all retains old embeddings in the cumulative dump as `is_current=false`.
- The only supported graph schema is `mvp-v2-graph-v2` with 22 tables and the current schema-profile SHA-256. Graph-v1/21-table artifacts and legacy headers are rejected. Dump format remains `mvp-v2-dump-v1` and database-manifest format remains `mvp-v2-db-v1`; timestamps serialize as UTC with `Z`.
- Dump `run_id` and database `build_id` are canonical SHA-256 identities over their
  immutable manifest facts; each manifest accounts for all 22 current registry tables.
- An empty version SHA is allowed only for an errored, non-current version whose
  `HAS_STATUS` target is the error status.

## TODO_USER

### TODO-SEC-001 — authoritative access classification

The approved graph intentionally has no `sensitivity` or `restricted` properties,
but previous code used them to prevent provider calls. Choose where persistent
classification lives:

1. recommended: a non-graph `access-policy.json` inside each dump, validated before
   any vision/embedding/chat call;
2. remove classification gating and rely only on local provider configuration.

Until this is decided, MVP v2 must default-deny external/model calls for records
without an explicit application-layer decision.

### TODO-MODEL-001 — local model selection

Before live integration, provide/approve the exact Ollama vision, text-embedding and
chat model names/versions and make them available in the operator runtime. Offline
fake-provider tests do not require them.

### Current-schema reference bootstrap

Upload exposes an explicit **Initialize workspace** action. It adds only missing deterministic `SourceType` and `Status` defaults to the session snapshot, preserves existing reference tables, and performs no file/dump/database operation.

### TODO-OPS-001 — persistent runtime location

Choose the final writable server location for dumps, media and LadybugDB files.
Development uses an ignored `mvp_v2/runtime/` path; the Python environment remains in
`/tmp`.

### TODO-VECTOR-001 — Ladybug vector provisioning

The vector extension must be provisioned outside application code for each runtime
HOME. The application may execute `LOAD vector` but never `INSTALL vector`.


## Current artifact contract

The application reads and publishes only current `mvp-v2-graph-v2` artifacts. A dump has exactly 22 canonical CSV tables and a matching schema-profile SHA-256. Asset uses `description`; Embedding purpose is inferred from typed owner relations and is not persisted. Unsupported graph versions, 21-table layouts, `caption`/`llm_description` Asset headers and persisted Embedding `purpose` are rejected rather than migrated.

The dump writer creates all 22 CSVs plus `manifest.json` in a temporary sibling and renames it to the next immutable six-digit directory. It assumes one local Streamlit process and does not mutate historical dumps. The separate restamp workflow repairs only current-schema dumps.

## Consolidated former Phases 4–6 — implemented offline

Flow: configured input adapter → mutation-free preview → explicit commit to configured runtime root + immutable snapshot → authorized local vision description → authorized exact-identity embeddings. `missing` skips current exact identity/purpose pairs. `all` retires relevant current embeddings and adds a deterministic generation while keeping history. Calls validate whole batches before snapshot construction.

Remaining operator validation: select and record exact `TODO-MODEL` vision/text model names and versions; ensure one text model/dimension is used for both chunks and asset descriptions; install/check `pdfinfo` and `pdftotext`; run a permitted live Ollama smoke test. No live call or graph dump publication is part of this phase.
