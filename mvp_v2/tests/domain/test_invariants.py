from __future__ import annotations

from mvp_v2.domain import graph
from mvp_v2.domain.invariants import validate_graph
from tests.factories import NOW, SHA, build_valid_snapshot


def codes(snapshot: object) -> set[str]:
    from mvp_v2.domain.snapshot import GraphSnapshot

    assert isinstance(snapshot, GraphSnapshot)
    return {issue.code for issue in validate_graph(snapshot).issues}


def test_full_snapshot_is_valid() -> None:
    assert validate_graph(build_valid_snapshot()).is_valid


def test_minimal_image_collection_with_no_chunks_is_valid() -> None:
    snapshot = build_valid_snapshot()
    image_only = snapshot.model_copy(
        update={
            "documents": (
                snapshot.documents[0].model_copy(
                    update={"content_format": "image_collection"}
                ),
            ),
            "chunks": (),
            "embeddings": (
                next(
                    item for item in snapshot.embeddings if item.id == "embedding-asset"
                ),
            ),
            "has_chunks": (),
            "contains_assets": (),
            "has_embedding_chunks": (),
        }
    )
    assert validate_graph(image_only).is_valid


def test_duplicate_node_ids_and_typed_edges_fail() -> None:
    snapshot = build_valid_snapshot()
    broken = snapshot.model_copy(
        update={
            "roles": (*snapshot.roles, snapshot.roles[0]),
            "for_roles": (*snapshot.for_roles, snapshot.for_roles[0]),
        }
    )
    assert {"DUPLICATE_NODE_ID", "DUPLICATE_TYPED_EDGE"} <= codes(broken)


def test_missing_relation_endpoints_fail_generically() -> None:
    snapshot = build_valid_snapshot()
    broken = snapshot.model_copy(
        update={
            "links_to": (
                graph.LinksTo.model_validate({"from": "missing", "to": "also-missing"}),
            )
        }
    )
    assert {"MISSING_SOURCE_ENDPOINT", "MISSING_TARGET_ENDPOINT"} <= codes(broken)


def test_every_required_owner_and_status_count_fails() -> None:
    snapshot = build_valid_snapshot()
    for field_name in (
        "has_source_types",
        "has_versions",
        "has_statuses",
        "has_chunks",
        "has_assets",
    ):
        broken = snapshot.model_copy(update={field_name: ()})
        assert "OWNERSHIP_COUNT" in codes(broken)


def test_active_document_requires_exactly_one_current_version() -> None:
    snapshot = build_valid_snapshot()
    inactive_version = snapshot.document_versions[0].model_copy(
        update={"is_current": False}
    )
    assert "ACTIVE_DOCUMENT_CURRENT_COUNT" in codes(
        snapshot.model_copy(update={"document_versions": (inactive_version,)})
    )
    duplicate = inactive_version.model_copy(
        update={"id": "version-two", "version": 2, "is_current": True}
    )
    first_current = snapshot.document_versions[0]
    broken = snapshot.model_copy(
        update={
            "document_versions": (first_current, duplicate),
            "has_versions": (
                *snapshot.has_versions,
                graph.HasVersion.model_validate(
                    {"from": "document", "to": "version-two"}
                ),
            ),
            "has_statuses": (
                *snapshot.has_statuses,
                graph.HasStatus.model_validate({"from": "version-two", "to": "status"}),
            ),
            "supersedes": (
                graph.Supersedes.model_validate(
                    {"from": "version-two", "to": "version"}
                ),
            ),
        }
    )
    assert "MULTIPLE_CURRENT_VERSIONS" in codes(broken)


def test_embedding_requires_exactly_one_owner_and_not_both_kinds() -> None:
    snapshot = build_valid_snapshot()
    no_owner = snapshot.model_copy(update={"has_embedding_chunks": ()})
    assert "EMBEDDING_OWNER_COUNT" in codes(no_owner)
    both = snapshot.model_copy(
        update={
            "has_embedding_assets": (
                *snapshot.has_embedding_assets,
                graph.HasEmbeddingAsset.model_validate(
                    {"from": "asset", "to": "embedding-chunk"}
                ),
            )
        }
    )
    assert "EMBEDDING_OWNER_KIND_CONFLICT" in codes(both)


def test_current_embedding_identity_handles_history_and_mixed_spaces() -> None:
    snapshot = build_valid_snapshot()
    chunk_embedding = next(
        item for item in snapshot.embeddings if item.id == "embedding-chunk"
    )
    duplicate = chunk_embedding.model_copy(update={"id": "embedding-duplicate"})
    duplicate_edge = graph.HasEmbeddingChunk.model_validate(
        {"from": "chunk", "to": "embedding-duplicate"}
    )
    broken = snapshot.model_copy(
        update={
            "embeddings": (*snapshot.embeddings, duplicate),
            "has_embedding_chunks": (*snapshot.has_embedding_chunks, duplicate_edge),
        }
    )
    assert "MULTIPLE_CURRENT_EMBEDDINGS" in codes(broken)
    historical = duplicate.model_copy(update={"is_current": False})
    other_space = duplicate.model_copy(
        update={"id": "embedding-other", "model_version": "v2"}
    )
    valid = snapshot.model_copy(
        update={
            "embeddings": (*snapshot.embeddings, historical, other_space),
            "has_embedding_chunks": (
                *snapshot.has_embedding_chunks,
                duplicate_edge,
                graph.HasEmbeddingChunk.model_validate(
                    {"from": "chunk", "to": "embedding-other"}
                ),
            ),
        }
    )
    assert validate_graph(valid).is_valid


def test_supersedes_preserves_version_gaps_and_rejects_wrong_edges() -> None:
    snapshot = build_valid_snapshot()
    current = snapshot.document_versions[0].model_copy(update={"version": 3})
    old = graph.DocumentVersion(
        id="version-old",
        version=1,
        sha256=SHA,
        local_path="documents/old.md",
        created_at=NOW,
        is_current=False,
        error="",
    )
    valid = snapshot.model_copy(
        update={
            "document_versions": (old, current),
            "has_versions": (
                *snapshot.has_versions,
                graph.HasVersion.model_validate(
                    {"from": "document", "to": "version-old"}
                ),
            ),
            "has_statuses": (
                *snapshot.has_statuses,
                graph.HasStatus.model_validate({"from": "version-old", "to": "status"}),
            ),
            "supersedes": (
                graph.Supersedes.model_validate(
                    {"from": "version", "to": "version-old"}
                ),
            ),
        }
    )
    assert validate_graph(valid).is_valid
    wrong = valid.model_copy(
        update={
            "supersedes": (
                graph.Supersedes.model_validate(
                    {"from": "version-old", "to": "version"}
                ),
            )
        }
    )
    assert {"UNEXPECTED_SUPERSEDES", "MISSING_SUPERSEDES"} <= codes(wrong)


def test_contains_asset_requires_same_owning_version() -> None:
    snapshot = build_valid_snapshot()
    second = snapshot.document_versions[0].model_copy(
        update={"id": "version-two", "version": 2, "is_current": False}
    )
    broken = snapshot.model_copy(
        update={
            "document_versions": (*snapshot.document_versions, second),
            "has_versions": (
                *snapshot.has_versions,
                graph.HasVersion.model_validate(
                    {"from": "document", "to": "version-two"}
                ),
            ),
            "has_statuses": (
                *snapshot.has_statuses,
                graph.HasStatus.model_validate({"from": "version-two", "to": "status"}),
            ),
            "has_assets": (
                graph.HasAsset.model_validate({"from": "version-two", "to": "asset"}),
            ),
            "supersedes": (
                graph.Supersedes.model_validate(
                    {"from": "version-two", "to": "version"}
                ),
            ),
        }
    )
    assert "CONTAINS_ASSET_VERSION_MISMATCH" in codes(broken)


def test_empty_sha_requires_status_named_exactly_error() -> None:
    snapshot = build_valid_snapshot()
    errored = graph.DocumentVersion(
        id="error-version",
        version=2,
        sha256="",
        local_path="",
        created_at=NOW,
        is_current=False,
        error="failed",
    )
    broken = snapshot.model_copy(
        update={
            "document_versions": (*snapshot.document_versions, errored),
            "has_versions": (
                *snapshot.has_versions,
                graph.HasVersion.model_validate(
                    {"from": "document", "to": "error-version"}
                ),
            ),
            "has_statuses": (
                *snapshot.has_statuses,
                graph.HasStatus.model_validate(
                    {"from": "error-version", "to": "status"}
                ),
            ),
            "supersedes": (
                graph.Supersedes.model_validate(
                    {"from": "error-version", "to": "version"}
                ),
            ),
        }
    )
    assert "EMPTY_SHA_REQUIRES_ERROR_STATUS" in codes(broken)


def test_embedding_purpose_must_match_typed_owner_relation() -> None:
    snapshot = build_valid_snapshot()
    embeddings = tuple(
        item.model_copy(
            update={
                "purpose": "asset_description_retrieval"
                if item.id == "embedding-chunk"
                else "chunk_retrieval"
            }
        )
        for item in snapshot.embeddings
    )
    result = validate_graph(snapshot.model_copy(update={"embeddings": embeddings}))
    mismatches = [
        issue
        for issue in result.issues
        if issue.code == "EMBEDDING_PURPOSE_OWNER_MISMATCH"
    ]
    assert len(mismatches) == 2


def test_typed_subject_identity_separates_equal_chunk_and_asset_ids() -> None:
    snapshot = build_valid_snapshot()
    shared_chunk = snapshot.chunks[0].model_copy(update={"id": "shared"})
    shared_asset = snapshot.assets[0].model_copy(update={"id": "shared"})
    chunk_edge = graph.HasEmbeddingChunk.model_validate(
        {"from": "shared", "to": "embedding-chunk"}
    )
    asset_edge = graph.HasEmbeddingAsset.model_validate(
        {"from": "shared", "to": "embedding-asset"}
    )
    collision = snapshot.model_copy(
        update={
            "chunks": (shared_chunk,),
            "assets": (shared_asset,),
            "has_chunks": (
                graph.HasChunk.model_validate({"from": "version", "to": "shared"}),
            ),
            "has_assets": (
                graph.HasAsset.model_validate({"from": "version", "to": "shared"}),
            ),
            "contains_assets": (
                graph.ContainsAsset.model_validate({"from": "shared", "to": "shared"}),
            ),
            "has_embedding_chunks": (chunk_edge,),
            "has_embedding_assets": (asset_edge,),
        }
    )
    assert validate_graph(collision).is_valid
    historical = next(
        item for item in collision.embeddings if item.id == "embedding-chunk"
    ).model_copy(update={"id": "embedding-history", "is_current": False})
    with_history = collision.model_copy(
        update={
            "embeddings": (*collision.embeddings, historical),
            "has_embedding_chunks": (
                *collision.has_embedding_chunks,
                graph.HasEmbeddingChunk.model_validate(
                    {"from": "shared", "to": "embedding-history"}
                ),
            ),
        }
    )
    assert validate_graph(with_history).is_valid


def test_duplicate_ids_report_ambiguity_without_dependent_crash() -> None:
    snapshot = build_valid_snapshot()
    duplicate = snapshot.embeddings[0].model_copy(update={"purpose": "chunk_retrieval"})
    broken = snapshot.model_copy(
        update={"embeddings": (*snapshot.embeddings, duplicate)}
    )

    first = validate_graph(broken)
    second = validate_graph(broken)

    assert first == second
    assert {"DUPLICATE_NODE_ID", "AMBIGUOUS_NODE_ID"} <= {
        issue.code for issue in first.issues
    }


def test_conflicting_duplicate_versions_are_deterministic_and_do_not_crash() -> None:
    snapshot = build_valid_snapshot()
    conflicting = snapshot.document_versions[0].model_copy(
        update={"version": 99, "is_current": False}
    )
    broken = snapshot.model_copy(
        update={"document_versions": (*snapshot.document_versions, conflicting)}
    )

    first = validate_graph(broken)
    second = validate_graph(broken)

    assert first == second
    assert not first.is_valid
    ambiguity = next(
        issue for issue in first.issues if issue.code == "AMBIGUOUS_NODE_ID"
    )
    assert "unique-lookup dependent checks skip" in ambiguity.message
    assert "row-level checks may still report" in ambiguity.message
