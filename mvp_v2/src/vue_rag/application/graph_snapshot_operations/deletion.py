from __future__ import annotations

from collections import defaultdict

from vue_rag.application.dto.deletion import (
    DatabaseIdentity,
    DeletionCommand,
    DeletionImpact,
    GraphNodeRef,
    GraphRelationRef,
)
from vue_rag.domain import graph
from vue_rag.domain.invariants import validate_graph
from vue_rag.domain.schema_registry import NODE_SPECS, RELATION_SPECS
from vue_rag.domain.snapshot import GraphSnapshot


class DeletionError(ValueError):
    pass


def apply_deletion(snapshot: GraphSnapshot, command: DeletionCommand) -> GraphSnapshot:
    table, target = command.target.table_name, command.target.id
    ids: dict[str, set[str]] = {s.table_name: set() for s in NODE_SPECS}
    ids[table].add(target)
    versions_by_document = defaultdict(set)
    for e in snapshot.has_versions:
        versions_by_document[e.source_id].add(e.target_id)
    if table == "Document":
        ids["DocumentVersion"] |= versions_by_document[target]
    if table == "DocumentVersion":
        pass
    # Owned version subgraph.
    ids["Chunk"] |= {
        e.target_id
        for e in snapshot.has_chunks
        if e.source_id in ids["DocumentVersion"]
    }
    ids["Asset"] |= {
        e.target_id
        for e in snapshot.has_assets
        if e.source_id in ids["DocumentVersion"]
    }
    if table == "Asset":
        ids["Asset"].add(target)
    ids["Embedding"] |= {
        e.target_id
        for e in snapshot.has_embedding_chunks
        if e.source_id in ids["Chunk"]
    }
    ids["Embedding"] |= {
        e.target_id
        for e in snapshot.has_embedding_assets
        if e.source_id in ids["Asset"]
    }
    ids["Embedding"] |= {
        e.target_id
        for e in snapshot.has_summary_embeddings
        if e.source_id in ids["Document"]
    }
    if table == "Embedding":
        ids["Embedding"].add(target)
    if not any(
        x.id == target
        for x in getattr(
            snapshot,
            next(s.snapshot_field for s in NODE_SPECS if s.table_name == table),
        )
    ):
        raise DeletionError("deletion target does not exist exactly")
    updates: dict[str, object] = {}
    for s in NODE_SPECS:
        updates[s.snapshot_field] = tuple(
            x
            for x in getattr(snapshot, s.snapshot_field)
            if x.id not in ids[s.table_name]
        )
    for relation_spec in RELATION_SPECS:
        updates[relation_spec.snapshot_field] = tuple(
            e
            for e in getattr(snapshot, relation_spec.snapshot_field)
            if e.source_id not in ids[relation_spec.source_label]
            and e.target_id not in ids[relation_spec.target_label]
        )
    result = GraphSnapshot.model_validate(updates)
    affected_documents = (
        {target}
        if table == "Document"
        else {
            e.source_id
            for e in snapshot.has_versions
            if e.target_id in ids["DocumentVersion"]
        }
    )
    versions = {v.id: v for v in result.document_versions}
    hv = list(result.has_versions)
    promoted: set[str] = set()
    deactivated: set[str] = set()
    version_updates = list(result.document_versions)
    document_updates = list(result.documents)
    supersedes = list(result.supersedes)
    for document_id in affected_documents:
        remaining = [
            versions[e.target_id]
            for e in hv
            if e.source_id == document_id and e.target_id in versions
        ]
        if document_id in ids["Document"]:
            continue
        if remaining:
            current = max(remaining, key=lambda x: (x.version, x.id))
            promoted.add(current.id)
            version_updates = [
                v.model_copy(update={"is_current": v.id == current.id})
                if v.id in {x.id for x in remaining}
                else v
                for v in version_updates
            ]
            owned = {x.id for x in remaining}
            supersedes = [
                e
                for e in supersedes
                if e.source_id not in owned and e.target_id not in owned
            ]
            ordered = sorted(remaining, key=lambda x: (x.version, x.id))
            supersedes += [
                graph.Supersedes.model_validate({"from": new.id, "to": old.id})
                for old, new in zip(ordered, ordered[1:], strict=False)
            ]
        else:
            deactivated.add(document_id)
            document_updates = [
                d.model_copy(update={"is_active": False}) if d.id == document_id else d
                for d in document_updates
            ]
    result = GraphSnapshot.model_validate(
        {
            **result.model_dump(),
            "document_versions": tuple(version_updates),
            "documents": tuple(document_updates),
            "supersedes": tuple(supersedes),
        }
    )
    if not validate_graph(result).is_valid:
        raise DeletionError("deletion would violate graph invariants")
    return result


def preview_deletion(
    snapshot: GraphSnapshot,
    command: DeletionCommand,
    stale_databases: tuple[DatabaseIdentity, ...] = (),
) -> DeletionImpact:
    result = apply_deletion(snapshot, command)
    old_nodes = {
        (s.table_name, x.id)
        for s in NODE_SPECS
        for x in getattr(snapshot, s.snapshot_field)
    }
    new_nodes = {
        (s.table_name, x.id)
        for s in NODE_SPECS
        for x in getattr(result, s.snapshot_field)
    }
    old_edges = {
        (s.relation_type, e.source_id, e.target_id)
        for s in RELATION_SPECS
        for e in getattr(snapshot, s.snapshot_field)
    }
    new_edges = {
        (s.relation_type, e.source_id, e.target_id)
        for s in RELATION_SPECS
        for e in getattr(result, s.snapshot_field)
    }
    removed_ids = {identifier for _, identifier in old_nodes - new_nodes}
    removed_paths = {
        item.local_path
        for item in snapshot.document_versions
        if item.id in removed_ids and item.local_path
    } | {
        item.local_path
        for item in snapshot.assets
        if item.id in removed_ids and item.local_path
    }
    remaining_paths = {
        item.local_path for item in result.document_versions if item.local_path
    } | {item.local_path for item in result.assets if item.local_path}
    paths = tuple(sorted(removed_paths - remaining_paths))
    promoted = tuple(
        sorted(
            v.id
            for v in result.document_versions
            if v.is_current
            and any(
                old.id == v.id and not old.is_current
                for old in snapshot.document_versions
            )
        )
    )
    deactivated = tuple(
        sorted(
            d.id
            for d in result.documents
            if not d.is_active
            and any(old.id == d.id and old.is_active for old in snapshot.documents)
        )
    )
    return DeletionImpact(
        command=command,
        affected_nodes=tuple(
            GraphNodeRef(table_name=t, id=i) for t, i in sorted(old_nodes - new_nodes)
        ),
        affected_relations=tuple(
            GraphRelationRef(relation_type=t, source_id=a, target_id=b)
            for t, a, b in sorted(old_edges - new_edges)
        ),
        referenced_physical_paths=paths,
        promoted_version_ids=promoted,
        deactivated_document_ids=deactivated,
        stale_databases=tuple(
            sorted(stale_databases, key=lambda x: (x.database_name, x.build_id))
        ),
    )
