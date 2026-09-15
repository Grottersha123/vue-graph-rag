from __future__ import annotations

from typing import Final, Literal

from mvp_v2.application.dto.retrieval import (
    EmbeddingProviderOutput,
    Evidence,
    SearchRequest,
    VectorHit,
    VectorQuery,
)
from mvp_v2.application.manifests import DatabaseManifest
from mvp_v2.domain.snapshot import GraphSnapshot
from mvp_v2.ports.ladybug import AnnPort
from mvp_v2.ports.models import EmbeddingModel

_DOCUMENT_CONTEXT_MAX_CHARS: Final = 1_400
_ANN_OVERFETCH_FACTOR: Final = 3
_ANN_OVERFETCH_MAX_CANDIDATES: Final = 30
_CHUNK_NEIGHBOR_RADIUS: Final = 1


class SearchError(ValueError):
    pass


class SearchService:
    def __init__(
        self,
        model: EmbeddingModel,
        ann: AnnPort,
        manifest: DatabaseManifest,
        snapshot: GraphSnapshot,
    ) -> None:
        self._model, self._ann, self._manifest, self._snapshot = (
            model,
            ann,
            manifest,
            snapshot,
        )

    def search(self, request: SearchRequest) -> tuple[Evidence, ...]:
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
            item = _hydrate(self._snapshot, hit)
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
            if len(joined) + 2 + len(item.text) <= _DOCUMENT_CONTEXT_MAX_CHARS:
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
        text = asset.llm_description if asset else ""
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
        if len(full_text) <= _DOCUMENT_CONTEXT_MAX_CHARS:
            text = full_text
            context_scope = "full_document"
        else:
            matched_index = next(
                index
                for index, item in enumerate(version_chunks)
                if item.id == hit.subject.id
            )
            start = max(0, matched_index - _CHUNK_NEIGHBOR_RADIUS)
            stop = matched_index + _CHUNK_NEIGHBOR_RADIUS + 1
            window_chunks = version_chunks[start:stop]
            window_text = "\n\n".join(item.content for item in window_chunks)
            matched_offset = window_text.find(version_chunks[matched_index].content)
            excerpt_start = max(
                0,
                min(
                    matched_offset - _DOCUMENT_CONTEXT_MAX_CHARS // 4,
                    len(window_text) - _DOCUMENT_CONTEXT_MAX_CHARS,
                ),
            )
            text = window_text[
                excerpt_start : excerpt_start + _DOCUMENT_CONTEXT_MAX_CHARS
            ]
            context_scope = "chunk_window"
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
