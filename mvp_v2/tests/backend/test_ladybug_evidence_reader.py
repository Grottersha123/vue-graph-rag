from __future__ import annotations

import pytest

from mvp_v2.adapters.ladybug import LadybugCapabilityError, LadybugEvidenceHydrator
from mvp_v2.application.dto.retrieval import (
    EmbeddingPurpose,
    EmbeddingSpaceIdentity,
    Evidence,
    SearchRequest,
    SubjectKind,
    SubjectRef,
    VectorHit,
    VectorQuery,
)
from mvp_v2.application.dump_lifecycle.manifests import DatabaseManifest
from mvp_v2.application.ingestion_contracts import ModelAuthorization
from mvp_v2.application.rag.retrieval.retrieval import SearchService
from tests.backend.helpers import manifest

SPACE = EmbeddingSpaceIdentity(
    provider="ollama", model="embed", model_version="v1", dimension=2
)


class _Result:
    def __init__(self, rows: list[list[object]]) -> None:
        self._rows = rows

    def has_next(self) -> bool:
        return bool(self._rows)

    def get_next(self) -> list[object]:
        return self._rows.pop(0)

    def close(self) -> None:
        pass


class _Connection:
    def __init__(self, *, current: bool = True, active: bool = True) -> None:
        self.current = current
        self.active = active
        self.calls: list[tuple[str, dict[str, object] | None]] = []

    def execute(
        self, query: str, parameters: dict[str, object] | None = None
    ) -> _Result:
        self.calls.append((query, parameters))
        if "RETURN c.content, c.position" in query:
            return _Result(
                [
                    [
                        "matched",
                        1,
                        "version",
                        3,
                        "documents/a.md",
                        self.current,
                        "document",
                        "Title",
                        "https://example.test/a",
                        self.active,
                    ]
                ]
            )
        if "RETURN a.description, a.position" in query:
            return _Result(
                [
                    [
                        "diagram",
                        0,
                        "version",
                        3,
                        "documents/a.md",
                        self.current,
                        "document",
                        "Title",
                        "https://example.test/a",
                        self.active,
                    ]
                ]
            )
        if "RETURN c.id, c.position, c.content" in query:
            return _Result(
                [
                    ["before", 0, "before"],
                    ["chunk", 1, "matched"],
                    ["after", 2, "after"],
                ]
            )
        labels: dict[str, list[list[object]]] = {
            "RETURN s.code": [["local_file"]],
            "RETURN s.name": [["ready"]],
            "RETURN r.name": [["student"]],
            "RETURN p.name": [["onboarding"]],
            "RETURN other.id": [["linked"]],
        }
        for marker, rows in labels.items():
            if marker in query:
                return _Result(rows)
        return _Result([])


def _hit(kind: SubjectKind, identity: str) -> VectorHit:
    return VectorHit(
        subject=SubjectRef(kind=kind, id=identity),
        embedding_id=f"embedding-{identity}",
        purpose=(
            EmbeddingPurpose.CHUNK_RETRIEVAL
            if kind is SubjectKind.CHUNK
            else EmbeddingPurpose.ASSET_DESCRIPTION_RETRIEVAL
        ),
        score=0.8,
    )


def test_ladybug_hydrator_reads_chunk_context_and_metadata_directly() -> None:
    connection = _Connection()

    evidence = LadybugEvidenceHydrator(connection).hydrate(
        _hit(SubjectKind.CHUNK, "chunk")
    )

    assert evidence.text == "before\n\nmatched\n\nafter"
    assert evidence.context_scope == "full_document"
    assert evidence.matched_chunk_position == 1
    assert evidence.document_chunk_count == 3
    assert evidence.source_type == "local_file"
    assert evidence.statuses == ("ready",)
    assert evidence.roles == ("student",)
    assert evidence.processes == ("onboarding",)
    assert evidence.linked_document_ids == ("linked",)
    assert all(parameters is not None for _, parameters in connection.calls)


def test_ladybug_hydrator_reads_asset_without_loading_snapshot() -> None:
    evidence = LadybugEvidenceHydrator(_Connection()).hydrate(
        _hit(SubjectKind.ASSET, "asset")
    )

    assert evidence.text == "diagram"
    assert evidence.context_scope == "asset"
    assert evidence.matched_chunk_position is None
    assert evidence.document_chunk_count == 3


@pytest.mark.parametrize(("current", "active"), [(False, True), (True, False)])
def test_ladybug_hydrator_rejects_stale_evidence(current: bool, active: bool) -> None:
    with pytest.raises(LadybugCapabilityError, match="current active evidence"):
        LadybugEvidenceHydrator(_Connection(current=current, active=active)).hydrate(
            _hit(SubjectKind.CHUNK, "chunk")
        )


class _Model:
    def embed(
        self, *, texts: tuple[str, ...], model: str
    ) -> tuple[tuple[float, ...], ...]:
        return ((1.0, 2.0),)


class _Ann:
    def query(self, request: VectorQuery) -> tuple[VectorHit, ...]:
        return (_hit(SubjectKind.CHUNK, "chunk"),)


class _Hydrator:
    def __init__(self) -> None:
        self.hits: list[VectorHit] = []

    def hydrate(self, hit: VectorHit) -> Evidence:
        self.hits.append(hit)
        return LadybugEvidenceHydrator(_Connection()).hydrate(hit)


def test_search_service_accepts_direct_hydrator_without_graph_snapshot() -> None:
    database_manifest: DatabaseManifest = manifest()
    hydrator = _Hydrator()
    service = SearchService(
        model=_Model(),
        ann=_Ann(),
        manifest=database_manifest,
        evidence_hydrator=hydrator,
    )
    request = SearchRequest(
        question="question",
        space=SPACE,
        purposes=(EmbeddingPurpose.CHUNK_RETRIEVAL,),
        limit=2,
        authorization=ModelAuthorization(allow=True, operation="embedding"),
        minimum_score=0.55,
    )

    evidence = service.search(request)

    assert len(evidence) == 1
    assert evidence[0].document_id == "document"
    assert [hit.subject.id for hit in hydrator.hits] == ["chunk"]
