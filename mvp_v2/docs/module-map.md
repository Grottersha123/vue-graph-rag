# Module map

The source tree is grouped by stable responsibility, not temporary `phase_1` folder
names. This document maps implementation phases to the packages that own them.

```text
mvp_v2/src/mvp_v2/
  domain/                    pure graph rules; no filesystem/network/database/UI
    base.py                  IDs, hashes, strict Pydantic base types
    graph.py                 persisted node and relation models
    snapshot.py              typed aggregate of graph tables
    schema_registry.py       one executable node/edge/property contract
    invariant_indexes.py     one-pass indexes for graph validation
    invariants.py            cross-row graph validation
    chunking.py              deterministic text chunk policy
  application/               use cases and non-persisted commands/results
    manifests.py             dump and database manifests
    dto/                     embedding/search/deletion/import DTOs
    services/                orchestration by use case
      migration.py           preview, approval, validation, and dump gates
  ports/                     narrow Protocols required by application services
  adapters/
    csv/                     deterministic typed CSV codec
    legacy/
      reader.py              allowlisted exact-header CSV reader
      common.py              bounded scalar/path/issue helpers
      parsers.py             adapter-local legacy-to-target record mapping
      diagnostics.py         cross-check and image-manifest diagnostics
      source_tree.py         documents/images/uploads scan and diagnostics
      reporting.py           canonical report facts, ordering, and identity
      importer.py            read-only preview composition
    filesystem/
      media.py               confined resolver for graph-referenced files
      dumps.py               single-process immutable numbered dump writer
    ollama.py                local vision and embedding adapter
    ladybug.py               graph load, ANN index and retrieval
  presentation/
    streamlit/               pages, components and session-state controllers
  bootstrap/                 configuration and dependency composition
```

Tests mirror these responsibilities under `mvp_v2/tests/`.

## Phase ownership

| Phase | Primary modules | Main agent role |
|---|---|---|
| 1. Pydantic contract | `domain/base.py`, `domain/graph.py`, `domain/snapshot.py`, `domain/schema_registry.py`, `application/manifests.py`, `application/dto/` | Python implementer |
| 2. CSV and validation | `adapters/csv/`, `domain/invariants.py`, dump application service | Python implementer |
| 3. Legacy import | `adapters/legacy/`, import application service | Python implementer |
| 4. Text ingestion | `domain/chunking.py`, ingestion service, document adapters | Python implementer |
| 5. Images and vision | filesystem adapter, vision port, `adapters/ollama/vision.py`, asset service | AI implementer |
| 6. Embeddings | embedding port, planning/service, `adapters/ollama/embedding.py` | AI implementer |
| 7. Ladybug and search | `adapters/ladybug.py`, search service | Python + AI implementers |
| 8. Deletion | pure deletion planner and publication service | Python implementer |
| 9. Local Chatbot and UI | `adapters/ollama/chat.py`, Chatbot service, `presentation/streamlit/`, `bootstrap/` | AI + Python implementers |
| 10. Release checks | mirrored tests and opt-in integration probes | Python + AI reviewers |

## Dependency direction

```text
presentation/bootstrap -> application -> domain
                              |
                              v
                            ports <- adapters
```

Domain modules never import Streamlit, CSV, filesystem, Ollama, or Ladybug. Adapters
do not define business policy. Ollama is the only Chatbot provider in the first
release and there is no cloud fallback.

## Consolidated ingestion/model delivery

- `application/ingestion_contracts.py`: preview/commit and explicit model authorization DTOs.
- `application/chunking.py`, `application/ingestion.py`: deterministic chunks and immutable version transitions.
- `application/model_operations.py`: local vision updates and missing/all embedding policy.
- `ports/ingestion.py`, `ports/models.py`: narrow input, file, vision, embedding, and later-chat generation ports.
- `adapters/ingestion.py`: UTF-8/HTML/PDF CLI/image extraction.
- `adapters/filesystem/runtime_store.py`: confined temp-file/rename writes.
- `adapters/ollama.py`: bounded local-only HTTP and response parsing.

- `application/projection.py`: deterministic current-active one-space database projection.
- `adapters/ladybug.py`: lazy Ladybug build and fixed-index ANN adapter (`LOAD`, never install).
- `application/retrieval.py`: authorized local embedding search and typed citation hydration.
- `application/deletion.py`: pure preview/apply cascade and version promotion policy.
- `application/deletion_orchestration.py`: dump-first stale marking and replacement switching.
- `bootstrap/provision_vector.py`: explicit, confirmed external extension provisioning only.
