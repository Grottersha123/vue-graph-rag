from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from mvp_v2.adapters.filesystem.media import FilesystemMediaResolver
from mvp_v2.application.rag.preparation.chunk_backfill import (
    ChunkBackfillError,
    build_missing_chunks,
    chunk_mutation_impact,
    delete_all_chunks_and_embeddings,
    regenerate_all_chunks,
)
from mvp_v2.domain.snapshot import GraphSnapshot
from tests.factories import build_valid_snapshot


def _snapshot_without_chunks(content: bytes) -> GraphSnapshot:
    snapshot = build_valid_snapshot()
    version = snapshot.document_versions[0].model_copy(
        update={"sha256": hashlib.sha256(content).hexdigest()}
    )
    return snapshot.model_copy(
        update={
            "document_versions": (version,),
            "chunks": (),
            "has_chunks": (),
            "contains_assets": (),
            "has_embedding_chunks": (),
            "has_embedding_assets": (),
            "embeddings": (),
        }
    )


def test_build_missing_chunks_is_deterministic_and_idempotent(tmp_path: Path) -> None:
    content = (
        "Russian and English text. Русский\x00 и английский текст. " * 30
    ).encode()
    source = tmp_path / "documents" / "a.md"
    source.parent.mkdir()
    source.write_bytes(content)
    snapshot = _snapshot_without_chunks(content)
    resolver = FilesystemMediaResolver(tmp_path)

    first = build_missing_chunks(snapshot, resolver)
    second = build_missing_chunks(first, resolver)

    assert first.chunks
    assert len(first.has_chunks) == len(first.chunks)
    assert second == first


def test_build_missing_chunks_rejects_changed_source(tmp_path: Path) -> None:
    expected = b"expected markdown"
    source = tmp_path / "documents" / "a.md"
    source.parent.mkdir()
    source.write_bytes(b"changed markdown")

    with pytest.raises(ChunkBackfillError, match="cannot be verified"):
        build_missing_chunks(
            _snapshot_without_chunks(expected), FilesystemMediaResolver(tmp_path)
        )


def test_build_missing_chunks_repairs_partial_chunk_set(tmp_path: Path) -> None:
    content = ("A sufficiently long Markdown paragraph. " * 100).encode()
    source = tmp_path / "documents" / "a.md"
    source.parent.mkdir()
    source.write_bytes(content)
    snapshot = _snapshot_without_chunks(content)
    resolver = FilesystemMediaResolver(tmp_path)
    complete = build_missing_chunks(snapshot, resolver)
    partial = complete.model_copy(
        update={
            "chunks": complete.chunks[:1],
            "has_chunks": complete.has_chunks[:1],
        }
    )

    repaired = build_missing_chunks(partial, resolver)

    assert repaired == complete


def test_build_missing_chunks_rejects_current_markdown_without_path() -> None:
    content = b"Markdown"
    snapshot = _snapshot_without_chunks(content)
    version = snapshot.document_versions[0].model_copy(update={"local_path": None})

    with pytest.raises(ChunkBackfillError, match="no source path"):
        build_missing_chunks(
            snapshot.model_copy(update={"document_versions": (version,)}),
            FilesystemMediaResolver(Path("/unused")),
        )


def test_build_missing_chunks_rejects_same_id_with_conflicting_payload(
    tmp_path: Path,
) -> None:
    content = ("Verified Markdown content. " * 80).encode()
    source = tmp_path / "documents" / "a.md"
    source.parent.mkdir()
    source.write_bytes(content)
    snapshot = _snapshot_without_chunks(content)
    resolver = FilesystemMediaResolver(tmp_path)
    complete = build_missing_chunks(snapshot, resolver)
    original = complete.chunks[0]
    changed_content = "Different but internally valid content"
    corrupted = type(original)(
        id=original.id,
        position=original.position,
        content=changed_content,
        content_hash=hashlib.sha256(changed_content.encode()).hexdigest(),
    )
    inconsistent = complete.model_copy(
        update={"chunks": (corrupted, *complete.chunks[1:])}
    )

    with pytest.raises(ChunkBackfillError, match="payload conflicts"):
        build_missing_chunks(inconsistent, resolver)


def test_delete_and_regenerate_preserve_non_chunk_graph(tmp_path: Path) -> None:
    snapshot = build_valid_snapshot()

    deleted = delete_all_chunks_and_embeddings(snapshot)
    deletion_impact = chunk_mutation_impact(snapshot, deleted)

    assert deleted.chunks == ()
    assert deleted.has_chunks == ()
    assert deleted.contains_assets == ()
    assert deleted.has_embedding_chunks == ()
    assert {item.id for item in deleted.embeddings} == {"embedding-asset"}
    assert deleted.assets == snapshot.assets
    assert deleted.has_embedding_assets == snapshot.has_embedding_assets
    assert deletion_impact.chunks_removed == 1
    assert deletion_impact.chunk_embeddings_removed == 1
    assert deletion_impact.contains_asset_edges_removed == 1

    content = ("A better deterministic Markdown paragraph. " * 80).encode()
    source = tmp_path / "documents" / "a.md"
    source.parent.mkdir()
    source.write_bytes(content)
    version = snapshot.document_versions[0].model_copy(
        update={"sha256": hashlib.sha256(content).hexdigest()}
    )
    source_snapshot = snapshot.model_copy(update={"document_versions": (version,)})

    regenerated = regenerate_all_chunks(
        source_snapshot, FilesystemMediaResolver(tmp_path)
    )
    regeneration_impact = chunk_mutation_impact(source_snapshot, regenerated)

    assert regenerated.chunks
    assert all(item.id != "chunk" for item in regenerated.chunks)
    assert len(regenerated.has_chunks) == len(regenerated.chunks)
    assert regenerated.has_embedding_chunks == ()
    assert {item.id for item in regenerated.embeddings} == {"embedding-asset"}
    assert regenerated.assets == source_snapshot.assets
    assert regeneration_impact.chunks_removed == 1
    assert regeneration_impact.chunks_added == len(regenerated.chunks)
    assert regeneration_impact.chunk_embeddings_removed == 1


def test_regeneration_preserves_stable_links_and_rejects_unsupported_sources(
    tmp_path: Path,
) -> None:
    content = b"hello"
    source = tmp_path / "documents" / "a.md"
    source.parent.mkdir()
    source.write_bytes(content)
    snapshot = _snapshot_without_chunks(content)
    generated = build_missing_chunks(snapshot, FilesystemMediaResolver(tmp_path))
    contains = (
        build_valid_snapshot()
        .contains_assets[0]
        .model_copy(update={"source_id": generated.chunks[0].id})
    )
    with_link = generated.model_copy(update={"contains_assets": (contains,)})

    updated = regenerate_all_chunks(with_link, FilesystemMediaResolver(tmp_path))

    assert updated.contains_assets == (contains,)

    unsupported = build_valid_snapshot()
    document = unsupported.documents[0].model_copy(update={"content_format": "pdf"})
    with pytest.raises(ChunkBackfillError, match="non-Markdown"):
        regenerate_all_chunks(
            unsupported.model_copy(update={"documents": (document,)}),
            FilesystemMediaResolver(Path("/unused")),
        )
