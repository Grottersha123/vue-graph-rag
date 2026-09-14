from __future__ import annotations

import csv
import hashlib
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from mvp_v2.adapters.csv.snapshot import read_snapshot
from mvp_v2.adapters.filesystem.dumps import (
    DumpPublicationError,
    FilesystemDumpRepository,
)
from mvp_v2.adapters.filesystem.media import (
    FilesystemMediaAuthority,
    FilesystemMediaResolver,
)
from mvp_v2.adapters.legacy import CsvLegacyImporter
from mvp_v2.adapters.legacy.reader import EXPECTED_HEADERS
from mvp_v2.application.dto.importing import (
    LegacyImportResult,
    MediaCopyPlan,
)
from mvp_v2.application.services.migration import (
    LegacyMigrationService,
    MigrationApprovalError,
)
from mvp_v2.domain import graph
from mvp_v2.domain.snapshot import GraphSnapshot
from mvp_v2.ports.media import MediaVerificationError


def _write_csv(root: Path, name: str, rows: list[list[str]]) -> None:
    with (root / f"{name}.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream, lineterminator="\r\n")
        writer.writerow(EXPECTED_HEADERS[name])
        writer.writerows(rows)


def _legacy_tree(root: Path) -> None:
    for directory in ("documents", "images", "uploads"):
        (root / directory).mkdir(parents=True)
    document_bytes = b"document"
    image_bytes = b"image"
    upload_bytes = b"upload"
    (root / "documents/doc.md").write_bytes(document_bytes)
    (root / "images/image.png").write_bytes(image_bytes)
    (root / "uploads/raw.md").write_bytes(upload_bytes)
    document_sha = hashlib.sha256(document_bytes).hexdigest()
    image_sha = hashlib.sha256(image_bytes).hexdigest()
    rows: dict[str, list[list[str]]] = {name: [] for name in EXPECTED_HEADERS}
    rows["SourceType"] = [["source-web", "external_web"]]
    rows["Document"] = [
        [
            "doc-1",
            "Title",
            "https://example.invalid/doc",
            "external-1",
            "external_web",
            document_sha,
            "mvp/mvp_data/documents/doc.md",
            "markdown",
            "html",
            "source",
            "true",
            "downloaded",
        ]
    ]
    rows["DocumentVersion"] = [
        [
            "version-1",
            "1",
            document_sha,
            "mvp/mvp_data/documents/doc.md",
            "2025-01-01T00:00:00.000000+00:00",
            "true",
            "",
        ]
    ]
    rows["Asset"] = [
        [
            "asset-1",
            "image",
            "image/png",
            "https://example.invalid/image",
            "mvp/mvp_data/images/image.png",
            "external-image",
            image_sha,
            "0",
            "caption",
            "ocr",
        ]
    ]
    rows["Role"] = [["role-1", "student"]]
    rows["Status"] = [["status-downloaded", "downloaded", "Downloaded"]]
    rows["HAS_VERSION"] = [["doc-1", "version-1"]]
    rows["HAS_ASSET"] = [["version-1", "asset-1"]]
    rows["HAS_STATUS"] = [["version-1", "status-downloaded"]]
    rows["FOR_ROLE"] = [["doc-1", "role-1"]]
    rows["HAS_DOCUMENT"] = [["source-web", "doc-1"]]
    rows["image_manifest"] = [
        [
            "asset-1",
            "doc-1",
            "redacted",
            "mvp/mvp_data/images/image.png",
            "image/png",
            image_sha,
            "downloaded",
            "",
        ]
    ]
    for name, table_rows in rows.items():
        _write_csv(root, name, table_rows)


def test_preview_maps_graph_and_is_deterministic(tmp_path: Path) -> None:
    _legacy_tree(tmp_path)
    first = CsvLegacyImporter().preview(tmp_path)
    second = CsvLegacyImporter().preview(tmp_path)
    assert first.report.is_publishable
    assert first.report.canonical_bytes() == second.report.canonical_bytes()
    assert first.report.report_id == second.report.report_id
    assert first.snapshot.has_source_types == (
        graph.HasSourceType(source_id="doc-1", target_id="source-web"),
    )
    assert first.snapshot.assets[0].llm_description == ""
    assert first.snapshot.document_versions[0].local_path == "documents/doc.md"
    assert first.snapshot.assets[0].local_path == "images/image.png"
    assert not first.snapshot.chunks and not first.snapshot.embeddings
    assert first.report.documents_without_process == ("doc-1",)
    assert first.copy_plan.total_files == 3


def test_version_gaps_preserve_numbers_and_use_immediate_imported_predecessor(
    tmp_path: Path,
) -> None:
    _legacy_tree(tmp_path)
    old_bytes, current_bytes = b"old", b"current"
    (tmp_path / "documents/old.md").write_bytes(old_bytes)
    (tmp_path / "documents/current.md").write_bytes(current_bytes)
    old_sha = hashlib.sha256(old_bytes).hexdigest()
    current_sha = hashlib.sha256(current_bytes).hexdigest()
    _write_csv(
        tmp_path,
        "DocumentVersion",
        [
            [
                "version-1",
                "1",
                old_sha,
                "mvp/mvp_data/documents/old.md",
                "2025-01-01T00:00:00.000000+00:00",
                "false",
                "",
            ],
            [
                "version-3",
                "3",
                current_sha,
                "mvp/mvp_data/documents/current.md",
                "2025-01-02T00:00:00.000000+00:00",
                "true",
                "",
            ],
        ],
    )
    _write_csv(
        tmp_path,
        "HAS_VERSION",
        [
            ["doc-1", "version-1"],
            ["doc-1", "version-3"],
        ],
    )
    _write_csv(
        tmp_path,
        "HAS_STATUS",
        [
            ["version-1", "status-downloaded"],
            ["version-3", "status-downloaded"],
        ],
    )
    _write_csv(
        tmp_path,
        "Document",
        [
            [
                "doc-1",
                "Title",
                "https://example.invalid/doc",
                "external-1",
                "external_web",
                current_sha,
                "mvp/mvp_data/documents/current.md",
                "markdown",
                "html",
                "source",
                "true",
                "downloaded",
            ]
        ],
    )
    result = CsvLegacyImporter().preview(tmp_path)
    assert result.report.is_publishable
    assert [item.version for item in result.snapshot.document_versions] == [1, 3]
    assert result.snapshot.supersedes == (
        graph.Supersedes(source_id="version-3", target_id="version-1"),
    )


def test_preview_collects_blocking_scalar_and_header_errors(tmp_path: Path) -> None:
    _legacy_tree(tmp_path)
    document = tmp_path / "Document.csv"
    text = document.read_text(encoding="utf-8").replace(
        ",true,downloaded", ",TRUE,downloaded"
    )
    document.write_text(text, encoding="utf-8")
    _write_csv(tmp_path, "Role", [["role-1", "student", "extra"]])
    result = CsvLegacyImporter().preview(tmp_path)
    assert not result.report.is_publishable
    codes = {issue.code for issue in result.report.issues}
    assert "SCALAR_OR_MODEL_ERROR" in codes
    assert "MALFORMED_ROW" in codes


def test_media_copy_verifies_and_rolls_back(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _legacy_tree(source)
    result = CsvLegacyImporter().preview(source)
    authority = tmp_path / "authority"
    resolver = FilesystemMediaAuthority().copy(
        source_root=source, destination=authority, plan=result.copy_plan
    )
    asset = result.snapshot.assets[0]
    assert resolver.verify(asset.local_path, asset.sha256).status == "verified"
    with pytest.raises(MediaVerificationError):
        FilesystemMediaAuthority().copy(
            source_root=source, destination=authority, plan=result.copy_plan
        )
    assert not tuple(tmp_path.glob(".authority-copy-*"))


def test_media_copy_changed_source_rolls_back_and_can_retry(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _legacy_tree(source)
    result = CsvLegacyImporter().preview(source)
    image = source / "images/image.png"
    image.write_bytes(b"changed-after-preview")
    destination = tmp_path / "authority"
    with pytest.raises(MediaVerificationError):
        FilesystemMediaAuthority().copy(
            source_root=source, destination=destination, plan=result.copy_plan
        )
    assert not destination.exists()
    assert not tuple(tmp_path.glob(".authority-copy-*"))
    image.write_bytes(b"image")
    FilesystemMediaAuthority().copy(
        source_root=source, destination=destination, plan=result.copy_plan
    )
    assert (destination / "images/image.png").read_bytes() == b"image"


def test_media_resolver_rejects_symlink_and_traversal(tmp_path: Path) -> None:
    root = tmp_path / "media"
    root.mkdir()
    (root / "file").write_bytes(b"x")
    (root / "link").symlink_to(root / "file")
    resolver = FilesystemMediaResolver(root)
    with pytest.raises(MediaVerificationError):
        resolver.verify("../file", hashlib.sha256(b"x").hexdigest())
    with pytest.raises(MediaVerificationError):
        resolver.verify("link", hashlib.sha256(b"x").hexdigest())


def test_publisher_numbers_concurrently_and_preserves_canonical_readback(
    tmp_path: Path,
) -> None:
    snapshot = GraphSnapshot()
    repository = FilesystemDumpRepository()
    published_at = datetime(2025, 1, 1, tzinfo=UTC)
    with ThreadPoolExecutor(max_workers=2) as executor:
        manifests = list(
            executor.map(
                lambda _: repository.publish(
                    dumps_root=tmp_path, snapshot=snapshot, published_at=published_at
                ),
                range(2),
            )
        )
    assert sorted(item.dump_number for item in manifests) == [1, 2]
    assert read_snapshot(tmp_path / "dump-000001") == snapshot
    assert read_snapshot(tmp_path / "dump-000002") == snapshot
    assert len(manifests[0].files) == 21


def test_application_gates_approval_and_integrates_to_first_dump(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _legacy_tree(source)
    service = LegacyMigrationService(
        importer=CsvLegacyImporter(),
        media_authority=FilesystemMediaAuthority(),
        dump_repository=FilesystemDumpRepository(),
    )
    result = service.preview(source_root=source)
    with pytest.raises(MigrationApprovalError):
        service.copy_media(
            result=result,
            approved_report_id="0" * 64,
            source_root=source,
            media_root=tmp_path / "media",
        )
    resolver = service.copy_media(
        result=result,
        approved_report_id=result.report.report_id,
        source_root=source,
        media_root=tmp_path / "media",
    )
    manifest = service.publish(
        result=result,
        approved_report_id=result.report.report_id,
        dumps_root=tmp_path / "dumps",
        media_resolver=resolver,
        published_at=datetime(2025, 1, 1, tzinfo=UTC),
    )
    assert manifest.dump_number == 1
    assert read_snapshot(tmp_path / "dumps/dump-000001") == result.snapshot


def test_empty_sha_current_flag_has_explicit_nonblocking_rewrite(
    tmp_path: Path,
) -> None:
    _legacy_tree(tmp_path)
    _write_csv(
        tmp_path,
        "Document",
        [
            [
                "doc-1",
                "Title",
                "https://example.invalid/doc",
                "external-1",
                "external_web",
                "",
                "",
                "markdown",
                "html",
                "source",
                "false",
                "error",
            ]
        ],
    )
    _write_csv(
        tmp_path,
        "DocumentVersion",
        [
            [
                "version-1",
                "1",
                "",
                "",
                "2025-01-01T00:00:00.000000+00:00",
                "true",
                "legacy failure",
            ]
        ],
    )
    _write_csv(tmp_path, "Status", [["status-error", "error", "Error"]])
    _write_csv(tmp_path, "HAS_STATUS", [["version-1", "status-error"]])
    _write_csv(tmp_path, "HAS_ERROR", [["doc-1", "error"]])
    result = CsvLegacyImporter().preview(tmp_path)
    assert result.report.is_publishable
    assert result.snapshot.document_versions[0].is_current is False
    assert any(
        issue.code == "CURRENT_FLAG_REWRITTEN_FOR_EMPTY_SHA"
        for issue in result.report.issues
    )


def test_missing_or_changed_referenced_media_blocks_preview(tmp_path: Path) -> None:
    _legacy_tree(tmp_path)
    (tmp_path / "images/image.png").write_bytes(b"changed")
    result = CsvLegacyImporter().preview(tmp_path)
    assert not result.report.is_publishable
    assert any(
        issue.code == "REFERENCED_MEDIA_HASH_MISMATCH" for issue in result.report.issues
    )


def test_failed_dump_validation_consumes_no_number_and_cleans_stage(
    tmp_path: Path,
) -> None:
    invalid = GraphSnapshot(
        documents=(
            graph.Document(
                id="doc",
                title="title",
                source_name="",
                content_format="markdown",
                source_url="",
                external_id="",
                is_active=False,
            ),
        )
    )
    repository = FilesystemDumpRepository()
    with pytest.raises(DumpPublicationError):
        repository.publish(
            dumps_root=tmp_path,
            snapshot=invalid,
            published_at=datetime(2025, 1, 1, tzinfo=UTC),
        )
    assert not (tmp_path / "dump-000001").exists()
    assert not tuple(tmp_path.glob(".dump-000001-*"))
    manifest = repository.publish(
        dumps_root=tmp_path,
        snapshot=GraphSnapshot(),
        published_at=datetime(2025, 1, 1, tzinfo=UTC),
    )
    assert manifest.dump_number == 1


def test_result_rejects_snapshot_change_with_approved_report(tmp_path: Path) -> None:
    _legacy_tree(tmp_path)
    result = CsvLegacyImporter().preview(tmp_path)
    changed_document = result.snapshot.documents[0].model_copy(
        update={"title": "Different title"}
    )
    changed_snapshot = result.snapshot.model_copy(
        update={"documents": (changed_document,)}
    )
    with pytest.raises(ValidationError, match="exact graph snapshot"):
        LegacyImportResult(
            snapshot=changed_snapshot,
            report=result.report,
            copy_plan=result.copy_plan,
        )


def test_result_rejects_copy_plan_change_with_same_totals(tmp_path: Path) -> None:
    _legacy_tree(tmp_path)
    result = CsvLegacyImporter().preview(tmp_path)
    entries = list(result.copy_plan.entries)
    entries[0] = entries[0].model_copy(update={"target_path": "documents/changed.md"})
    changed_plan = MediaCopyPlan(
        entries=tuple(entries),
        total_files=result.copy_plan.total_files,
        total_bytes=result.copy_plan.total_bytes,
    )
    with pytest.raises(ValidationError, match="exact media copy plan"):
        LegacyImportResult(
            snapshot=result.snapshot,
            report=result.report,
            copy_plan=changed_plan,
        )


@pytest.mark.parametrize("changed_path", ["documents/doc.md", "uploads/raw.md"])
def test_publish_rechecks_every_copied_file_before_dump(
    tmp_path: Path, changed_path: str
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _legacy_tree(source)
    service = LegacyMigrationService(
        importer=CsvLegacyImporter(),
        media_authority=FilesystemMediaAuthority(),
        dump_repository=FilesystemDumpRepository(),
    )
    result = service.preview(source_root=source)
    media_root = tmp_path / "media"
    resolver = service.copy_media(
        result=result,
        approved_report_id=result.report.report_id,
        source_root=source,
        media_root=media_root,
    )
    (media_root / changed_path).write_bytes(b"changed-after-copy")
    dumps_root = tmp_path / "dumps"
    with pytest.raises(
        MigrationApprovalError,
        match="complete target media authority verification failed",
    ):
        service.publish(
            result=result,
            approved_report_id=result.report.report_id,
            dumps_root=dumps_root,
            media_resolver=resolver,
            published_at=datetime(2025, 1, 1, tzinfo=UTC),
        )
    assert not (dumps_root / "dump-000001").exists()
