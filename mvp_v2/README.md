# MVP v2

Local single-user multimodal Graph RAG with a strict, versioned graph contract.

- Canonical schema: [`docs/schema.md`](docs/schema.md)
- Implementation plan: [`docs/implementation-plan.md`](docs/implementation-plan.md)
- Package map: [`docs/module-map.md`](docs/module-map.md)

## Local runtime workflow

```text
mvp_v2/runtime/mvp_data/ -> working source/media files
session snapshot -> mvp_v2/runtime/dumps/dump-NNNNNN/
mvp_v2/runtime/dumps/ -> mvp_v2/runtime/databases/
```

The folder name `runtime/mvp_data` is retained as the configured working root, but no legacy importer remains. Do not copy or load old `mvp/mvp_data` CSV trees. The application accepts only current `mvp-v2-graph-v2` artifacts with exactly 22 canonical CSV tables and the current schema-profile SHA-256. Graph-v1/21-table dumps, legacy Asset headers (`caption`, `llm_description`), and persisted Embedding `purpose` columns are rejected.

`FilesystemDumpRepository` writes 22 canonical CSV tables and `manifest.json` to a temporary sibling, then renames it to immutable `dump-NNNNNN`. It chooses the next number after the highest valid six-digit dump directory and cleans its failed staging directory. It assumes one local Streamlit process; it has no inter-process lock. No preview copies data or publishes a dump.

A session can start from a verified current dump or an empty snapshot. For an empty snapshot, Upload shows **Initialize workspace**. This explicit action adds only minimal current-schema `SourceType` and `Status` rows to the session snapshot; it does not read files, publish a dump or create a database.

## Offline ingestion and embeddings

`IngestionService` previews URL/PDF/Markdown/HTML/image-collection inputs without mutation, then commits a new immutable `GraphSnapshot` and confined runtime files. Exact `external_id` (or URL when no external ID is supplied) drives document identity. Versions and embedding history are retained. Upload is the primary ingestion surface. For a selected Document, **Upload new version** opens the same upload form in a dialog and binds the request to that exact Document ID and external ID. Markdown sibling images must be included in the same upload batch; the new DocumentVersion receives its own Asset nodes and version-confined media paths, while prior version media remains intact.

Local model operations require `ModelAuthorization(allow=True, operation=...)`. Configure exact Ollama model and version values at the application boundary (`TODO-MODEL`); there is no default model or cloud fallback. Asset retrieval embeds the deterministic text `Image description:\n{description}` and requires a nonblank description. Live Ollama/model quality and installed PDF CLI validation remain operator tasks.

## Ladybug database and vector provisioning

The database is an **active evidence projection**: active Documents, their current DocumentVersion, owned Chunks/Assets, exact-current embedding-space Embeddings, and only linked SourceType/Status/Role/Process metadata. Historical versions, other embedding spaces, and dangling ownership edges are excluded. Media paths remain references; image bytes are never loaded. Builds refuse overwrite and require a direct `.lbdb` child of an existing real configured root. They stage beside the target, verify all 22 counts and ANN smoke, close and reopen the database, write and validate canonical `_mvp_v2_manifest.json` inside the staged database directory, then publish that one directory with one atomic rename.

Vector provisioning is an explicit operator-only network side effect. Run it once with the same `HOME` used by the application:

```console
/tmp/vue-graph-rag-mvp-v2/.venv/bin/python -m mvp_v2.bootstrap.provision_vector --home <runtime-home> --yes
```

Normal application code only executes `LOAD vector`; it never installs extensions. Ordinary uploads do not build a database. Only confirmed deletion orchestration may immediately build a replacement. Physical source/media files are reported, never deleted.


## Phase 9 local Streamlit UI

From the repository root, create the explicit local runtime folders and copy the
example environment file once:

```console
mkdir -p mvp_v2/runtime/{mvp_data,dumps,databases,ladybug-home}
cp mvp_v2/.env.example mvp_v2/.env
```

Fill in the exact installed Ollama model names, versions, and embedding dimension.
Then launch through uv while keeping the environment outside the repository:

```console
UV_PROJECT_ENVIRONMENT=/tmp/vue-graph-rag-mvp-v2/.venv \
  uv run --project mvp_v2 --env-file mvp_v2/.env \
  streamlit run mvp_v2/streamlit_app.py
```

`.env` is ignored; `.env.example` is tracked and contains no secrets. `uv` loads its
values into the process, and the UI uses them as sidebar/model defaults. The sidebar
still accepts only paths confined below `mvp_v2/runtime`. Missing folders, dumps,
databases, Ollama, and models are reported without startup calls or automatic
creation. The UI has no legacy migration path and does not open a native database until the operator explicitly selects it. Use the same `MVP_V2_LADYBUG_HOME` for
external vector provisioning. Every vision, embedding, or generation call still
requires fresh explicit authorization.

## Implemented Phase 9 controls

The eight-tab UI wires ingestion preview/commit, document views and deletion preview,
relation edits, chunk/asset/embedding history, authorized local Ollama embedding and
vision operations, canonical dump load/publication, manifest-only database discovery
and explicit builds, authorized evidence search, and session-only cited chat. Invalid
or out-of-runtime sidebar paths block every operation; action code never falls back to
default paths. Database artifacts are scanned through their canonical internal manifest
without opening `database.lbug`, and only an explicit search/chat/build action lazily
imports Ladybug. Database and model operations never run during render.

Configured node properties from `graph_fields.json` are displayed as ordinary read-only columns only on their declared Document, Chunk, Asset, or Embedding table. The internal `custom_properties` sidecar is never displayed directly.

Deletion confirmation requires an exact preview, a clean snapshot bound to the selected
canonical dump, and its nonstale active database. It publishes the validated deleted
snapshot before building a replacement. A failed replacement leaves the new snapshot
clean and published while the old database stays stale and unselectable. Physical media
is never deleted.
All model providers are fixed to local Ollama; cloud fallback is not supported.

## RAG batch debugging

Open `notebooks/rag_batch_debug.ipynb` with the `mvp_v2` project interpreter. Set the exact Ollama chat model/version in the configuration cell. Loopback is allowed by default. A private LAN Ollama additionally requires `MVP_V2_OLLAMA_ALLOWED_LAN_HOSTS` (for example `models.local`); this is an explicit opt-in to unencrypted HTTP inside the LAN. The notebook selects a verified current-schema LadybugDB and its exact source dump, runs the editable batch through the production `SearchService` and `ChatService`, and records retrieval evidence, prompts, raw responses, final answers, latency, and errors. Retrieval keeps only evidence with cosine-derived similarity score greater than or equal to `MVP_V2_MINIMUM_SIMILARITY_SCORE` (default `0.55`), and records that threshold in each trace and `run.json`. Models use strict `[N]` or `[N, M]` technical citation tokens only in raw responses. Before display, the application validates and replaces them with short references containing the source URL when available (otherwise the document title) and the one-based chunk number. Chat UI and Markdown export use the same renderer without technical numbering. The model never generates source metadata. Select versioned templates with `PROMPT_PROFILE_NAME` and `MCP_PROMPT_PROFILE_NAME`; all Chat and MCP templates live in `application/rag/agent/prompts.py`. Each archived batch `run.json` records the complete Chat prompt profile, `GENERATION_TEMPERATURE` (currently `0.0`), and the location of per-case rendered prompts; `results.json` stores each exact raw prompt. The interactive MCP trace records its own profile, temperature, and both generated prompts. Set `EXECUTION_MODE` to `sequential` or `async`; `Run All` executes only the selected batch mode.

Each sequential or four-worker async batch is automatically saved under `runtime/debug/rag-runs/YYYY-MM-DD/<UTC-run-id>/`: `run.json` stores reproducibility metadata and `results.json` stores the complete trace. The directory is git-ignored and may contain retrieved text. Student refusal cases are behavioral probes only: the current retrieval layer does not implement a role-based authorization boundary.

The **Chunks & Embeddings** tab also provides preview/confirm actions to regenerate all supported current Markdown chunks or delete the complete chunk subgraph and its owned embeddings. These actions update only the session snapshot until a new dump is published.
