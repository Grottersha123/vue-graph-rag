# MVP v2

Local single-user multimodal Graph RAG with a strict, versioned graph contract.

- Local setup: [`setup-project.md`](setup-project.md)
- Canonical schema: [`docs/schema.md`](docs/schema.md)
- Implementation history: [`docs/implementation-plan.md`](docs/implementation-plan.md)
- Package map: [`docs/module-map.md`](docs/module-map.md)
- Token-efficient OpenSpec workflow: [`docs/openspec-token-efficient-workflow.md`](docs/openspec-token-efficient-workflow.md)

## Local runtime workflow

```text
mvp_v2/runtime/mvp_data/ -> working source/media files
session snapshot -> mvp_v2/runtime/dumps/dump-NNNNNN/
mvp_v2/runtime/dumps/ -> mvp_v2/runtime/databases/
```

`runtime/mvp_data` is the current mutable workspace. The sibling legacy project
`../mvp/` is not a package, build, runtime, or data dependency of `mvp_v2`; do not
copy its CSV trees into the current workspace. The application accepts only current
`mvp-v2-graph-v2` artifacts with exactly 22 canonical CSV tables and the current
schema-profile SHA-256. Graph-v1/21-table dumps, legacy Asset headers (`caption`,
`llm_description`), and persisted Embedding `purpose` columns are rejected.

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

## Read-only Ladybug Explorer

Use the official [Ladybug Explorer](https://github.com/LadybugDB/explorer) to
inspect an existing canonical artifact under `runtime/databases/`. The Makefile
lists only real `.lbdb` directories containing both `database.lbug` and
`_mvp_v2_manifest.json`:

```console
make explorer
```

Select a known artifact without an interactive prompt:

```console
make explorer EXPLORER_DATABASE=clean-v8-onboarding.lbdb
```

The target uses the project's `PODMAN` command (`podman` by default), adds
`--userns=keep-id`, and opens `http://127.0.0.1:8000`. If the gateway already
owns port 8000, choose another host port:

```console
make explorer EXPLORER_PORT=8001
```

A compatible alternative engine can still be supplied explicitly with
`EXPLORER_ENGINE`, but Docker is not the project default.

The artifact is mounted read-only and Explorer receives `MODE=READ_ONLY`, so UI
queries cannot modify the immutable database. The container runs in the
foreground and is removed when it stops. Override `EXPLORER_IMAGE` to use a
pinned image tag or digest instead of the default
`ghcr.io/ladybugdb/explorer:latest`.

On macOS, `proxy already running` is a Podman `gvproxy` port-forwarding failure,
not a database error. Confirm that the failed container is stopped, retry with a
different `EXPLORER_PORT`, and restart `podman machine` only if a stale `gvproxy`
listener remains. Restarting the machine stops all running Podman containers.

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
  uv run --project vue_rag --env-file vue_rag/.env \
  streamlit run vue_rag/streamlit_app.py
```

`.env` is ignored; `.env.example` is tracked and contains no secrets. `uv` loads its
values into the process, and the UI uses them as sidebar/model defaults. The sidebar
still accepts only paths confined below `mvp_v2/runtime`. Missing folders, dumps,
databases, Ollama, and models are reported without startup calls or automatic
creation. The UI has no legacy migration path and does not open a native database until the operator explicitly selects it. Use the same `MVP_V2_LADYBUG_HOME` for
external vector provisioning. Every vision, embedding, or generation call still
requires fresh explicit authorization.

## Local FastAPI/Telegram gateway runtime

The gateway reads one explicit immutable database artifact and never scans for the
latest database. Configure the ignored local `.env` with:

- `MVP_V2_DATABASE_NAME` — the artifact name without `.lbdb`;
- `MVP_V2_CHAT_MODEL` and `MVP_V2_CHAT_MODEL_VERSION`;
- `TELEGRAM_ALLOWED_USER_IDS` — comma-separated positive Telegram user IDs;
- optionally `MVP_V2_OLLAMA_BASE_URL`, bounded
  `MVP_V2_OLLAMA_TIMEOUT_SECONDS` (default `120`, maximum `600`),
  `MVP_V2_MODEL_CONCURRENCY`, and the runtime directory overrides documented
  by `GatewaySettings`.

Direct non-container runs additionally read `MVP_V2_BACKEND_TOKEN` and
`TELEGRAM_BOT_TOKEN` from their environment. Compose does not use those token
environment variables; it reads ignored files under `secrets/` instead.

The selected artifact must be
`runtime/databases/${MVP_V2_DATABASE_NAME}.lbdb`. The backend validates its
canonical manifest, compares table counts and embedding identity with the open
artifact, and opens `database.lbug` read-only. It does not read `runtime/dumps/`,
run ingestion, or build a database.

Host-side checks and non-container runs must use the external environment:

```console
UV_PROJECT_ENVIRONMENT=/tmp/vue-graph-rag-mvp-v2/.venv \
  uv run --project mvp_v2 --env-file mvp_v2/.env \
  python -m pytest mvp_v2/tests/backend

UV_PROJECT_ENVIRONMENT=/tmp/vue-graph-rag-mvp-v2/.venv \
  uv run --project mvp_v2 --env-file mvp_v2/.env \
  python -m mvp_v2.backend

UV_PROJECT_ENVIRONMENT=/tmp/vue-graph-rag-mvp-v2/.venv \
  uv run --project mvp_v2 --env-file mvp_v2/.env \
  python -m mvp_v2.telegram_bot
```

The local API provides:

- `GET /health`;
- `GET /v1/profiles`;
- `POST /v1/messages`;
- `PUT /v1/users/{telegram_user_id}/profile`;
- `POST /v1/feedback`.

All `/v1/*` routes require `Authorization: Bearer <MVP_V2_BACKEND_TOKEN>`.
`GET /health` remains credential-free for the local container healthcheck.

Prompt profiles come from `PROMPT_TEMPLATES`; Redis stores only the selected
`profile_id`. Effective selection is explicit request profile, then the user's
Redis default, then `DEFAULT_PROMPT_TEMPLATE`. The gateway rejects excess work
immediately with HTTP 503 at the global limit of six or the configured per-model
limit.

Non-container runs write `backend.jsonl`, `telegram.jsonl`, `errors.jsonl`,
`prompt_traces.jsonl`, and `feedback.jsonl` under `runtime/logs/gateway/`.
Compose stores the same files in its `gateway-logs` named volume. Only
`prompt_traces.jsonl` contains complete rendered prompts. Use
`mvp_v2.analytics.chat_logs.ChatLogAnalytics` or `standard_reports()` for
offline DuckDB reports and request-ID debugging.

Podman Compose runs backend, Telegram, and Redis as separate services. Run the
following commands from `mvp_v2/`:

```console
make deploy
make status
```

`make deploy` checks host tools and required `.env` keys, securely prompts for a
missing Telegram token, generates the shared gateway token, validates Compose,
builds and force-recreates the services, and waits for backend readiness. Secret
files are ignored, mode `0600`, and their values are not printed. Deployment does
not run embedded smoke, authenticated RAG, or container-security tests. Use
`make health`, `make diagnose-models`, and the project test suite explicitly when
those checks are required.

Useful targets are:

- `make secrets` — create or validate the two secret files;
- `make config` — validate Compose without printing resolved environment values;
- `make health` — print the public backend health response with bounded `curl`;
- `make diagnose-models` — call the configured embedding and generation models
  without printing prompts, tokens, or model responses;
- `make export-logs` — copy the five JSONL streams and the last 500 backend and
  Telegram container log lines to the ignored `runtime/logs/gateway-export/`
  directory;
- `make logs` — show the last 100 Compose log lines without following;
- `make down` — stop and remove Compose containers/network without deleting secrets or named volumes;
- `make clean` — explicitly remove Compose containers/network while preserving images, Redis/log volumes, secrets, `.env`, and database artifacts.

The shared image includes `curl` for bounded in-container diagnostics. For example:

```console
podman-compose exec backend curl -fsS http://127.0.0.1:8000/health
```

The image provisions the Ladybug `vector` extension at build time under
`/opt/mvp-v2/ladybug-home`; runtime only loads it and never installs extensions
or mutates the selected database artifact.

Compose mounts only `runtime/databases/` read-only and the `gateway-logs` named
volume writable. `errors.jsonl` can contain bounded local exception tracebacks,
and `prompt_traces.jsonl` contains full rendered prompts; treat both as sensitive
local diagnostic files. Compose does not mount `.venv`, `runtime/dumps/`, a home
directory, SSH/GPG material, or a container socket. Ollama remains on the host and defaults
to `host.containers.internal` for the container runtime.

After changing gateway DTOs or services in a long-lived notebook/process,
restart the interpreter or reload in dependency order: contracts, profiles and
audit modules first; service/runtime modules second; application composition
last. Validate DTO construction in a fresh interpreter before reuse.

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

Open `notebooks/rag_batch_debug.ipynb` with the `mvp_v2` project interpreter. Set the exact Ollama chat model/version in the configuration cell. Loopback is allowed by default. A private LAN Ollama additionally requires `MVP_V2_OLLAMA_ALLOWED_LAN_HOSTS` (for example `models.local`); this is an explicit opt-in to unencrypted HTTP inside the LAN. The notebook selects a verified current-schema LadybugDB and its exact source dump, runs the editable batch through the production `SearchService` and `ChatService`, and records retrieval evidence, run-level prompt metadata, raw responses, final answers, latency, and errors. Retrieval keeps only evidence with cosine-derived similarity score greater than or equal to `MVP_V2_MINIMUM_SIMILARITY_SCORE` (default `0.55`), and records that threshold in each trace and `run.json`. Models use strict `[N]` or `[N, M]` technical citation tokens only in raw responses. Before display, the application validates and replaces them with short references containing the source URL when available (otherwise the document title) and the one-based chunk number. Chat UI and Markdown export use the same renderer without technical numbering. The model never generates source metadata. Select versioned templates with `PROMPT_PROFILE_NAME` and `MCP_PROMPT_PROFILE_NAME`; all Chat and MCP templates live in `application/rag/agent/prompts.py`. Each archived batch `run.json` records the complete Chat prompt profile, `GENERATION_TEMPERATURE` (currently `0.0`), database identity and model settings; `results.json` stores compact case-level traces without rendered prompts. The interactive MCP trace records its own profile, temperature, and both generated prompts. Set `EXECUTION_MODE` to `sequential` or `async`; `Run All` executes only the selected batch mode.

Each sequential or four-worker async batch is automatically saved under `runtime/debug/rag-runs/YYYY-MM-DD/<UTC-run-id>/`: `run.json` stores shared reproducibility metadata and `results.json` stores compact case-level traces without rendered prompts. The directory is git-ignored and may contain retrieved text. Student refusal cases are behavioral probes only: the current retrieval layer does not implement a role-based authorization boundary.

The **Chunks & Embeddings** tab also provides preview/confirm actions to regenerate all supported current Markdown chunks or delete the complete chunk subgraph and its owned embeddings. These actions update only the session snapshot until a new dump is published.
