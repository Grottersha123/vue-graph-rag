from __future__ import annotations

import math
from typing import Literal, cast

from mvp_v2.application.ingestion_contracts import EmbeddingRequest, ModelAuthorization
from mvp_v2.domain import graph
from mvp_v2.domain.base import stable_id
from mvp_v2.domain.invariants import validate_graph
from mvp_v2.domain.snapshot import GraphSnapshot
from mvp_v2.ports.models import EmbeddingModel, VisionModel


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
            {**matching[0].model_dump(), "llm_description": description}
        )
        assets = tuple(
            updated_asset if item.id == asset_id else item for item in snapshot.assets
        )
        return _validated_snapshot(snapshot.model_copy(update={"assets": assets}))
    except ValueError as error:
        raise ModelOperationError(
            "vision model returned an invalid description"
        ) from error


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
        else tuple(item for item in subjects if (item[0], item[1]) not in existing)
    )
    new_ids = {
        stable_id(
            "embedding",
            subject_id,
            purpose,
            identity.provider,
            identity.model,
            identity.model_version,
            str(identity.dimension),
            request.generation_token,
        )
        for subject_id, purpose, _ in selected
    }
    occupied_ids = {item.id for item in snapshot.embeddings}
    if new_ids & occupied_ids:
        raise ModelOperationError("generation token would duplicate embedding history")
    vectors: list[tuple[float, ...]] = []
    for offset in range(0, len(selected), request.batch_size):
        batch = selected[offset : offset + request.batch_size]
        returned = model.embed(
            texts=tuple(item[2] for item in batch), model=identity.model
        )
        if len(returned) != len(batch):
            raise ModelOperationError("embedding response cardinality mismatch")
        for vector in returned:
            if len(vector) != identity.dimension or any(
                isinstance(value, bool) or not math.isfinite(value) for value in vector
            ):
                raise ModelOperationError("embedding vector is invalid")
        vectors.extend(returned)
    embeddings = list(snapshot.embeddings)
    if request.mode == "all":
        relevant_ids = {
            embedding_id
            for key, embedding_id in existing.items()
            if key in {(item[0], item[1]) for item in selected}
        }
        embeddings = [
            item.model_copy(update={"is_current": False})
            if item.id in relevant_ids
            else item
            for item in embeddings
        ]
    chunk_edges = list(snapshot.has_embedding_chunks)
    asset_edges = list(snapshot.has_embedding_assets)
    for (subject_id, purpose, _), vector in zip(selected, vectors, strict=True):
        embedding_id = stable_id(
            "embedding",
            subject_id,
            purpose,
            identity.provider,
            identity.model,
            identity.model_version,
            str(identity.dimension),
            request.generation_token,
        )
        typed_purpose = cast(
            Literal["chunk_retrieval", "asset_description_retrieval"], purpose
        )
        embedding = graph.Embedding(
            id=embedding_id,
            provider=identity.provider,
            model=identity.model,
            model_version=identity.model_version,
            purpose=typed_purpose,
            dimension=identity.dimension,
            vector=vector,
            created_at=request.created_at,
            is_current=True,
        )
        embeddings.append(embedding)
        edge = {"from": subject_id, "to": embedding_id}
        if purpose == "chunk_retrieval":
            chunk_edges.append(graph.HasEmbeddingChunk.model_validate(edge))
        else:
            asset_edges.append(graph.HasEmbeddingAsset.model_validate(edge))
    return _validated_snapshot(
        snapshot.model_copy(
            update={
                "embeddings": tuple(embeddings),
                "has_embedding_chunks": tuple(chunk_edges),
                "has_embedding_assets": tuple(asset_edges),
            }
        )
    )


def _subjects(snapshot: GraphSnapshot) -> tuple[tuple[str, str, str], ...]:
    values = [(item.id, "chunk_retrieval", item.content) for item in snapshot.chunks]
    values.extend(
        (
            item.id,
            "asset_description_retrieval",
            f"Image description:\n{item.llm_description.strip()}",
        )
        for item in snapshot.assets
        if item.llm_description.strip()
    )
    return tuple(sorted(values, key=lambda item: (item[0], item[1])))


def _current_keys(
    snapshot: GraphSnapshot, provider: str, model: str, version: str, dimension: int
) -> dict[tuple[str, str], str]:
    embeddings = {item.id: item for item in snapshot.embeddings}
    result: dict[tuple[str, str], str] = {}
    for relation in (*snapshot.has_embedding_chunks, *snapshot.has_embedding_assets):
        item = embeddings.get(relation.target_id)
        if (
            item is not None
            and item.is_current
            and (item.provider, item.model, item.model_version, item.dimension)
            == (provider, model, version, dimension)
        ):
            result[(relation.source_id, item.purpose)] = item.id
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
