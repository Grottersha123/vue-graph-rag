from __future__ import annotations

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
                limit=request.limit,
            )
        )
        return tuple(_hydrate(self._snapshot, hit) for hit in hits)


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
        subject_exists = chunk is not None and len(owners) == 1
    else:
        asset = assets.get(hit.subject.id)
        owners = sorted(
            e.source_id for e in snapshot.has_assets if e.target_id == hit.subject.id
        )
        owner = owners[0] if len(owners) == 1 else None
        text = asset.llm_description if asset else ""
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
