# Module map

The source tree is grouped by stable responsibility, not temporary `phase_1` folder
names. This document maps implementation phases to the packages that own them.

```text
mvp_v2/src/mvp_v2/
  domain/                    pure graph rules; no filesystem/network/database/UI
    base.py                  IDs, hashes, strict Pydantic base types
    graph.py                 persisted node and relation models
    snapshot.py              typed aggregate of graph tables
    schema_registry.py       typed core schema plus configured optional fields
    field_extensions.py      strict optional-field config parser
  graph_fields.json          user-editable optional node-field declarations
    invariant_indexes.py     one-pass indexes for graph validation
    invariants.py            cross-row graph validation
  application/               use cases and non-persisted commands/results
    graph_snapshot_operations/ GraphSnapshot use cases and immutable version transitions
      deletion.py             pure deletion planning and snapshot mutation
      document_editing.py     document metadata and title updates
      document_view.py        document summaries and current-version views
      ingestion.py            source ingestion preview/commit workflow
      reference_data.py       minimal SourceType/Status bootstrap
      relations.py            typed GraphSnapshot relation operations
      title_backfill.py       deterministic title backfill from chunks
    conversation/            versioned support prompts and trusted citation rendering
      conf.py                 immutable Chat/MCP prompt profiles and pure templates
      prompting.py            evidence limits and prompt assembly
      rendering.py            strict citations and human-readable provenance
    manifests.py             current-only dump and database manifests
    json_encoding.py         canonical JSON bytes for signed artifacts
    dto/                     embedding/search/deletion DTOs
    rag/preparation/         deterministic chunks and explicit model preparation
      chunking.py            deterministic text chunk policy
      chunk_backfill.py      verified-source chunk preparation and maintenance
      model_operations.py    authorized vision and embedding operations
    rag/retrieval/           active projection and evidence retrieval
      projection.py          deterministic current-active graph projection
      retrieval.py           authorized local embedding search and citations
    services/                orchestration by use case
  ports/                     narrow Protocols required by application services
  adapters/
    csv/                     deterministic typed CSV codec
    filesystem/
      media.py               confined resolver for graph-referenced files
      dumps.py               single-process immutable numbered dump writer
    ollama.py                local vision and embedding adapter
    ladybug.py               graph load, ANN index and retrieval
  presentation/
    app.py                   eight-tab Streamlit composition and click gates
    controller.py            safe upload/config and manifest-only scan helpers
  bootstrap/                 configuration and dependency composition
```

Tests mirror these responsibilities under `mvp_v2/tests/`.

## Phase ownership

| Phase | Primary modules | Main agent role |
|---|---|---|
| 1. Pydantic contract | `domain/base.py`, `domain/graph.py`, `domain/snapshot.py`, `domain/schema_registry.py`, `application/manifests.py`, `application/dto/` | Python implementer |
| 2. CSV and validation | `adapters/csv/`, `domain/invariants.py`, dump application service | Python implementer |
| 3. Current dump publication | `adapters/csv/`, `adapters/filesystem/dumps.py`, publication service | Python implementer |
| 4. Text ingestion | `domain/chunking.py`, ingestion service, document adapters | Python implementer |
| 5. Images and vision | filesystem adapter, vision port, `adapters/ollama/vision.py`, asset service | AI implementer |
| 6. Embeddings | embedding port, planning/service, `adapters/ollama/embedding.py` | AI implementer |
| 7. Ladybug and search | `adapters/ladybug.py`, search service | Python + AI implementers |
| 8. Deletion | pure deletion planner and publication service | Python implementer |
| 9. Local Chatbot and UI | `adapters/ollama.py`, Chatbot service, `presentation/app.py, presentation/controller.py`, `bootstrap/` | AI + Python implementers |
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

- `application/reference_data.py`: explicit minimal current-schema SourceType/Status bootstrap.
- `application/ingestion_contracts.py`: preview/commit and explicit model authorization DTOs.
- `application/graph_snapshot_operations/ingestion.py`: ingestion preview/commit and immutable version transitions.
- `application/rag/preparation/`: deterministic chunks and explicit model preparation.
- `application/rag/preparation/model_operations.py`: local vision updates and missing/all embedding policy.
- `ports/ingestion.py`, `ports/models.py`: narrow input, file, vision, embedding, and later-chat generation ports.
- `adapters/ingestion.py`: UTF-8/HTML/PDF CLI/image extraction.
- `adapters/filesystem/runtime_store.py`: confined temp-file/rename writes.
- `adapters/ollama.py`: bounded local-only HTTP and response parsing.

- `application/rag/retrieval/projection.py`: deterministic current-active one-space database projection.
- `adapters/ladybug.py`: lazy Ladybug build and fixed-index ANN adapter (`LOAD`, never install).
- `application/rag/retrieval/retrieval.py`: authorized local embedding search and typed citation hydration.
- `application/deletion.py`: pure preview/apply cascade and version promotion policy.
- `application/deletion_orchestration.py`: dump-first stale marking and replacement switching.
- `application/services/publication.py`: graph, media, and version-file publication gate.
- `presentation/controller.py`: upload limits/cleanup, exact snapshot/dump binding, and UI deletion adapters.
- `bootstrap/provision_vector.py`: explicit, confirmed external extension provisioning only.

## Debug notebook

- `notebooks/rag_batch_debug.ipynb`: editable 20-case batch runner over the production retrieval/chat path with full local trace export.
