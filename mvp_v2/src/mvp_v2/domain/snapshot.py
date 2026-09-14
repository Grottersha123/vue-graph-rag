from __future__ import annotations

from pydantic import BaseModel, field_validator

from mvp_v2.domain import graph
from mvp_v2.domain.base import ContractModel


class GraphSnapshot(ContractModel):
    @field_validator("*", mode="after")
    @classmethod
    def sort_table_rows(cls, value: object) -> object:
        if isinstance(value, tuple) and all(
            isinstance(item, BaseModel) for item in value
        ):
            return tuple(
                sorted(value, key=lambda item: item.model_dump_json(by_alias=True))
            )
        return value

    source_types: tuple[graph.SourceType, ...] = ()
    documents: tuple[graph.Document, ...] = ()
    document_versions: tuple[graph.DocumentVersion, ...] = ()
    chunks: tuple[graph.Chunk, ...] = ()
    assets: tuple[graph.Asset, ...] = ()
    embeddings: tuple[graph.Embedding, ...] = ()
    roles: tuple[graph.Role, ...] = ()
    processes: tuple[graph.Process, ...] = ()
    statuses: tuple[graph.Status, ...] = ()
    has_source_types: tuple[graph.HasSourceType, ...] = ()
    has_versions: tuple[graph.HasVersion, ...] = ()
    supersedes: tuple[graph.Supersedes, ...] = ()
    has_statuses: tuple[graph.HasStatus, ...] = ()
    has_chunks: tuple[graph.HasChunk, ...] = ()
    has_assets: tuple[graph.HasAsset, ...] = ()
    contains_assets: tuple[graph.ContainsAsset, ...] = ()
    has_embedding_chunks: tuple[graph.HasEmbeddingChunk, ...] = ()
    has_embedding_assets: tuple[graph.HasEmbeddingAsset, ...] = ()
    links_to: tuple[graph.LinksTo, ...] = ()
    describes: tuple[graph.Describes, ...] = ()
    for_roles: tuple[graph.ForRole, ...] = ()
