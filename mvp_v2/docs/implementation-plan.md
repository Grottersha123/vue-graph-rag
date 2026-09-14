# MVP v2 implementation plan

Status: active implementation plan. The canonical graph contract is `docs/schema.md`.

## Boundaries

- New code and project configuration live only under `mvp_v2/`.
- `mvp/mvp_data` is a read-only initial import source.
- `mvp/streamlit_app.py` is a read-only UX reference, not code to copy blindly.
- `agentic_mvp` is a read-only source of tested ideas and failure lessons.
- Runtime Python lives in `/tmp/vue-graph-rag-mvp-v2/.venv`.
- Do not read `.env`, modify the repository `.venv`, or open existing binary `.lbdb`.
- Root `pyproject.toml` and `uv.lock` remain unchanged.

## Legacy source mapping

| Legacy source | MVP v2 target/transformation |
|---|---|
| `SourceType.csv` | Validate and import `SourceType`. |
| `Document.csv` | Import target Document fields. Move `source_type` to a relation; do not copy legacy `sha256`, `local_path`, `status`, or `source_format` as Document properties. |
| `HAS_DOCUMENT.csv` | Reverse `SourceType -> Document` into `Document -> HAS_SOURCE_TYPE -> SourceType`. |
| `DocumentVersion.csv` | Validate and import `DocumentVersion`; ownership comes only from `HAS_VERSION`. |
| `HAS_VERSION.csv` | Import `Document -> DocumentVersion`; construct immediate-predecessor `SUPERSEDES` when absent. |
| `Asset.csv` | Import Asset metadata, caption, and OCR. Initialize `llm_description` as empty until vision processing. |
| `HAS_ASSET.csv` | Import `DocumentVersion -> Asset`. |
| `Role.csv`, `FOR_ROLE.csv` | Import directly after endpoint validation. `support` remains a Role. |
| `Status.csv`, `HAS_STATUS.csv` | Import and validate exactly one status per version. |
| `LINKS_TO.csv` | Import valid Document-to-Document links. |
| `manual_links.csv` | Resolve valid document endpoints into `LINKS_TO`; unresolved rows go to the import report. |
| `image_manifest.csv` | Use only for import diagnostics/path reconciliation; it is not a graph table. |
| `HAS_ERROR.csv` | Drop the edge table; preserve applicable text in `DocumentVersion.error` and report losses. |
| `documents/`, `images/`, `uploads/` | Copy into the new media/source authority during import; never mutate the originals. |
| Chunks and Embeddings | They do not exist in the initial legacy graph and are built later by MVP v2. |
| Process and DESCRIBES | Legacy data does not define them reliably. Create them through Streamlit/operator input; never infer `onboarding` from arbitrary text. |

## Delivery phases

1. Pydantic v2 graph/application/manifest models and executable schema registry.
2. Deterministic typed CSV codec, graph snapshot and invariant validator.
3. Read-only legacy importer and deterministic loss report; gated numbered dump
   publication capability is implemented. The real `dump-000001` remains blocked on
   operator report approval and persistent runtime roots (TODO-DATA-001/TODO-OPS-001).
4. Text ingestion and deterministic chunking without persisted Section nodes.
5. Image collection ingestion, safe media copying and vision descriptions.
6. Missing/all embedding workflows with the exact nine-field Embedding model.
7. LadybugDB staged publication, one selected embedding identity and retrieval.
8. Deletion preview/cascade, immediate active-DB rebuild and stale-DB blocking.
9. OOP Streamlit UI based on legacy UX: Upload, Documents, Relations, Processing,
   Databases, Search and Chatbot.
10. Offline release checks and opt-in external integration/evaluation.

Each phase adds focused tests and must pass Ruff, strict mypy, and offline pytest in
the isolated runtime before the next phase is considered complete.

## Defaults selected for implementation

- Strict immutable Pydantic v2 models with `extra="forbid"`.
- Imported IDs are nonblank/control-free opaque strings; new IDs are deterministic.
- IDs are unique within a node table; relations supply type context.
- Retrieval purposes: `chunk_retrieval` and `asset_description_retrieval`.
- `non_semantic_hash_dev` is test-only and never published.
- Asset type is `image` in schema v1.
- Version numbers and IDs are never renumbered after deletion.
- Rebuild-all retains old embeddings in the cumulative dump as `is_current=false`.
- Graph schema version is `mvp-v2-graph-v1`; dump format is `mvp-v2-dump-v1` and
  database-manifest format is `mvp-v2-db-v1`; timestamps serialize as UTC with `Z`.
- Dump `run_id` and database `build_id` are canonical SHA-256 identities over their
  immutable manifest facts; each manifest accounts for all 21 registry tables.
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

### TODO-DATA-001 — first import review

Review the generated import-loss report before `dump-000001` is accepted. In
particular, approve unresolved manual links, legacy errors, missing files/hashes and
Documents without Process assignments.

### TODO-OPS-001 — persistent runtime location

Choose the final writable server location for dumps, media and LadybugDB files.
Development uses an ignored `mvp_v2/runtime/` path; the Python environment remains in
`/tmp`.

### TODO-VECTOR-001 — Ladybug vector provisioning

The vector extension must be provisioned outside application code for each runtime
HOME. The application may execute `LOAD vector` but never `INSTALL vector`.


## Phase 3 implemented contract

Preview is a non-mutating operation. The importer reads exact allowlisted CSV names,
accumulates parse/model/edge/path/media findings instead of failing at the first bad
row, hashes source CSV and media bytes, and returns the canonical graph separately
from the report JSON. Report identity is the SHA-256 of canonical, sorted, nonvolatile
facts. Expected legacy transformations and review items are warnings/information;
unsafe paths, malformed target rows, invalid endpoints/cardinality, missing referenced
files, hash mismatches, and graph publication errors block publication.

Media commit copies all regular files below the three authorized legacy directories.
It rejects symlinks and lexical escapes, stages beside the chosen destination,
stream-copies with SHA/size verification, refuses an existing authority, and exposes
the complete authority by rename. `FilesystemMediaResolver` confines every graph
path and translates expected filesystem failures to bounded errors.

Dump commit requires the exact approved report ID twice, a publishable report,
successful target media validation, explicit runtime roots, and a UTC timestamp.
Existing dumps must form a valid contiguous immutable sequence. Publication holds an
exclusive lock, writes and rereads 21 canonical CSV tables, verifies counts/hashes,
writes a canonical `DumpManifest`, fsyncs staged files/directories, and atomically
renames the next six-digit directory. Report approval binds canonical digests of the
exact graph and full ordered media copy plan. Every target copy-plan entry is verified
again immediately before the final approval check and dump repository call.

Media-first and dump-second cannot be atomic across separate operator roots. A newly
published immutable media authority can remain after a later dump failure; it is not
visible through a dump and can be reused for a corrected explicit publication. No
real runtime location or dump is selected by this phase.

Remaining gates: TODO-DATA-001 requires operator review of the derived report;
TODO-OPS-001 requires operator-provided persistent media and dumps roots.
TODO-SEC-001 remains default-deny. Phase 3 makes no provider, database, or UI calls.
