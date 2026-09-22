from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from mvp_v2.adapters.filesystem.dumps import FilesystemDumpRepository
from mvp_v2.application.services.publication import (
    SnapshotPublicationError,
    SnapshotPublicationService,
)
from mvp_v2.ports.media import MediaVerification
from tests.factories import build_valid_snapshot


class Resolver:
    def __init__(self, *, fail_document: bool = False) -> None:
        self._fail_document = fail_document

    def verify(self, relative_path: str, expected_sha256: str) -> MediaVerification:
        del expected_sha256
        if self._fail_document and relative_path == "documents/a.md":
            return MediaVerification(status="hash_mismatch")
        return MediaVerification(status="verified")


def test_current_snapshot_publication_writes_first_v2_dump(tmp_path: Path) -> None:
    manifest = SnapshotPublicationService(FilesystemDumpRepository()).publish(
        snapshot=build_valid_snapshot(),
        dumps_root=tmp_path,
        published_at=datetime(2026, 9, 21, tzinfo=UTC),
        media_resolver=Resolver(),
    )

    assert manifest.dump_number == 1
    assert manifest.graph_schema_version == "mvp-v2-graph-v2"
    assert len(manifest.files) == 22
    assert (tmp_path / "dump-000001/manifest.json").is_file()


def test_publication_blocks_changed_document_version_file(tmp_path: Path) -> None:
    with pytest.raises(SnapshotPublicationError):
        SnapshotPublicationService(FilesystemDumpRepository()).publish(
            snapshot=build_valid_snapshot(),
            dumps_root=tmp_path,
            published_at=datetime(2026, 9, 21, tzinfo=UTC),
            media_resolver=Resolver(fail_document=True),
        )

    assert not (tmp_path / "dump-000001").exists()
