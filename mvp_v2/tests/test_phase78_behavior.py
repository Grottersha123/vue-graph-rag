from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, cast

import pytest

from mvp_v2.adapters.csv.snapshot import write_snapshot
from mvp_v2.adapters.ladybug import (
    LadybugAnn,
    LadybugBuildError,
    load_verified_dump,
)
from mvp_v2.application.deletion import apply_deletion, preview_deletion
from mvp_v2.application.deletion_orchestration import DeletionService
from mvp_v2.application.dto.deletion import (
    DatabaseIdentity,
    DeletionCommand,
    GraphNodeRef,
)
from mvp_v2.application.dto.importing import canonical_json_bytes
from mvp_v2.application.dto.retrieval import (
    EmbeddingPurpose,
    EmbeddingSpaceIdentity,
    SearchRequest,
    SubjectKind,
    SubjectRef,
    VectorHit,
    VectorQuery,
)
from mvp_v2.application.ingestion_contracts import ModelAuthorization
from mvp_v2.application.manifests import (
    DATABASE_FORMAT,
    DUMP_FORMAT,
    DatabaseManifest,
    DumpManifest,
    TableCount,
    compute_database_build_id,
    compute_dump_run_id,
)
from mvp_v2.application.projection import active_projection
from mvp_v2.application.retrieval import SearchError, SearchService
from mvp_v2.domain import graph
from mvp_v2.domain.invariants import validate_graph
from mvp_v2.domain.schema_registry import (
    GRAPH_SCHEMA_VERSION,
    TABLE_NAMES,
    GraphNodeTableName,
)
from mvp_v2.domain.snapshot import GraphSnapshot
from tests.factories import NOW, build_valid_snapshot

SPACE = EmbeddingSpaceIdentity(
    provider="local", model="embed", model_version="v1", dimension=2
)


def _manifest(
    space: EmbeddingSpaceIdentity = SPACE, *, embedding_count: int = 0
) -> DatabaseManifest:
    counts = tuple(
        TableCount(table=name, count=embedding_count if name == "Embedding" else 0)
        for name in TABLE_NAMES
    )
    values: dict[str, Any] = dict(
        database_name="active",
        built_at=NOW,
        source_dump_number=1,
        source_dump_published_at=NOW,
        source_dump_run_id="a" * 64,
        embedding_space=space,
        vision_provider="local",
        vision_model="vision",
        vision_model_version="v1",
        row_counts=counts,
    )
    return DatabaseManifest(
        format=DATABASE_FORMAT,
        graph_schema_version=GRAPH_SCHEMA_VERSION,
        build_id=compute_database_build_id(**values),
        **values,
    )


def _command(table: str, identifier: str) -> DeletionCommand:
    return DeletionCommand(
        target=GraphNodeRef(table_name=cast(GraphNodeTableName, table), id=identifier)
    )


def test_projection_excludes_inactive_historical_and_wrong_embeddings() -> None:
    source = build_valid_snapshot()
    inactive = source.documents[0].model_copy(
        update={"id": "inactive", "is_active": False}
    )
    historical = source.document_versions[0].model_copy(
        update={"id": "historical", "is_current": False}
    )
    noncurrent = source.embeddings[0].model_copy(
        update={"id": "old", "is_current": False}
    )
    other = source.embeddings[0].model_copy(
        update={"id": "other", "model_version": "v2"}
    )
    summary_embedding = source.embeddings[0].model_copy(
        update={"id": "document-summary", "summary_vector": ()}
    )
    mixed = source.model_copy(
        update={
            "documents": (*source.documents, inactive),
            "document_versions": (*source.document_versions, historical),
            "embeddings": (
                *source.embeddings,
                noncurrent,
                other,
                summary_embedding,
            ),
            "has_summary_embeddings": (
                graph.HasSummaryEmbedding.model_validate(
                    {"from": "document", "to": "document-summary"}
                ),
            ),
        }
    )
    projected = active_projection(mixed, SPACE)
    assert {item.id for item in projected.documents} == {"document"}
    assert {item.id for item in projected.document_versions} == {"version"}
    assert {item.id for item in projected.embeddings} == {
        "embedding-chunk",
        "embedding-asset",
    }
    assert validate_graph(projected).is_valid


class _Result:
    def __init__(self, rows: list[list[object]]) -> None:
        self.rows = rows

    def has_next(self) -> bool:
        return bool(self.rows)

    def get_next(self) -> list[object]:
        return self.rows.pop(0)

    def close(self) -> None:
        pass


class _Connection:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def execute(
        self, query: str, parameters: dict[str, object] | None = None
    ) -> _Result:
        self.calls.append((query, parameters))
        if "QUERY_VECTOR_INDEX" in query:
            return _Result(
                [
                    ["embedding-chunk", 0.25],
                    ["embedding-asset", 0.25],
                ]
            )
        embedding_id = str((parameters or {}).get("id", ""))
        if "HAS_EMBEDDING_CHUNK" in query and embedding_id == "embedding-chunk":
            return _Result([["chunk"]])
        if "HAS_EMBEDDING_ASSET" in query and embedding_id == "embedding-asset":
            return _Result([["asset"]])
        return _Result([])


def test_ann_rejects_identity_and_constructed_bad_dimension_before_execute() -> None:
    connection = _Connection()
    ann = LadybugAnn(connection, _manifest())
    wrong = SPACE.model_copy(update={"model_version": "other"})
    with pytest.raises(ValueError):
        ann.query(
            VectorQuery(
                space=wrong,
                vector=(1.0, 2.0),
                purposes=(EmbeddingPurpose.CHUNK_RETRIEVAL,),
                limit=1,
            )
        )
    bad = VectorQuery.model_construct(
        space=SPACE,
        vector=(1.0,),
        purposes=(EmbeddingPurpose.CHUNK_RETRIEVAL,),
        limit=1,
    )
    with pytest.raises(ValueError):
        ann.query(bad)
    assert connection.calls == []


def test_ann_query_syntax_filtering_and_deterministic_finite_hits() -> None:
    connection = _Connection()
    ann = LadybugAnn(connection, _manifest())
    hits = ann.query(
        VectorQuery(
            space=SPACE,
            vector=(1.0, 2.0),
            purposes=(
                EmbeddingPurpose.CHUNK_RETRIEVAL,
                EmbeddingPurpose.ASSET_DESCRIPTION_RETRIEVAL,
            ),
            limit=2,
        )
    )
    assert connection.calls[0] == ("LOAD vector", None)
    vector_call = next(
        call for call in connection.calls if "QUERY_VECTOR_INDEX" in call[0]
    )
    assert "efs := 32" in vector_call[0]
    assert " WHERE " not in vector_call[0]
    assert vector_call[1] == {"vector": [1.0, 2.0], "k": 2}
    assert [(hit.embedding_id, hit.score) for hit in hits] == [
        ("embedding-asset", 0.75),
        ("embedding-chunk", 0.75),
    ]


def test_ann_subset_purpose_uses_bounded_overfetch_and_stops_at_limit() -> None:
    connection = _Connection()
    ann = LadybugAnn(connection, _manifest(embedding_count=50_000))

    hits = ann.query(
        VectorQuery(
            space=SPACE,
            vector=(1.0, 2.0),
            purposes=(EmbeddingPurpose.ASSET_DESCRIPTION_RETRIEVAL,),
            limit=1,
        )
    )

    vector_call = next(
        call for call in connection.calls if "QUERY_VECTOR_INDEX" in call[0]
    )
    ownership_calls = [call for call in connection.calls if "HAS_EMBEDDING_" in call[0]]
    assert vector_call[1] == {"vector": [1.0, 2.0], "k": 10}
    assert len(ownership_calls) == 2
    assert [hit.embedding_id for hit in hits] == ["embedding-asset"]


class _Model:
    def __init__(self, vectors: tuple[tuple[float, ...], ...]) -> None:
        self.vectors, self.calls = vectors, 0

    def embed(
        self, *, texts: tuple[str, ...], model: str
    ) -> tuple[tuple[float, ...], ...]:
        self.calls += 1
        return self.vectors


class _Ann:
    def query(self, request: VectorQuery) -> tuple[VectorHit, ...]:
        return (
            VectorHit(
                subject=SubjectRef(kind=SubjectKind.CHUNK, id="chunk"),
                embedding_id="embedding-chunk",
                purpose=EmbeddingPurpose.CHUNK_RETRIEVAL,
                score=0.8,
            ),
            VectorHit(
                subject=SubjectRef(kind=SubjectKind.ASSET, id="asset"),
                embedding_id="embedding-asset",
                purpose=EmbeddingPurpose.ASSET_DESCRIPTION_RETRIEVAL,
                score=0.7,
            ),
        )


def _search_request(
    space: EmbeddingSpaceIdentity = SPACE, allow: bool = True
) -> SearchRequest:
    return SearchRequest(
        question="question",
        space=space,
        purposes=(
            EmbeddingPurpose.CHUNK_RETRIEVAL,
            EmbeddingPurpose.ASSET_DESCRIPTION_RETRIEVAL,
        ),
        limit=2,
        authorization=ModelAuthorization(allow=allow, operation="embedding"),
    )


def test_search_auth_and_identity_fail_before_model() -> None:
    model = _Model(((1.0, 2.0),))
    service = SearchService(model, _Ann(), _manifest(), build_valid_snapshot())
    with pytest.raises(SearchError):
        service.search(_search_request(allow=False))
    with pytest.raises(SearchError):
        service.search(_search_request(SPACE.model_copy(update={"model_version": "x"})))
    assert model.calls == 0


def test_search_maps_bad_provider_and_hydrates_chunk_asset_citations() -> None:
    bad = SearchService(_Model(((1.0,),)), _Ann(), _manifest(), build_valid_snapshot())
    with pytest.raises(SearchError, match="embedding response is invalid"):
        bad.search(_search_request())
    evidence = SearchService(
        _Model(((1.0, 2.0),)), _Ann(), _manifest(), build_valid_snapshot()
    ).search(_search_request())
    assert [
        (
            item.subject.kind,
            item.text,
            item.document_id,
            item.version_id,
            item.local_path,
        )
        for item in evidence
    ] == [
        (SubjectKind.CHUNK, "hello\n\ndesc", "document", "version", "documents/a.md"),
    ]
    assert evidence[0].matched_chunk_position == 0
    assert evidence[0].document_chunk_count == 1
    assert evidence[0].context_scope == "full_document"
    assert all(
        item.statuses == ("ready",)
        and item.roles == ("student",)
        and item.processes == ("onboarding",)
        for item in evidence
    )


def test_search_hydrates_multiline_quiz_chunk() -> None:
    snapshot = build_valid_snapshot()
    text = "1. Что нужно для начала?\n- заполнить файл\n- просто ожидать"
    chunk = snapshot.chunks[0].model_copy(
        update={
            "content": text,
            "content_hash": hashlib.sha256(text.encode()).hexdigest(),
        }
    )

    evidence = SearchService(
        _Model(((1.0, 2.0),)),
        _Ann(),
        _manifest(),
        snapshot.model_copy(update={"chunks": (chunk,)}),
    ).search(_search_request())

    assert evidence[0].text == text + "\n\ndesc"


def _three_versions() -> GraphSnapshot:
    source = build_valid_snapshot()
    base = source.document_versions[0]
    versions = (
        base.model_copy(update={"id": "v1", "version": 1, "is_current": False}),
        base.model_copy(update={"id": "v2", "version": 2, "is_current": False}),
        base.model_copy(update={"id": "v3", "version": 3, "is_current": True}),
    )
    edges = tuple(
        graph.HasVersion.model_validate({"from": "document", "to": item.id})
        for item in versions
    )
    supersedes = (
        graph.Supersedes.model_validate({"from": "v2", "to": "v1"}),
        graph.Supersedes.model_validate({"from": "v3", "to": "v2"}),
    )
    # Keep evidence on v2 so promotion after deleting v3 remains valid.
    return source.model_copy(
        update={
            "document_versions": versions,
            "has_versions": edges,
            "supersedes": supersedes,
            "has_statuses": tuple(
                graph.HasStatus.model_validate({"from": version_id, "to": "status"})
                for version_id in ("v1", "v2", "v3")
            ),
            "has_chunks": (
                graph.HasChunk.model_validate({"from": "v2", "to": "chunk"}),
            ),
            "has_assets": (
                graph.HasAsset.model_validate({"from": "v2", "to": "asset"}),
            ),
        }
    )


def test_delete_current_version_promotes_immediate_lower_and_rebuilds_gap_chain() -> (
    None
):
    result = apply_deletion(_three_versions(), _command("DocumentVersion", "v3"))
    assert [(item.id, item.is_current) for item in result.document_versions] == [
        ("v1", False),
        ("v2", True),
    ]
    assert [(item.source_id, item.target_id) for item in result.supersedes] == [
        ("v2", "v1")
    ]
    assert validate_graph(result).is_valid


def test_delete_last_version_deactivates_document_and_reports_paths() -> None:
    source = build_valid_snapshot()
    command = _command("DocumentVersion", "version")
    result = apply_deletion(source, command)
    impact = preview_deletion(source, command)
    assert result.documents[0].is_active is False
    assert impact.referenced_physical_paths == ("documents/a.md", "images/a.png")
    assert validate_graph(result).is_valid


class _Publisher:
    def __init__(self) -> None:
        self.called = False

    def publish(self, *, snapshot: GraphSnapshot) -> DumpManifest:
        self.called = True
        files = ()
        return DumpManifest.model_construct(
            format=DUMP_FORMAT,
            graph_schema_version=GRAPH_SCHEMA_VERSION,
            dump_number=2,
            published_at=NOW,
            run_id=compute_dump_run_id(2, NOW, files),
            files=files,
        )


class _Builder:
    def build(self, *, dump: DumpManifest, target: Path) -> DatabaseManifest:
        raise LadybugBuildError("expected")


class _Catalog:
    def __init__(self, identity: DatabaseIdentity) -> None:
        self.identity = identity
        self.marked = False
        self.switches = 0
        self.path = Path("old.lbdb")

    def active(self) -> DatabaseIdentity:
        return self.identity

    def identities(self) -> tuple[DatabaseIdentity, ...]:
        return (self.identity,)

    def mark_stale_before_dump(self, dump_number: int) -> None:
        self.marked = True

    def switch(self, identity: DatabaseIdentity, path: Path) -> None:
        self.switches += 1


def test_rebuild_failure_after_publish_marks_old_stale_without_switch(
    tmp_path: Path,
) -> None:
    identity = DatabaseIdentity(database_name="active", build_id="a" * 64)
    catalog = _Catalog(identity)
    publisher = _Publisher()
    service = DeletionService(publisher, _Builder(), catalog, tmp_path)
    source = build_valid_snapshot()
    command = _command("Embedding", "embedding-chunk")
    result = service.confirm(source, command, service.preview(source, command))
    assert (
        publisher.called
        and catalog.marked
        and catalog.switches == 0
        and catalog.path == Path("old.lbdb")
    )
    assert (
        result.status == "pending"
        and result.active_database == identity
        and result.stale_databases == (identity,)
    )


def test_asset_and_embedding_deletion_semantics_and_paths() -> None:
    source = build_valid_snapshot()
    asset_command = _command("Asset", "asset")
    asset_result = apply_deletion(source, asset_command)
    asset_impact = preview_deletion(source, asset_command)
    assert {item.id for item in asset_result.chunks} == {"chunk"}
    assert {item.id for item in asset_result.embeddings} == {"embedding-chunk"}
    assert asset_impact.referenced_physical_paths == ("images/a.png",)
    assert asset_impact == preview_deletion(source, asset_command)
    embedding_impact = preview_deletion(
        source, _command("Embedding", "embedding-chunk")
    )
    assert [(item.table_name, item.id) for item in embedding_impact.affected_nodes] == [
        ("Embedding", "embedding-chunk")
    ]
    assert embedding_impact.referenced_physical_paths == ()
    assert validate_graph(asset_result).is_valid


def test_document_cascade_removes_owned_graph_and_links_but_keeps_taxonomy() -> None:
    base = build_valid_snapshot()
    summary_embedding = base.embeddings[0].model_copy(
        update={"id": "embedding-document", "summary_vector": ()}
    )
    source = base.model_copy(
        update={
            "embeddings": (*base.embeddings, summary_embedding),
            "has_summary_embeddings": (
                graph.HasSummaryEmbedding.model_validate(
                    {"from": "document", "to": "embedding-document"}
                ),
            ),
            "links_to": (
                graph.LinksTo.model_validate({"from": "document", "to": "document"}),
            ),
        }
    )
    command = _command("Document", "document")
    result = apply_deletion(source, command)
    impact = preview_deletion(source, command)
    assert not result.documents and not result.document_versions
    assert not result.chunks and not result.assets and not result.embeddings
    assert result.source_types and result.roles and result.processes and result.statuses
    assert any(item.relation_type == "LINKS_TO" for item in impact.affected_relations)
    assert validate_graph(result).is_valid


def test_dump_loader_rejects_foreign_current_schema_profile(
    tmp_path: Path,
) -> None:
    entries = write_snapshot(tmp_path, build_valid_snapshot())
    foreign_profile = "f" * 64
    run_id = compute_dump_run_id(
        1,
        NOW,
        entries,
        schema_profile_sha256=foreign_profile,
    )
    manifest = DumpManifest(
        format=DUMP_FORMAT,
        graph_schema_version=GRAPH_SCHEMA_VERSION,
        schema_profile_sha256=foreign_profile,
        dump_number=1,
        published_at=NOW,
        run_id=run_id,
        files=entries,
    )
    (tmp_path / "manifest.json").write_bytes(
        canonical_json_bytes(manifest.canonical_payload())
    )

    with pytest.raises(LadybugBuildError, match="verification failed"):
        load_verified_dump(tmp_path)


def test_database_name_rejects_control_whitespace_before_dump_access(
    tmp_path: Path,
) -> None:
    from mvp_v2.adapters.ladybug import build_database

    with pytest.raises(LadybugBuildError, match="safe path segment"):
        build_database(
            dump_path=tmp_path / "missing",
            target=tmp_path / "safe.lbdb",
            database_name="unsafe\nname",
            space=SPACE,
            vision_provider="local",
            vision_model="vision",
            vision_model_version="v1",
        )
