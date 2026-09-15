# MVP v2

Local single-user multimodal Graph RAG with a strict, versioned graph contract.

- Canonical schema: [`docs/schema.md`](docs/schema.md)
- Implementation plan: [`docs/implementation-plan.md`](docs/implementation-plan.md)
- Package map: [`docs/module-map.md`](docs/module-map.md)

## Local runtime workflow

```text
mvp/mvp_data/ -> mvp_v2/runtime/mvp_data/
mvp_v2/runtime/mvp_data/ -> mvp_v2/runtime/dumps/dump-NNNNNN/
mvp_v2/runtime/dumps/ -> mvp_v2/runtime/databases/ (later phase)
```

Populate the ignored working directory once with ordinary filesystem tooling, for
example `cp -R mvp/mvp_data mvp_v2/runtime/mvp_data`. Review what you copy. Do not
select or use old `.lbdb` files; MVP v2 will create its databases later.

`CsvLegacyImporter.preview(runtime_data_root)` reads the existing working copy. It
allowlists the 15 source CSV names and scans only `documents/`, `images/`, and
`uploads/`. Other root entries are ignored. Preview returns a canonical
`GraphSnapshot` and deterministic report. The report binds the exact snapshot,
source CSV hashes, source file totals, source-tree digest, and diagnostics.

After explicit report review, `LegacyMigrationService.publish` accepts the exact
report ID, `FilesystemMediaResolver(runtime_data_root)`, dumps root, and UTC time.
A blocking report, approval mismatch, invalid graph, or missing/changed graph-referenced
DocumentVersion or Asset file blocks publication. Uploads and other orphan source
files are not rechecked at publication.

`FilesystemDumpRepository` writes 21 canonical CSV tables and `manifest.json` to a
temporary sibling, then renames it to immutable `dump-NNNNNN`. It chooses the next
number after the highest valid six-digit dump directory and cleans its failed staging
directory. It assumes one local Streamlit process; it has no inter-process lock.
No preview copies data or publishes a dump.

## Offline ingestion and embeddings

`IngestionService` previews URL/PDF/Markdown/HTML/image-collection inputs without mutation, then commits a new immutable `GraphSnapshot` and confined runtime files. Exact `external_id` (or URL when no external ID is supplied) drives document identity. Versions and embedding history are retained.

Local model operations require `ModelAuthorization(allow=True, operation=...)`. Configure exact Ollama model and version values at the application boundary (`TODO-MODEL`); there is no default model or cloud fallback. Asset retrieval embeds the deterministic text `Image description:\n{llm_description}` and requires a nonblank description. Live Ollama/model quality and installed PDF CLI validation remain operator tasks.

## Ladybug database and vector provisioning

The database is an **active evidence projection**: active Documents, their current DocumentVersion, owned Chunks/Assets, exact-current embedding-space Embeddings, and only linked SourceType/Status/Role/Process metadata. Historical versions, other embedding spaces, and dangling ownership edges are excluded. Media paths remain references; image bytes are never loaded. Builds refuse overwrite and require a direct `.lbdb` child of an existing real configured root. They stage beside the target, verify all 21 counts and ANN smoke, close and reopen the database, write and validate canonical `_mvp_v2_manifest.json` inside the staged database directory, then publish that one directory with one atomic rename.

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
creation. The UI does not migrate `mvp/mvp_data` and does not open a native database
until the operator explicitly selects it. Use the same `MVP_V2_LADYBUG_HOME` for
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

Deletion confirmation requires an exact preview, a clean snapshot bound to the selected
canonical dump, and its nonstale active database. It publishes the validated deleted
snapshot before building a replacement. A failed replacement leaves the new snapshot
clean and published while the old database stays stale and unselectable. Physical media
is never deleted.
All model providers are fixed to local Ollama; cloud fallback is not supported.
