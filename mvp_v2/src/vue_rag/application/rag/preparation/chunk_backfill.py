from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import Protocol

from vue_rag.application.rag.preparation.chunking import (
    DEFAULT_CHUNK_POLICY,
    ChunkPolicy,
    make_chunks,
    split_text,
)
from vue_rag.domain import graph
from vue_rag.domain.invariants import validate_graph
from vue_rag.domain.snapshot import GraphSnapshot
from vue_rag.ports.media import MediaVerificationError


class ChunkBackfillError(ValueError):
    pass


class VerifiedSourceReader(Protocol):
    def read_verified(self, relative_path: str, expected_sha256: str) -> bytes: ...


def _sanitize_source_text(text: str) -> str:
    return "".join(
        "\n"
        if unicodedata.category(character) in {"Zl", "Zp"}
        else " "
        if character not in {"\n", "\t"}
        and unicodedata.category(character).startswith("C")
        else character
        for character in text
    )


def build_missing_chunks(
    snapshot: GraphSnapshot,
    resolver: VerifiedSourceReader,
    *,
    policy: ChunkPolicy = DEFAULT_CHUNK_POLICY,
) -> GraphSnapshot:
    """Create deterministic chunks for current text versions without chunks."""
    markdown_documents = {
        item.id for item in snapshot.documents if item.content_format == "markdown"
    }
    markdown_version_ids = {
        relation.target_id
        for relation in snapshot.has_versions
        if relation.source_id in markdown_documents
    }
    versions = tuple(
        version
        for version in snapshot.document_versions
        if version.id in markdown_version_ids and version.is_current
    )
    missing_paths = tuple(version.id for version in versions if not version.local_path)
    if missing_paths:
        raise ChunkBackfillError(
            "current Markdown versions have no source path: " + ", ".join(missing_paths)
        )
    chunks = list(snapshot.chunks)
    chunks_by_id = {item.id: item for item in chunks}
    relations = list(snapshot.has_chunks)
    for version in versions:
        assert version.local_path is not None
        try:
            content = resolver.read_verified(version.local_path, version.sha256)
            text = _sanitize_source_text(content.decode("utf-8"))
        except (MediaVerificationError, UnicodeDecodeError) as error:
            raise ChunkBackfillError(
                f"source file cannot be verified as UTF-8: {version.id}"
            ) from error
        generated = make_chunks(version.id, split_text(text, policy))
        if not generated:
            raise ChunkBackfillError(
                f"source file produced no nonblank chunks: {version.id}"
            )
        expected_ids = {item.id for item in generated}
        related_ids = {
            relation.target_id
            for relation in relations
            if relation.source_id == version.id
        }
        if not related_ids <= expected_ids:
            raise ChunkBackfillError(
                f"existing chunks conflict with verified source: {version.id}"
            )
        for item in generated:
            existing = chunks_by_id.get(item.id)
            if existing is not None and existing != item:
                raise ChunkBackfillError(
                    f"existing chunk payload conflicts with verified source: {item.id}"
                )
            if existing is None:
                chunks.append(item)
                chunks_by_id[item.id] = item
            if item.id not in related_ids:
                relations.append(
                    graph.HasChunk.model_validate({"from": version.id, "to": item.id})
                )
    updated = snapshot.model_copy(
        update={"chunks": tuple(chunks), "has_chunks": tuple(relations)}
    )
    if not validate_graph(updated).is_valid:
        raise ChunkBackfillError("chunk backfill would violate graph invariants")
    return updated


@dataclass(frozen=True, slots=True)
class ChunkMutationImpact:
    chunks_before: int
    chunks_after: int
    chunks_removed: int
    chunks_added: int
    chunk_embeddings_removed: int
    contains_asset_edges_removed: int


def chunk_mutation_impact(
    before: GraphSnapshot, after: GraphSnapshot
) -> ChunkMutationImpact:
    before_chunk_ids = {item.id for item in before.chunks}
    after_chunk_ids = {item.id for item in after.chunks}
    before_embedding_ids = {
        relation.target_id for relation in before.has_embedding_chunks
    }
    after_embedding_ids = {
        relation.target_id for relation in after.has_embedding_chunks
    }
    return ChunkMutationImpact(
        chunks_before=len(before_chunk_ids),
        chunks_after=len(after_chunk_ids),
        chunks_removed=len(before_chunk_ids - after_chunk_ids),
        chunks_added=len(after_chunk_ids - before_chunk_ids),
        chunk_embeddings_removed=len(before_embedding_ids - after_embedding_ids),
        contains_asset_edges_removed=max(
            0, len(before.contains_assets) - len(after.contains_assets)
        ),
    )


def delete_all_chunks_and_embeddings(snapshot: GraphSnapshot) -> GraphSnapshot:
    """Remove the complete chunk subgraph without touching asset embeddings."""
    chunk_embedding_ids = {
        relation.target_id for relation in snapshot.has_embedding_chunks
    }
    updated = snapshot.model_copy(
        update={
            "chunks": (),
            "embeddings": tuple(
                item
                for item in snapshot.embeddings
                if item.id not in chunk_embedding_ids
            ),
            "has_chunks": (),
            "contains_assets": (),
            "has_embedding_chunks": (),
        }
    )
    if not validate_graph(updated).is_valid:
        raise ChunkBackfillError("chunk deletion would violate graph invariants")
    return updated


def regenerate_all_chunks(
    snapshot: GraphSnapshot,
    resolver: VerifiedSourceReader,
    *,
    policy: ChunkPolicy = DEFAULT_CHUNK_POLICY,
) -> GraphSnapshot:
    """Replace all chunks for every supported current Markdown version."""
    current_version_ids = {
        item.id for item in snapshot.document_versions if item.is_current
    }
    non_markdown_document_ids = {
        item.id for item in snapshot.documents if item.content_format != "markdown"
    }
    unsupported_version_ids = {
        relation.target_id
        for relation in snapshot.has_versions
        if relation.source_id in non_markdown_document_ids
        and relation.target_id in current_version_ids
    }
    if any(
        relation.source_id in unsupported_version_ids
        for relation in snapshot.has_chunks
    ):
        raise ChunkBackfillError(
            "current non-Markdown chunks cannot be regenerated from the stored source"
        )
    preserved_contains_assets = snapshot.contains_assets
    cleared = delete_all_chunks_and_embeddings(snapshot)
    regenerated = build_missing_chunks(cleared, resolver, policy=policy)
    regenerated_chunk_ids = {item.id for item in regenerated.chunks}
    restored_contains_assets = tuple(
        relation
        for relation in preserved_contains_assets
        if relation.source_id in regenerated_chunk_ids
    )
    updated = regenerated.model_copy(
        update={"contains_assets": restored_contains_assets}
    )
    if not validate_graph(updated).is_valid:
        raise ChunkBackfillError("chunk regeneration would violate graph invariants")
    return updated
