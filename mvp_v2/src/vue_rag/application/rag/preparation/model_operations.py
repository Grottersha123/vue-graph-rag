from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal, TypeAlias

from vue_rag.application.ingestion_contracts import EmbeddingRequest, ModelAuthorization
from vue_rag.domain import graph
from vue_rag.domain.base import stable_id
from vue_rag.domain.invariants import validate_graph
from vue_rag.domain.snapshot import GraphSnapshot
from vue_rag.ports.models import EmbeddingModel, VisionModel


class ModelOperationError(RuntimeError):
    pass


def _authorize(authorization: ModelAuthorization, operation: str) -> None:
    if not authorization.allow or authorization.operation != operation:
        raise ModelOperationError("TODO-SEC-001: explicit authorization is required")


def describe_asset(
    snapshot: GraphSnapshot,
    asset_id: str,
    image: bytes,
    authorization: ModelAuthorization,
    *,
    model: str,
    vision: VisionModel,
) -> GraphSnapshot:
    _authorize(authorization, "vision_description")
    matching = [item for item in snapshot.assets if item.id == asset_id]
    if len(matching) != 1:
        raise ModelOperationError("asset_id does not resolve exactly")
    description = vision.describe(
        image=image, mime_type=matching[0].mime_type, model=model
    ).strip()
    if not description:
        raise ModelOperationError("vision model returned an empty description")
    try:
        updated_asset = graph.Asset.model_validate(
            {**matching[0].model_dump(), "description": description}
        )
        assets = tuple(
            updated_asset if item.id == asset_id else item for item in snapshot.assets
        )
        return _validated_snapshot(snapshot.model_copy(update={"assets": assets}))
    except ValueError as error:
        raise ModelOperationError(
            "vision model returned an invalid description"
        ) from error


SubjectKind: TypeAlias = Literal["chunk", "asset", "document"]


@dataclass(frozen=True, slots=True, kw_only=True)
class _EmbeddingSubject:
    id: str
    kind: SubjectKind
    text: str
    summary: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class _BuiltVectors:
    vector: tuple[float, ...]
    summary_vector: tuple[float, ...] = ()


def build_embeddings(
    snapshot: GraphSnapshot, request: EmbeddingRequest, model: EmbeddingModel
) -> GraphSnapshot:
    _authorize(request.authorization, "embedding")
    identity = request.identity
    if identity.dimension < 1 or request.batch_size < 1 or request.batch_size > 256:
        raise ModelOperationError("dimension and batch size must be within bounds")
    subjects = _subjects(snapshot)
    existing = _current_keys(
        snapshot,
        identity.provider,
        identity.model,
        identity.model_version,
        identity.dimension,
    )
    selected = (
        subjects
        if request.mode == "all"
        else tuple(item for item in subjects if (item.id, item.kind) not in existing)
    )
    new_ids = {_embedding_id(item, request) for item in selected}
    occupied_ids = {item.id for item in snapshot.embeddings}
    if new_ids & occupied_ids:
        raise ModelOperationError("generation token would duplicate embedding history")

    vectors = _build_vectors(selected, request, model)
    embeddings = list(snapshot.embeddings)
    if request.mode == "all":
        relevant_ids = set(existing.values())
        embeddings = [
            item.model_copy(update={"is_current": False})
            if item.id in relevant_ids
            else item
            for item in embeddings
        ]
    relation_updates: dict[SubjectKind, list[object]] = {
        "chunk": list(snapshot.has_embedding_chunks),
        "asset": list(snapshot.has_embedding_assets),
        "document": list(snapshot.has_summary_embeddings),
    }
    relation_models = {
        "chunk": graph.HasEmbeddingChunk,
        "asset": graph.HasEmbeddingAsset,
        "document": graph.HasSummaryEmbedding,
    }
    for subject, built in zip(selected, vectors, strict=True):
        embedding_id = _embedding_id(subject, request)
        embeddings.append(
            graph.Embedding(
                id=embedding_id,
                provider=identity.provider,
                model=identity.model,
                model_version=identity.model_version,
                dimension=identity.dimension,
                vector=built.vector,
                created_at=request.created_at,
                is_current=True,
                summary_vector=built.summary_vector,
            )
        )
        relation_updates[subject.kind].append(
            relation_models[subject.kind].model_validate(
                {"from": subject.id, "to": embedding_id}
            )
        )
    return _validated_snapshot(
        snapshot.model_copy(
            update={
                "embeddings": tuple(embeddings),
                "has_embedding_chunks": tuple(relation_updates["chunk"]),
                "has_embedding_assets": tuple(relation_updates["asset"]),
                "has_summary_embeddings": tuple(relation_updates["document"]),
            }
        )
    )


def _embedding_id(subject: _EmbeddingSubject, request: EmbeddingRequest) -> str:
    identity = request.identity
    return stable_id(
        "embedding",
        subject.id,
        subject.kind,
        identity.provider,
        identity.model,
        identity.model_version,
        str(identity.dimension),
        request.generation_token,
    )


def _build_vectors(
    subjects: tuple[_EmbeddingSubject, ...],
    request: EmbeddingRequest,
    model: EmbeddingModel,
) -> tuple[_BuiltVectors, ...]:
    primary = _embed_texts(tuple(item.text for item in subjects), request, model)
    summaries = tuple(
        (index, item.summary)
        for index, item in enumerate(subjects)
        if item.kind == "chunk" and item.summary.strip()
    )
    summary_vectors = _embed_texts(tuple(text for _, text in summaries), request, model)
    summary_by_index = {
        index: vector
        for (index, _), vector in zip(summaries, summary_vectors, strict=True)
    }
    return tuple(
        _BuiltVectors(
            vector=vector,
            summary_vector=summary_by_index.get(index, ()),
        )
        for index, vector in enumerate(primary)
    )


def _embed_texts(
    texts: tuple[str, ...],
    request: EmbeddingRequest,
    model: EmbeddingModel,
) -> tuple[tuple[float, ...], ...]:
    vectors: list[tuple[float, ...]] = []
    for offset in range(0, len(texts), request.batch_size):
        batch = texts[offset : offset + request.batch_size]
        returned = model.embed(texts=batch, model=request.identity.model)
        if len(returned) != len(batch):
            raise ModelOperationError("embedding response cardinality mismatch")
        for vector in returned:
            if len(vector) != request.identity.dimension or any(
                isinstance(value, bool) or not math.isfinite(value) for value in vector
            ):
                raise ModelOperationError("embedding vector is invalid")
        vectors.extend(returned)
    return tuple(vectors)


def _subjects(snapshot: GraphSnapshot) -> tuple[_EmbeddingSubject, ...]:
    values = [
        _EmbeddingSubject(
            id=item.id,
            kind="chunk",
            text=item.content,
            summary=item.summary,
        )
        for item in snapshot.chunks
    ]
    values.extend(
        _EmbeddingSubject(
            id=item.id,
            kind="asset",
            text=f"Image description:\n{item.description.strip()}",
        )
        for item in snapshot.assets
        if item.description.strip()
    )
    values.extend(
        _EmbeddingSubject(
            id=item.id,
            kind="document",
            text=f"Document summary:\n{item.summary.strip()}",
        )
        for item in snapshot.documents
        if item.summary.strip()
    )
    return tuple(sorted(values, key=lambda item: (item.id, item.kind)))


def _current_keys(
    snapshot: GraphSnapshot, provider: str, model: str, version: str, dimension: int
) -> dict[tuple[str, SubjectKind], str]:
    embeddings = {item.id: item for item in snapshot.embeddings}
    relations: tuple[tuple[SubjectKind, tuple[graph.Relation, ...]], ...] = (
        ("chunk", snapshot.has_embedding_chunks),
        ("asset", snapshot.has_embedding_assets),
        ("document", snapshot.has_summary_embeddings),
    )
    result: dict[tuple[str, SubjectKind], str] = {}
    for kind, typed_relations in relations:
        for relation in typed_relations:
            source_id = relation.source_id
            target_id = relation.target_id
            item = embeddings.get(target_id)
            if (
                item is not None
                and item.is_current
                and (item.provider, item.model, item.model_version, item.dimension)
                == (provider, model, version, dimension)
            ):
                result[(source_id, kind)] = item.id
    return result


def _validated_snapshot(snapshot: GraphSnapshot) -> GraphSnapshot:
    try:
        validated = GraphSnapshot.model_validate(snapshot.model_dump())
    except ValueError as error:
        raise ModelOperationError(
            "model operation produced an invalid graph"
        ) from error
    if not validate_graph(validated).is_valid:
        raise ModelOperationError("model operation would violate graph invariants")
    return validated
