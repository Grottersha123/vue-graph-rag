from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

import pytest

from vue_rag.adapters.filesystem.dumps import FilesystemDumpRepository
from vue_rag.adapters.filesystem.media import FilesystemMediaResolver
from vue_rag.domain.snapshot import GraphSnapshot
from vue_rag.ports.media import MediaVerificationError


def test_media_resolver_rejects_symlink_and_traversal(tmp_path: Path) -> None:
    root = tmp_path / "media"
    root.mkdir()
    (root / "file").write_bytes(b"x")
    (root / "link").symlink_to(root / "file")
    resolver = FilesystemMediaResolver(root)
    digest = hashlib.sha256(b"x").hexdigest()

    with pytest.raises(MediaVerificationError):
        resolver.verify("../file", digest)
    with pytest.raises(MediaVerificationError):
        resolver.verify("link", digest)


def test_dump_number_uses_highest_valid_directory_name(tmp_path: Path) -> None:
    (tmp_path / "dump-invalid").mkdir()
    (tmp_path / "dump-000004").mkdir()

    manifest = FilesystemDumpRepository().publish(
        dumps_root=tmp_path,
        snapshot=GraphSnapshot(),
        published_at=datetime(2025, 1, 1, tzinfo=UTC),
    )

    assert manifest.dump_number == 5
    assert (tmp_path / "dump-000005").is_dir()
