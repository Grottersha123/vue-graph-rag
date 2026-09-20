from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from mvp_v2.adapters.filesystem.media import FilesystemMediaDeleter
from mvp_v2.application.deletion import preview_deletion
from mvp_v2.application.dto.deletion import DeletionCommand, GraphNodeRef
from mvp_v2.bootstrap.ui import UiRuntimeConfig
from mvp_v2.ports.media import MediaDeletionError, MediaDeletionTarget
from mvp_v2.presentation.controller import confirm_file_deletion
from tests.factories import build_valid_snapshot


def _document_command() -> DeletionCommand:
    return DeletionCommand(target=GraphNodeRef(table_name="Document", id="document"))


def _runtime_config(tmp_path: Path) -> UiRuntimeConfig:
    paths = {
        name: tmp_path / name
        for name in ("working", "dumps", "databases", "ladybug-home")
    }
    for path in paths.values():
        path.mkdir()
    return UiRuntimeConfig.model_construct(
        working_root=paths["working"],
        dumps_root=paths["dumps"],
        databases_root=paths["databases"],
        ladybug_home=paths["ladybug-home"],
    )


def test_file_first_deletion_publishes_dump_then_deletes_owned_media(
    tmp_path: Path,
) -> None:
    config = _runtime_config(tmp_path)
    document_path = config.working_root / "documents/a.md"
    asset_path = config.working_root / "images/a.png"
    document_path.parent.mkdir()
    asset_path.parent.mkdir()
    document_content = b"document"
    asset_content = b"image"
    document_path.write_bytes(document_content)
    asset_path.write_bytes(asset_content)
    snapshot = build_valid_snapshot()
    snapshot = snapshot.model_copy(
        update={
            "document_versions": (
                snapshot.document_versions[0].model_copy(
                    update={"sha256": hashlib.sha256(document_content).hexdigest()}
                ),
            ),
            "assets": (
                snapshot.assets[0].model_copy(
                    update={"sha256": hashlib.sha256(asset_content).hexdigest()}
                ),
            ),
        }
    )
    command = _document_command()

    outcome = confirm_file_deletion(
        snapshot=snapshot,
        command=command,
        confirmation=preview_deletion(snapshot, command),
        config=config,
    )

    assert outcome.dump.dump_number == 1
    assert not outcome.snapshot.documents
    assert outcome.deleted_paths == ("documents/a.md", "images/a.png")
    assert outcome.missing_paths == ()
    assert outcome.failed_paths == ()
    assert not document_path.exists()
    assert not asset_path.exists()
    assert (config.dumps_root / "dump-000001" / "manifest.json").is_file()


def test_changed_media_aborts_before_new_dump_is_published(tmp_path: Path) -> None:
    config = _runtime_config(tmp_path)
    document_path = config.working_root / "documents/a.md"
    asset_path = config.working_root / "images/a.png"
    document_path.parent.mkdir()
    asset_path.parent.mkdir()
    document_path.write_text("changed document")
    asset_path.write_bytes(b"changed image")
    snapshot = build_valid_snapshot()
    command = _document_command()

    with pytest.raises(RuntimeError, match="physical media deletion failed safely"):
        confirm_file_deletion(
            snapshot=snapshot,
            command=command,
            confirmation=preview_deletion(snapshot, command),
            config=config,
        )

    assert not tuple(config.dumps_root.iterdir())
    assert document_path.is_file()
    assert asset_path.is_file()


def test_deletion_preview_does_not_delete_path_still_referenced_by_graph() -> None:
    snapshot = build_valid_snapshot()
    shared_version = snapshot.document_versions[0].model_copy(
        update={"local_path": "images/a.png"}
    )
    snapshot = snapshot.model_copy(update={"document_versions": (shared_version,)})
    command = DeletionCommand(target=GraphNodeRef(table_name="Asset", id="asset"))

    impact = preview_deletion(snapshot, command)

    assert impact.referenced_physical_paths == ()


def test_filesystem_media_deleter_rejects_path_escape(tmp_path: Path) -> None:
    deleter = FilesystemMediaDeleter(tmp_path)

    target = MediaDeletionTarget(
        relative_path="../outside.txt", expected_sha256="a" * 64
    )

    with pytest.raises(MediaDeletionError):
        deleter.validate((target,))


def test_filesystem_media_deleter_rejects_changed_file(tmp_path: Path) -> None:
    media_path = tmp_path / "document.md"
    media_path.write_text("changed")
    target = MediaDeletionTarget(relative_path="document.md", expected_sha256="a" * 64)

    with pytest.raises(MediaDeletionError):
        FilesystemMediaDeleter(tmp_path).validate((target,))

    assert media_path.is_file()


def test_filesystem_media_deleter_reports_missing_file(tmp_path: Path) -> None:
    target = MediaDeletionTarget(relative_path="missing.md", expected_sha256="a" * 64)

    report = FilesystemMediaDeleter(tmp_path).delete((target,))

    assert report.deleted_paths == ()
    assert report.missing_paths == ("missing.md",)
    assert report.failed_paths == ()
