from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from vue_rag.domain import graph
from vue_rag.domain.snapshot import GraphSnapshot

NOW = datetime(2026, 1, 2, 3, 4, 5, 6, tzinfo=UTC)
SHA = "a" * 64


def build_valid_snapshot() -> GraphSnapshot:
    content = "hello"
    return GraphSnapshot(
        source_types=(graph.SourceType(id="source", code="local_file"),),
        documents=(
            graph.Document(
                id="document",
                title="Title",
                source_name="name",
                content_format="markdown",
                source_url="",
                external_id="",
                is_active=True,
            ),
        ),
        document_versions=(
            graph.DocumentVersion(
                id="version",
                version=1,
                sha256=SHA,
                local_path="documents/a.md",
                created_at=NOW,
                is_current=True,
                error="",
            ),
        ),
        chunks=(
            graph.Chunk(
                id="chunk",
                position=0,
                content=content,
                content_hash=hashlib.sha256(content.encode()).hexdigest(),
            ),
        ),
        assets=(
            graph.Asset(
                id="asset",
                asset_type="image",
                mime_type="image/png",
                source_url="",
                local_path="images/a.png",
                external_id="",
                sha256=SHA,
                position=0,
                description="desc",
                ocr_text="",
            ),
        ),
        embeddings=(
            graph.Embedding(
                id="embedding-chunk",
                provider="local",
                model="embed",
                model_version="v1",
                dimension=2,
                vector=(1.0, 2.5),
                created_at=NOW,
                is_current=True,
            ),
            graph.Embedding(
                id="embedding-asset",
                provider="local",
                model="embed",
                model_version="v1",
                dimension=2,
                vector=(3.0, 4.5),
                created_at=NOW,
                is_current=True,
            ),
        ),
        roles=(graph.Role(id="role", name="student"),),
        processes=(
            graph.Process(id="process", name="onboarding", status="active", owner=""),
        ),
        statuses=(graph.Status(id="status", name="ready", description=""),),
        has_source_types=(
            graph.HasSourceType.model_validate({"from": "document", "to": "source"}),
        ),
        has_versions=(
            graph.HasVersion.model_validate({"from": "document", "to": "version"}),
        ),
        has_statuses=(
            graph.HasStatus.model_validate({"from": "version", "to": "status"}),
        ),
        has_chunks=(graph.HasChunk.model_validate({"from": "version", "to": "chunk"}),),
        has_assets=(graph.HasAsset.model_validate({"from": "version", "to": "asset"}),),
        contains_assets=(
            graph.ContainsAsset.model_validate({"from": "chunk", "to": "asset"}),
        ),
        has_embedding_chunks=(
            graph.HasEmbeddingChunk.model_validate(
                {"from": "chunk", "to": "embedding-chunk"}
            ),
        ),
        has_embedding_assets=(
            graph.HasEmbeddingAsset.model_validate(
                {"from": "asset", "to": "embedding-asset"}
            ),
        ),
        describes=(
            graph.Describes.model_validate({"from": "document", "to": "process"}),
        ),
        for_roles=(graph.ForRole.model_validate({"from": "document", "to": "role"}),),
    )
