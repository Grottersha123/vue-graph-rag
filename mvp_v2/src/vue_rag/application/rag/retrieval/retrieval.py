from __future__ import annotations

from typing import Final, Literal, Protocol

from vue_rag.application.dto.retrieval import (
    EmbeddingProviderOutput,
    Evidence,
    SearchRequest,
    VectorHit,
    VectorQuery,
)
from vue_rag.application.dump_lifecycle.manifests import DatabaseManifest
from vue_rag.application.rag.retrieval.safety import contains_phone_number
from vue_rag.domain.snapshot import GraphSnapshot
from vue_rag.ports.ladybug import AnnPort
from vue_rag.ports.models import EmbeddingModel

DOCUMENT_CONTEXT_MAX_CHARS: Final = 4_000
_ANN_OVERFETCH_FACTOR: Final = 3
_ANN_OVERFETCH_MAX_CANDIDATES: Final = 30
CHUNK_NEIGHBOR_RADIUS: Final = 2


class SearchError(ValueError):
    pass


class EvidenceHydrator(Protocol):
    def hydrate(self, hit: VectorHit) -> Evidence: ...


class SearchExecutionLock(Protocol):
    def acquire(self) -> bool: ...

    def release(self) -> None: ...


class SnapshotEvidenceHydrator:
    def __init__(self, snapshot: GraphSnapshot) -> None:
        self._snapshot = snapshot

    def hydrate(self, hit: VectorHit) -> Evidence:
        return _hydrate(self._snapshot, hit)


class SearchService:
    def __init__(
        self,
        model: EmbeddingModel,
        ann: AnnPort,
        manifest: DatabaseManifest,
        snapshot: GraphSnapshot | None = None,
        *,
        evidence_hydrator: EvidenceHydrator | None = None,
        execution_lock: SearchExecutionLock | None = None,
    ) -> None:
        if (snapshot is None) == (evidence_hydrator is None):
            raise ValueError("provide exactly one evidence source")
        self._model = model
        self._ann = ann
        self._manifest = manifest
        self._hydrator: EvidenceHydrator
        if snapshot is not None:
            self._hydrator = SnapshotEvidenceHydrator(snapshot)
        else:
            assert evidence_hydrator is not None
            self._hydrator = evidence_hydrator
        self._execution_lock = execution_lock

    def search(self, request: SearchRequest) -> tuple[Evidence, ...]:
        if self._execution_lock is None:
            return self._search(request)
        self._execution_lock.acquire()
        try:
            return self._search(request)
        finally:
            self._execution_lock.release()

    def _search(self, request: SearchRequest) -> tuple[Evidence, ...]:
        if request.space != self._manifest.embedding_space:
            raise SearchError("configured identity does not match database manifest")
        if (
            not request.authorization.allow
            or request.authorization.operation != "embedding"
        ):
            raise SearchError(
                "TODO-SEC-001: explicit embedding authorization is required"
            )
        vectors = self._model.embed(
            texts=(request.question,), model=request.space.model
        )
        if len(vectors) != 1:
            raise SearchError("embedding response is invalid")
        try:
            output = EmbeddingProviderOutput(space=request.space, vector=vectors[0])
        except (TypeError, ValueError) as error:
            raise SearchError("embedding response is invalid") from error
        hits = self._ann.query(
            VectorQuery(
                space=output.space,
                vector=output.vector,
                purposes=request.purposes,
                limit=max(
                    request.limit,
                    min(
                        request.limit * _ANN_OVERFETCH_FACTOR,
                        _ANN_OVERFETCH_MAX_CANDIDATES,
                    ),
                ),
            )
        )
        evidence: list[Evidence] = []
        merged_texts: dict[str, list[str]] = {}
        for hit in hits:
            if hit.score < request.minimum_score:
                continue
            item = self._hydrator.hydrate(hit)
            if contains_phone_number(item.text):
                continue
            existing = next(
                (e for e in evidence if e.version_id == item.version_id), None
            )
            if existing is None:
                if len(evidence) == request.limit:
                    continue
                evidence.append(item)
                merged_texts[item.version_id] = [item.text]
                continue
            parts = merged_texts[existing.version_id]
            joined = "\n\n".join(parts)
            if item.text in joined:
                continue
            if len(joined) + 2 + len(item.text) <= DOCUMENT_CONTEXT_MAX_CHARS:
                parts.append(item.text)
        return tuple(
            item.model_copy(update={"text": "\n\n".join(merged_texts[item.version_id])})
            for item in evidence
        )


def _hydrate(snapshot: GraphSnapshot, hit: VectorHit) -> Evidence:
    chunks = {x.id: x for x in snapshot.chunks}
    assets = {x.id: x for x in snapshot.assets}
    if hit.subject.kind.value == "chunk":
        chunk = chunks.get(hit.subject.id)
        owners = sorted(
            e.source_id for e in snapshot.has_chunks if e.target_id == hit.subject.id
        )
        owner = owners[0] if len(owners) == 1 else None
        text = chunk.content if chunk else ""
        matched_chunk_position = chunk.position if chunk else None
        subject_exists = chunk is not None and len(owners) == 1
    else:
        asset = assets.get(hit.subject.id)
        owners = sorted(
            e.source_id for e in snapshot.has_assets if e.target_id == hit.subject.id
        )
        owner = owners[0] if len(owners) == 1 else None
        text = asset.description if asset else ""
        matched_chunk_position = None
        subject_exists = asset is not None and len(owners) == 1
    versions = {x.id: x for x in snapshot.document_versions}
    version = versions.get(owner or "")
    document_id = next(
        (e.source_id for e in snapshot.has_versions if e.target_id == owner), None
    )
    documents = {x.id: x for x in snapshot.documents}
    document = documents.get(document_id or "")
    if (
        not subject_exists
        or version is None
        or document is None
        or not version.is_current
        or not document.is_active
        or not _is_production_policy_document(document)
        or not text.strip()
    ):
        raise SearchError("hit does not hydrate to current active evidence")
    version_chunks = tuple(
        sorted(
            (
                chunks[edge.target_id]
                for edge in snapshot.has_chunks
                if edge.source_id == version.id and edge.target_id in chunks
            ),
            key=lambda item: (item.position, item.id),
        )
    )
    context_scope: Literal["asset", "full_document", "chunk_window"] = "asset"
    if matched_chunk_position is not None:
        full_text = "\n\n".join(item.content for item in version_chunks)
        if len(full_text) <= DOCUMENT_CONTEXT_MAX_CHARS:
            text = full_text
            context_scope = "full_document"
        else:
            matched_index = next(
                index
                for index, item in enumerate(version_chunks)
                if item.id == hit.subject.id
            )
            start = max(0, matched_index - CHUNK_NEIGHBOR_RADIUS)
            stop = matched_index + CHUNK_NEIGHBOR_RADIUS + 1
            window_chunks = version_chunks[start:stop]
            window_text = "\n\n".join(item.content for item in window_chunks)
            matched_offset = window_text.find(version_chunks[matched_index].content)
            excerpt_start = max(
                0,
                min(
                    matched_offset - DOCUMENT_CONTEXT_MAX_CHARS // 4,
                    len(window_text) - DOCUMENT_CONTEXT_MAX_CHARS,
                ),
            )
            text = window_text[
                excerpt_start : excerpt_start + DOCUMENT_CONTEXT_MAX_CHARS
            ]
            context_scope = "chunk_window"
    properties = {item.name: item.value for item in document.custom_properties}
    knowledge_kind = str(properties.get("knowledge_kind", ""))
    external_id = document.external_id
    source_kind: Literal["faq", "documentation", "other"] = (
        "faq"
        if knowledge_kind == "curated_faq" or external_id.startswith("faq:")
        else "documentation"
        if knowledge_kind == "official_documentation"
        else "other"
    )
    source_ids = {x.id: x.code for x in snapshot.source_types}
    source = next(
        (
            source_ids.get(e.target_id)
            for e in snapshot.has_source_types
            if e.source_id == document.id
        ),
        None,
    )
    status_ids = {x.id: x.name for x in snapshot.statuses}
    role_ids = {x.id: x.name for x in snapshot.roles}
    process_ids = {x.id: x.name for x in snapshot.processes}
    linked = {e.target_id for e in snapshot.links_to if e.source_id == document.id} | {
        e.source_id for e in snapshot.links_to if e.target_id == document.id
    }
    return Evidence(
        subject=hit.subject,
        embedding_id=hit.embedding_id,
        score=hit.score,
        text=text,
        matched_chunk_position=matched_chunk_position,
        document_chunk_count=len(version_chunks),
        context_scope=context_scope,
        document_id=document.id,
        document_title=document.title,
        external_id=external_id,
        knowledge_kind=knowledge_kind,
        source_kind=source_kind,
        source_url=document.source_url,
        version_id=version.id,
        version=version.version,
        local_path=version.local_path,
        source_type=source or "unknown",
        statuses=tuple(
            sorted(
                status_ids[e.target_id]
                for e in snapshot.has_statuses
                if e.source_id == version.id
            )
        ),
        roles=tuple(
            sorted(
                role_ids[e.target_id]
                for e in snapshot.for_roles
                if e.source_id == document.id
            )
        ),
        processes=tuple(
            sorted(
                process_ids[e.target_id]
                for e in snapshot.describes
                if e.source_id == document.id
            )
        ),
        linked_document_ids=tuple(sorted(linked)),
    )


def _is_production_policy_document(document: object) -> bool:
    properties = {
        item.name: item.value for item in getattr(document, "custom_properties", ())
    }
    kind = properties.get("knowledge_kind")
    status = properties.get("review_status")
    reusable = properties.get("reusable_as_policy")
    # Legacy documents remain searchable; curated records require the full gate.
    if kind is None and status is None and reusable is None:
        return True
    return (
        kind in {"official_documentation", "curated_faq"}
        and status == "approved"
        and reusable is True
    )
