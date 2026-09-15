from __future__ import annotations

import unicodedata
from typing import Protocol

from mvp_v2.application.chunking import (
    DEFAULT_CHUNK_POLICY,
    ChunkPolicy,
    make_chunks,
    split_text,
)
from mvp_v2.domain import graph
from mvp_v2.domain.invariants import validate_graph
from mvp_v2.domain.snapshot import GraphSnapshot
from mvp_v2.ports.media import MediaVerificationError


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
                    graph.HasChunk.model_validate(
                        {"from": version.id, "to": item.id}
                    )
                )
    updated = snapshot.model_copy(
        update={"chunks": tuple(chunks), "has_chunks": tuple(relations)}
    )
    if not validate_graph(updated).is_valid:
        raise ChunkBackfillError("chunk backfill would violate graph invariants")
    return updated
