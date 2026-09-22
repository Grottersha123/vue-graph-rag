from __future__ import annotations

import hashlib
from typing import Any

import pytest
from pydantic import ValidationError

from mvp_v2.adapters.ladybug import LadybugAnn
from mvp_v2.application.dto.chat import ChatAnswer, Citation
from mvp_v2.application.dto.retrieval import (
    ContextScope,
    EmbeddingPurpose,
    EmbeddingSpaceIdentity,
    SearchRequest,
    SubjectKind,
    SubjectRef,
    VectorHit,
    VectorQuery,
)
from mvp_v2.application.graph_snapshot_operations.document_view import (
    build_document_view,
)
from mvp_v2.application.graph_snapshot_operations.title_backfill import (
    fill_missing_document_titles,
)
from mvp_v2.application.ingestion_contracts import ModelAuthorization
from mvp_v2.application.manifests import (
    DATABASE_FORMAT,
    DatabaseManifest,
    TableCount,
    compute_database_build_id,
)
from mvp_v2.application.rag.agent.chat import MAX_EVIDENCE_ITEMS, ChatService
from mvp_v2.application.rag.retrieval.retrieval import SearchService, _hydrate
from mvp_v2.domain import graph
from mvp_v2.domain.graph import Document
from mvp_v2.domain.schema_registry import GRAPH_SCHEMA_VERSION, TABLE_NAMES
from mvp_v2.domain.snapshot import GraphSnapshot
from tests.factories import NOW, SHA, build_valid_snapshot


def _chunk(identifier: str, position: int, content: str) -> graph.Chunk:
    return graph.Chunk(
        id=identifier,
        position=position,
        content=content,
        content_hash=hashlib.sha256(content.encode()).hexdigest(),
    )


def test_document_view_assembles_current_chunks_and_assets() -> None:
    snapshot = build_valid_snapshot()

    view = build_document_view(snapshot, "document")

    assert view.document.title == "Title"
    assert view.version.id == "version"
    assert view.content == "hello"
    assert view.chunk_count == 1
    assert [asset.id for asset in view.assets] == ["asset"]


def test_hydration_uses_bounded_neighbor_window_for_large_document() -> None:
    snapshot = build_valid_snapshot()
    chunks = tuple(
        _chunk(f"chunk-{position}", position, str(position) * 2_000)
        for position in range(4)
    )
    has_chunks = tuple(
        graph.HasChunk.model_validate({"from": "version", "to": chunk.id})
        for chunk in chunks
    )
    snapshot = snapshot.model_copy(update={"chunks": chunks, "has_chunks": has_chunks})
    hit = VectorHit(
        subject=SubjectRef(kind=SubjectKind.CHUNK, id="chunk-1"),
        embedding_id="embedding-chunk",
        purpose=EmbeddingPurpose.CHUNK_RETRIEVAL,
        score=0.75,
    )

    evidence = _hydrate(snapshot, hit)

    assert evidence.context_scope == "chunk_window"
    assert evidence.matched_chunk_position == 1
    assert evidence.document_chunk_count == 4
    assert len(evidence.text) == 1_400
    assert "1" * 1_000 in evidence.text


class _EmbedModel:
    def __init__(self, vector: tuple[float, float]) -> None:
        self.vector = vector

    def embed(
        self, texts: tuple[str, ...], model: str
    ) -> tuple[tuple[float, float], ...]:
        return (self.vector,)


class _ScriptedAnn:
    def __init__(self, hits: tuple[VectorHit, ...]) -> None:
        self.hits = hits
        self.limits: list[int] = []

    def query(self, request: VectorQuery) -> tuple[VectorHit, ...]:
        self.limits.append(request.limit)
        return self.hits


class _EchoModel:
    def generate(self, *, prompt: str, model: str) -> str:
        tokens = " ".join(f"[{n}]" for n in range(1, MAX_EVIDENCE_ITEMS + 1))
        return f"Answer {tokens}"


SPACE = EmbeddingSpaceIdentity(
    provider="local", model="embed", model_version="v1", dimension=2
)


def _manifest() -> DatabaseManifest:
    counts = tuple(TableCount(table=name, count=0) for name in TABLE_NAMES)
    values: dict[str, Any] = dict(
        database_name="active",
        built_at=NOW,
        source_dump_number=1,
        source_dump_published_at=NOW,
        source_dump_run_id="a" * 64,
        embedding_space=SPACE,
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


def _identity() -> EmbeddingSpaceIdentity:
    return EmbeddingSpaceIdentity(
        provider="local", model="embed", model_version="v1", dimension=2
    )


def _hit(identifier: str, score: float) -> VectorHit:
    return VectorHit(
        subject=SubjectRef(kind=SubjectKind.CHUNK, id=identifier),
        embedding_id=f"embedding-{identifier}",
        purpose=EmbeddingPurpose.CHUNK_RETRIEVAL,
        score=score,
    )


def _multi_version_snapshot() -> GraphSnapshot:
    base = build_valid_snapshot()
    extra_documents = (
        graph.Document(
            id="document-b",
            title="Title B",
            source_name="",
            content_format="markdown",
            source_url="",
            external_id="",
            is_active=True,
        ),
        graph.Document(
            id="document-c",
            title="Title C",
            source_name="",
            content_format="markdown",
            source_url="",
            external_id="",
            is_active=True,
        ),
    )
    extra_versions = (
        graph.DocumentVersion(
            id="version-b",
            version=1,
            sha256=SHA,
            local_path="documents/b.md",
            created_at=NOW,
            is_current=True,
            error="",
        ),
        graph.DocumentVersion(
            id="version-c",
            version=1,
            sha256=SHA,
            local_path="documents/c.md",
            created_at=NOW,
            is_current=True,
            error="",
        ),
    )
    chunk_b = _chunk("chunk-b", 0, "content-b")
    chunk_c = _chunk("chunk-c", 0, "content-c")
    return base.model_copy(
        update={
            "documents": base.documents + extra_documents,
            "document_versions": base.document_versions + extra_versions,
            "chunks": base.chunks + (chunk_b, chunk_c),
            "has_versions": base.has_versions
            + (
                graph.HasVersion.model_validate(
                    {"from": "document-b", "to": "version-b"}
                ),
                graph.HasVersion.model_validate(
                    {"from": "document-c", "to": "version-c"}
                ),
            ),
            "has_chunks": base.has_chunks
            + (
                graph.HasChunk.model_validate({"from": "version-b", "to": "chunk-b"}),
                graph.HasChunk.model_validate({"from": "version-c", "to": "chunk-c"}),
            ),
        }
    )


class _LadybugQueryResult:
    def __init__(self, rows: list[list[object]]) -> None:
        self._rows = iter(rows)
        self._next: list[object] | None = None

    def has_next(self) -> bool:
        try:
            self._next = next(self._rows)
        except StopIteration:
            return False
        return True

    def get_next(self) -> list[object]:
        assert self._next is not None
        row = self._next
        self._next = None
        return row

    def close(self) -> None:
        return None


class _LadybugConnection:
    def execute(
        self,
        query: str,
        parameters: dict[str, object] | None = None,
    ) -> _LadybugQueryResult:
        del parameters
        if query == "LOAD vector":
            return _LadybugQueryResult([])
        if query.startswith("CALL QUERY_VECTOR_INDEX"):
            return _LadybugQueryResult([["embedding-chunk", 0.2]])
        if "HAS_EMBEDDING_CHUNK" in query:
            return _LadybugQueryResult([["chunk"]])
        raise AssertionError(f"unexpected query: {query}")


def test_ladybug_adapter_and_search_service_share_current_dto_types() -> None:
    service = SearchService(
        _EmbedModel((1.0, 2.0)),
        LadybugAnn(_LadybugConnection(), _manifest()),
        _manifest(),
        build_valid_snapshot(),
    )

    evidence = service.search(
        SearchRequest(
            question="question",
            space=_identity(),
            purposes=(EmbeddingPurpose.CHUNK_RETRIEVAL,),
            limit=1,
            authorization=ModelAuthorization(allow=True, operation="embedding"),
        )
    )

    assert len(evidence) == 1
    assert isinstance(evidence[0].subject, SubjectRef)
    assert evidence[0].subject.id == "chunk"
    assert evidence[0].score == pytest.approx(0.8)


def test_duplicate_heavy_topk_refills_distinct_versions() -> None:
    ann = _ScriptedAnn(
        (
            _hit("chunk", 0.9),
            _hit("chunk", 0.85),
            _hit("chunk-b", 0.8),
            _hit("chunk-c", 0.7),
        )
    )
    service = SearchService(
        _EmbedModel((1.0, 2.0)), ann, _manifest(), _multi_version_snapshot()
    )

    evidence = service.search(
        SearchRequest(
            question="question",
            space=_identity(),
            purposes=(EmbeddingPurpose.CHUNK_RETRIEVAL,),
            limit=3,
            authorization=ModelAuthorization(allow=True, operation="embedding"),
        )
    )

    assert ann.limits == [9]
    assert [item.version_id for item in evidence] == [
        "version",
        "version-b",
        "version-c",
    ]
    assert evidence[0].text == "hello"


def test_search_filters_hits_below_minimum_similarity_score() -> None:
    ann = _ScriptedAnn(
        (
            _hit("chunk", 0.55),
            _hit("chunk-b", 0.549999),
        )
    )
    service = SearchService(
        _EmbedModel((1.0, 2.0)), ann, _manifest(), _multi_version_snapshot()
    )

    evidence = service.search(
        SearchRequest(
            question="question",
            space=_identity(),
            purposes=(EmbeddingPurpose.CHUNK_RETRIEVAL,),
            limit=2,
            authorization=ModelAuthorization(allow=True, operation="embedding"),
        )
    )

    assert [(item.version_id, item.score) for item in evidence] == [("version", 0.55)]


def test_search_request_validates_minimum_similarity_score() -> None:
    with pytest.raises(ValidationError):
        SearchRequest(
            question="question",
            space=_identity(),
            purposes=(EmbeddingPurpose.CHUNK_RETRIEVAL,),
            limit=2,
            authorization=ModelAuthorization(allow=True, operation="embedding"),
            minimum_score=1.01,
        )


def test_search_limit_100_requests_at_least_100_ann_candidates() -> None:
    ann = _ScriptedAnn((_hit("chunk", 0.9),))

    service = SearchService(
        _EmbedModel((1.0, 2.0)), ann, _manifest(), build_valid_snapshot()
    )

    service.search(
        SearchRequest(
            question="question",
            space=_identity(),
            purposes=(EmbeddingPurpose.CHUNK_RETRIEVAL,),
            limit=100,
            authorization=ModelAuthorization(allow=True, operation="embedding"),
        )
    )

    assert ann.limits == [100]


def test_chat_truncates_evidence_to_configured_maximum() -> None:
    from tests.test_phase9_closure import Search, evidence, request

    items = tuple(
        evidence().model_copy(update={"embedding_id": f"e{number}"})
        for number in range(10)
    )

    answer = ChatService(Search(items), _EchoModel()).answer(request())

    assert len(answer.evidence) == MAX_EVIDENCE_ITEMS


def test_output_contracts_allow_multiline_and_reject_blank_or_controls() -> None:
    from tests.test_phase9_closure import evidence as sample_evidence

    base = sample_evidence().model_dump()
    multiline = sample_evidence().model_validate(
        {**base, "text": "line one\nline two\ttabbed"}
    )
    assert "\n" in multiline.text
    with pytest.raises(ValidationError):
        sample_evidence().model_validate({**base, "text": ""})
    with pytest.raises(ValidationError):
        sample_evidence().model_validate({**base, "text": "bad\x00text"})

    valid = sample_evidence()

    def citation(context_scope: ContextScope) -> Citation:
        return Citation(
            number=1,
            evidence_id=valid.embedding_id,
            score=valid.score,
            matched_chunk_position=None,
            document_chunk_count=1,
            context_scope=context_scope,
            document_id=valid.document_id,
            document_title=valid.document_title,
            version_id=valid.version_id,
            version=valid.version,
            source_url=valid.source_url,
            local_path=valid.local_path,
        )

    citation("full_document")
    with pytest.raises(ValidationError):
        citation("bogus")  # type: ignore[arg-type]

    with pytest.raises(ValidationError):
        ChatAnswer(answer="", citations=(), evidence=(), generated=True)


def test_title_backfill_renames_sha_documents_from_headings() -> None:
    snapshot = build_valid_snapshot()
    sha_document = graph.Document(
        id="doc-sha",
        title="b" * 64,
        source_name="",
        content_format="markdown",
        source_url="",
        external_id="",
        is_active=True,
    )
    sha_version = graph.DocumentVersion(
        id="version-sha",
        version=1,
        sha256=SHA,
        local_path="documents/sha.md",
        created_at=NOW,
        is_current=True,
        error="",
    )
    heading_chunk = _chunk("chunk-heading", 0, "# Гайд по ведению телеграма\nТекст")
    snapshot = snapshot.model_copy(
        update={
            "documents": snapshot.documents + (sha_document,),
            "document_versions": snapshot.document_versions + (sha_version,),
            "chunks": snapshot.chunks + (heading_chunk,),
            "has_versions": snapshot.has_versions
            + (
                graph.HasVersion.model_validate(
                    {"from": "doc-sha", "to": "version-sha"}
                ),
            ),
            "has_chunks": snapshot.has_chunks
            + (
                graph.HasChunk.model_validate(
                    {"from": "version-sha", "to": "chunk-heading"}
                ),
            ),
        }
    )

    updated, renamed = fill_missing_document_titles(snapshot)
    again, renamed_again = fill_missing_document_titles(updated)

    assert renamed == 1
    titles = {item.id: item.title for item in updated.documents}
    assert titles["doc-sha"] == "Гайд по ведению телеграма"
    assert titles["document"] == "Title"
    assert renamed_again == 0


def test_title_backfill_normalises_tab_in_heading_to_single_line() -> None:
    snapshot = build_valid_snapshot()
    tab_heading = _chunk("chunk", 0, "# Гайд\tпо телеграму\nТело")
    snapshot = snapshot.model_copy(
        update={
            "documents": (
                snapshot.documents[0].model_copy(update={"title": "d" * 64}),
            ),
            "chunks": (tab_heading,),
        }
    )

    updated, renamed = fill_missing_document_titles(snapshot)

    assert renamed == 1
    assert updated.documents[0].title == "Гайд по телеграму"
    Document.model_validate(updated.documents[0].model_dump())


def test_title_backfill_falls_back_to_first_line_without_heading() -> None:
    snapshot = build_valid_snapshot()
    plain_chunk = _chunk("chunk", 0, "первая строка без решётки\nвторая")
    snapshot = snapshot.model_copy(
        update={
            "documents": (
                snapshot.documents[0].model_copy(update={"title": "c" * 64}),
            ),
            "chunks": (plain_chunk,),
        }
    )

    updated, renamed = fill_missing_document_titles(snapshot)

    assert renamed == 1
    assert updated.documents[0].title == "первая строка без решётки"
