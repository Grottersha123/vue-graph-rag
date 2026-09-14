from __future__ import annotations

from collections import Counter, defaultdict
from typing import TypeAlias

from mvp_v2.domain import graph
from mvp_v2.domain.invariant_indexes import (
    GraphIndexes,
    build_indexes,
    index_incoming,
    index_outgoing,
    model_id,
)
from mvp_v2.domain.schema_registry import (
    NODE_SPECS,
    RELATION_SPECS,
)
from mvp_v2.domain.snapshot import GraphSnapshot
from mvp_v2.domain.validation import (
    ValidationIssue,
    ValidationResult,
    build_validation_result,
)

Edge: TypeAlias = tuple[str, str]
EmbeddingIdentity: TypeAlias = tuple[str, str, str, int]


def validate_graph(snapshot: GraphSnapshot) -> ValidationResult:
    """Return every deterministic graph invariant issue without performing I/O."""
    issues: list[ValidationIssue] = []
    indexes = build_indexes(snapshot)
    _validate_unique_ids(snapshot, issues)
    _validate_edges(snapshot, indexes, issues)
    _validate_required_ownership(snapshot, indexes, issues)
    _validate_current_versions(snapshot, indexes, issues)
    _validate_embedding_ownership(snapshot, indexes, issues)
    _validate_current_embedding_spaces(snapshot, indexes, issues)
    _validate_supersedes(snapshot, indexes, issues)
    _validate_contains_assets(snapshot, indexes, issues)
    _validate_empty_sha_status(snapshot, indexes, issues)
    return build_validation_result(issues)


def _issue(code: str, table: str, record_id: str, message: str) -> ValidationIssue:
    return ValidationIssue(
        severity="error", code=code, table=table, record_id=record_id, message=message
    )


def _validate_unique_ids(
    snapshot: GraphSnapshot, issues: list[ValidationIssue]
) -> None:
    for table_spec in NODE_SPECS:
        models = snapshot.__getattribute__(table_spec.snapshot_field)
        counts = Counter(model_id(model) for model in models)
        for identifier, count in counts.items():
            if count > 1:
                issues.append(
                    _issue(
                        "DUPLICATE_NODE_ID",
                        table_spec.table_name,
                        identifier,
                        f"id occurs {count} times",
                    )
                )
                issues.append(
                    _issue(
                        "AMBIGUOUS_NODE_ID",
                        table_spec.table_name,
                        identifier,
                        (
                            "unique-lookup dependent checks skip this ambiguous "
                            "identifier; row-level checks may still report issues"
                        ),
                    )
                )


def _validate_edges(
    snapshot: GraphSnapshot, indexes: GraphIndexes, issues: list[ValidationIssue]
) -> None:
    for relation_spec in RELATION_SPECS:
        relations = snapshot.__getattribute__(relation_spec.snapshot_field)
        counts = Counter((item.source_id, item.target_id) for item in relations)
        for (source_id, target_id), count in counts.items():
            edge_id = f"{source_id}->{target_id}"
            if count > 1:
                issues.append(
                    _issue(
                        "DUPLICATE_TYPED_EDGE",
                        relation_spec.table_name,
                        edge_id,
                        f"edge occurs {count} times",
                    )
                )
            if source_id not in indexes.node_ids[relation_spec.source_label]:
                issues.append(
                    _issue(
                        "MISSING_SOURCE_ENDPOINT",
                        relation_spec.table_name,
                        edge_id,
                        f"source is absent from {relation_spec.source_label}",
                    )
                )
            if target_id not in indexes.node_ids[relation_spec.target_label]:
                issues.append(
                    _issue(
                        "MISSING_TARGET_ENDPOINT",
                        relation_spec.table_name,
                        edge_id,
                        f"target is absent from {relation_spec.target_label}",
                    )
                )


def _validate_required_ownership(
    snapshot: GraphSnapshot, indexes: GraphIndexes, issues: list[ValidationIssue]
) -> None:
    _require_incoming(
        "Document",
        indexes.node_ids["Document"],
        index_outgoing(snapshot.has_source_types),
        "HAS_SOURCE_TYPE",
        issues,
    )
    _require_incoming(
        "DocumentVersion",
        indexes.node_ids["DocumentVersion"],
        indexes.version_documents,
        "HAS_VERSION",
        issues,
    )
    _require_incoming(
        "DocumentVersion",
        indexes.node_ids["DocumentVersion"],
        index_outgoing(snapshot.has_statuses),
        "HAS_STATUS",
        issues,
    )
    _require_incoming(
        "Chunk", indexes.node_ids["Chunk"], indexes.chunk_versions, "HAS_CHUNK", issues
    )
    _require_incoming(
        "Asset", indexes.node_ids["Asset"], indexes.asset_versions, "HAS_ASSET", issues
    )


def _require_incoming(
    table: str,
    identifiers: frozenset[str],
    owners: dict[str, tuple[str, ...]],
    relation_table: str,
    issues: list[ValidationIssue],
) -> None:
    for identifier in identifiers:
        count = len(owners.get(identifier, ()))
        if count != 1:
            issues.append(
                _issue(
                    "OWNERSHIP_COUNT",
                    relation_table,
                    identifier,
                    f"{table} requires exactly one owner; found {count}",
                )
            )


def _validate_current_versions(
    snapshot: GraphSnapshot, indexes: GraphIndexes, issues: list[ValidationIssue]
) -> None:
    current_by_document: Counter[str] = Counter()
    for version in snapshot.document_versions:
        if version.is_current:
            for document_id in indexes.version_documents.get(version.id, ()):
                current_by_document[document_id] += 1
    for document in snapshot.documents:
        current_count = current_by_document[document.id]
        if current_count > 1:
            issues.append(
                _issue(
                    "MULTIPLE_CURRENT_VERSIONS",
                    "Document",
                    document.id,
                    f"document has {current_count} current versions",
                )
            )
        if document.is_active and current_count != 1:
            issues.append(
                _issue(
                    "ACTIVE_DOCUMENT_CURRENT_COUNT",
                    "Document",
                    document.id,
                    (
                        "active document requires one current version; "
                        f"found {current_count}"
                    ),
                )
            )


def _validate_embedding_ownership(
    snapshot: GraphSnapshot, indexes: GraphIndexes, issues: list[ValidationIssue]
) -> None:
    chunk_owners = index_incoming(snapshot.has_embedding_chunks)
    asset_owners = index_incoming(snapshot.has_embedding_assets)
    for identifier in indexes.node_ids["Embedding"]:
        chunk_count = len(chunk_owners.get(identifier, ()))
        asset_count = len(asset_owners.get(identifier, ()))
        if chunk_count + asset_count != 1:
            issues.append(
                _issue(
                    "EMBEDDING_OWNER_COUNT",
                    "Embedding",
                    identifier,
                    "embedding requires one chunk or asset owner; "
                    f"found {chunk_count + asset_count}",
                )
            )
        if chunk_count and asset_count:
            issues.append(
                _issue(
                    "EMBEDDING_OWNER_KIND_CONFLICT",
                    "Embedding",
                    identifier,
                    "embedding is owned by both chunk and asset",
                )
            )
    _validate_embedding_purposes(snapshot, indexes, issues)


def _validate_embedding_purposes(
    snapshot: GraphSnapshot, indexes: GraphIndexes, issues: list[ValidationIssue]
) -> None:
    expected_relations = (
        ("HAS_EMBEDDING_CHUNK", snapshot.has_embedding_chunks, "chunk_retrieval"),
        (
            "HAS_EMBEDDING_ASSET",
            snapshot.has_embedding_assets,
            "asset_description_retrieval",
        ),
    )
    for relation_table, relations, expected_purpose in expected_relations:
        for relation in relations:
            embedding = indexes.embedding_by_id.get(relation.target_id)
            if embedding is None or embedding.purpose == expected_purpose:
                continue
            issues.append(
                _issue(
                    "EMBEDDING_PURPOSE_OWNER_MISMATCH",
                    relation_table,
                    f"{relation.source_id}->{relation.target_id}",
                    f"owner relation requires purpose {expected_purpose}",
                )
            )


def _validate_current_embedding_spaces(
    snapshot: GraphSnapshot, indexes: GraphIndexes, issues: list[ValidationIssue]
) -> None:
    current_versions = {
        item.id for item in snapshot.document_versions if item.is_current
    }
    current_chunks = _subjects_under_current(indexes.chunk_versions, current_versions)
    current_assets = _subjects_under_current(indexes.asset_versions, current_versions)
    grouped: Counter[tuple[str, str, EmbeddingIdentity]] = Counter()
    typed_relations = (
        ("chunk", snapshot.has_embedding_chunks, current_chunks),
        ("asset", snapshot.has_embedding_assets, current_assets),
    )
    for subject_kind, relations, current_subjects in typed_relations:
        for relation in relations:
            embedding = indexes.embedding_by_id.get(relation.target_id)
            if (
                relation.source_id not in current_subjects
                or embedding is None
                or not embedding.is_current
            ):
                continue
            identity = (
                embedding.provider,
                embedding.model,
                embedding.model_version,
                embedding.dimension,
            )
            grouped[(subject_kind, relation.source_id, identity)] += 1
    for (subject_kind, subject_id, identity), count in grouped.items():
        if count > 1:
            issues.append(
                _issue(
                    "MULTIPLE_CURRENT_EMBEDDINGS",
                    "Embedding",
                    f"{subject_kind}:{subject_id}",
                    f"subject has {count} current embeddings for identity {identity!r}",
                )
            )


def _subjects_under_current(
    owners: dict[str, tuple[str, ...]], current_versions: set[str]
) -> set[str]:
    return {
        subject_id
        for subject_id, version_ids in owners.items()
        if any(version_id in current_versions for version_id in version_ids)
    }


def _validate_supersedes(
    snapshot: GraphSnapshot, indexes: GraphIndexes, issues: list[ValidationIssue]
) -> None:
    versions_by_document: dict[str, list[graph.DocumentVersion]] = defaultdict(list)
    for version_id, document_ids in indexes.version_documents.items():
        version = indexes.version_by_id.get(version_id)
        if version is not None and len(document_ids) == 1:
            versions_by_document[document_ids[0]].append(version)
    expected: set[Edge] = set()
    for document_id, versions in versions_by_document.items():
        number_counts = Counter(version.version for version in versions)
        for version_number, count in number_counts.items():
            if count > 1:
                issues.append(
                    _issue(
                        "DUPLICATE_VERSION_NUMBER",
                        "DocumentVersion",
                        document_id,
                        f"version {version_number} occurs {count} times",
                    )
                )
        ordered = sorted(versions, key=lambda version: (version.version, version.id))
        for current in ordered:
            lower = [
                candidate
                for candidate in ordered
                if candidate.version < current.version
            ]
            if lower:
                greatest = max(
                    lower, key=lambda candidate: (candidate.version, candidate.id)
                )
                expected.add((current.id, greatest.id))
    actual = {(item.source_id, item.target_id) for item in snapshot.supersedes}
    for source_id, target_id in actual - expected:
        issues.append(
            _issue(
                "UNEXPECTED_SUPERSEDES",
                "SUPERSEDES",
                f"{source_id}->{target_id}",
                "edge is not the immediate same-document predecessor",
            )
        )
    for source_id, target_id in expected - actual:
        issues.append(
            _issue(
                "MISSING_SUPERSEDES",
                "SUPERSEDES",
                source_id,
                f"expected predecessor {target_id}",
            )
        )


def _validate_contains_assets(
    snapshot: GraphSnapshot, indexes: GraphIndexes, issues: list[ValidationIssue]
) -> None:
    for relation in snapshot.contains_assets:
        chunk_owners = set(indexes.chunk_versions.get(relation.source_id, ()))
        asset_owners = set(indexes.asset_versions.get(relation.target_id, ()))
        if chunk_owners and asset_owners and chunk_owners.isdisjoint(asset_owners):
            issues.append(
                _issue(
                    "CONTAINS_ASSET_VERSION_MISMATCH",
                    "CONTAINS_ASSET",
                    f"{relation.source_id}->{relation.target_id}",
                    "chunk and asset do not belong to the same version",
                )
            )


def _validate_empty_sha_status(
    snapshot: GraphSnapshot, indexes: GraphIndexes, issues: list[ValidationIssue]
) -> None:
    status_owners = index_outgoing(snapshot.has_statuses)
    for version in snapshot.document_versions:
        if version.sha256:
            continue
        status_ids = status_owners.get(version.id, ())
        status_names = [
            indexes.status_by_id[item].name
            for item in status_ids
            if item in indexes.status_by_id
        ]
        if status_names != ["error"]:
            issues.append(
                _issue(
                    "EMPTY_SHA_REQUIRES_ERROR_STATUS",
                    "DocumentVersion",
                    version.id,
                    "empty-SHA version must have status name exactly error",
                )
            )
