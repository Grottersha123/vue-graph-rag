from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from mvp_v2.adapters.filesystem.media import FilesystemMediaResolver
from mvp_v2.application.chunk_backfill import ChunkBackfillError, build_missing_chunks
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
