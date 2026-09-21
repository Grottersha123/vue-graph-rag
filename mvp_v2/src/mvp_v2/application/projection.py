from __future__ import annotations

from mvp_v2.application.dto.retrieval import EmbeddingSpaceIdentity
from mvp_v2.domain.invariants import validate_graph
from mvp_v2.domain.schema_registry import NODE_SPECS, RELATION_SPECS
from mvp_v2.domain.snapshot import GraphSnapshot


class ProjectionError(ValueError):
    pass


def active_projection(
    snapshot: GraphSnapshot, space: EmbeddingSpaceIdentity
) -> GraphSnapshot:
    active_documents = {x.id for x in snapshot.documents if x.is_active}
    version_owners = {e.target_id: e.source_id for e in snapshot.has_versions}
    versions = {
        x.id
        for x in snapshot.document_versions
        if x.is_current and version_owners.get(x.id) in active_documents
    }
    chunks = {e.target_id for e in snapshot.has_chunks if e.source_id in versions}
    assets = {e.target_id for e in snapshot.has_assets if e.source_id in versions}
    embeddings_by_id = {x.id: x for x in snapshot.embeddings}
    owned: set[str] = set()
    for chunk_edge in snapshot.has_embedding_chunks:
        item = embeddings_by_id.get(chunk_edge.target_id)
        if chunk_edge.source_id in chunks and item is not None:
            owned.add(item.id)
    for asset_edge in snapshot.has_embedding_assets:
        item = embeddings_by_id.get(asset_edge.target_id)
        if asset_edge.source_id in assets and item is not None:
            owned.add(item.id)
    embeddings = {
        x.id
        for x in snapshot.embeddings
        if x.id in owned
        and x.is_current
        and (x.provider, x.model, x.model_version, x.dimension)
        == (space.provider, space.model, space.model_version, space.dimension)
        and len(x.vector) == space.dimension
    }
    if not embeddings:
        raise ProjectionError(
            "active projection requires at least one indexed embedding"
        )
    node_ids: dict[str, set[str]] = {
        "Document": active_documents,
        "DocumentVersion": versions,
        "Chunk": chunks,
        "Asset": assets,
        "Embedding": embeddings,
    }
    # Retain only useful classification metadata linked to projected documents/versions.
    node_ids["SourceType"] = {
        e.target_id
        for e in snapshot.has_source_types
        if e.source_id in active_documents
    }
    node_ids["Role"] = {
        e.target_id for e in snapshot.for_roles if e.source_id in active_documents
    }
    node_ids["Process"] = {
        e.target_id for e in snapshot.describes if e.source_id in active_documents
    }
    node_ids["Status"] = {
        e.target_id for e in snapshot.has_statuses if e.source_id in versions
    }
    updates: dict[str, object] = {}
    for spec in NODE_SPECS:
        updates[spec.snapshot_field] = tuple(
            x
            for x in getattr(snapshot, spec.snapshot_field)
            if x.id in node_ids[spec.table_name]
        )
    for relation_spec in RELATION_SPECS:
        updates[relation_spec.snapshot_field] = tuple(
            e
            for e in getattr(snapshot, relation_spec.snapshot_field)
            if e.source_id in node_ids[relation_spec.source_label]
            and e.target_id in node_ids[relation_spec.target_label]
        )
    try:
        projected = GraphSnapshot.model_validate(updates)
    except ValueError as error:
        raise ProjectionError("active projection is invalid") from error
    if not validate_graph(projected).is_valid:
        raise ProjectionError("active projection is invalid")
    return projected
