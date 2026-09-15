from __future__ import annotations

from datetime import datetime
from pathlib import Path

from mvp_v2.application.dto.importing import LegacyImportResult
from mvp_v2.application.manifests import DumpManifest
from mvp_v2.application.services.validation import validate_snapshot
from mvp_v2.ports.media import MediaResolver, MediaVerificationError
from mvp_v2.ports.migration import DumpRepository, LegacyImporter


class MigrationApprovalError(RuntimeError):
    """The explicit report and publication gate was not satisfied."""


class LegacyMigrationService:
    def __init__(
        self, *, importer: LegacyImporter, dump_repository: DumpRepository
    ) -> None:
        self._importer = importer
        self._dump_repository = dump_repository

    def preview(self, *, runtime_data_root: Path) -> LegacyImportResult:
        return self._importer.preview(runtime_data_root)

    def publish(
        self,
        *,
        result: LegacyImportResult,
        approved_report_id: str,
        dumps_root: Path,
        media_resolver: MediaResolver,
        published_at: datetime,
    ) -> DumpManifest:
        self._check_approval(result, approved_report_id)
        validation = validate_snapshot(
            result.snapshot, mode="publication", media_resolver=media_resolver
        )
        if not validation.is_valid:
            raise MigrationApprovalError(
                "target graph or referenced media failed publication validation"
            )
        self._verify_document_versions(result, media_resolver)
        return self._dump_repository.publish(
            dumps_root=dumps_root,
            snapshot=result.snapshot,
            published_at=published_at,
        )

    @staticmethod
    def _verify_document_versions(
        result: LegacyImportResult, media_resolver: MediaResolver
    ) -> None:
        for version in result.snapshot.document_versions:
            if not version.local_path:
                continue
            try:
                verification = media_resolver.verify(version.local_path, version.sha256)
            except MediaVerificationError:
                raise MigrationApprovalError(
                    "target graph or referenced media failed publication validation"
                ) from None
            if verification.status != "verified":
                raise MigrationApprovalError(
                    "target graph or referenced media failed publication validation"
                )

    @staticmethod
    def _check_approval(result: LegacyImportResult, approved_report_id: str) -> None:
        if not result.report.is_publishable:
            raise MigrationApprovalError("import report contains blocking issues")
        if approved_report_id != result.report.report_id:
            raise MigrationApprovalError("approved report ID does not match preview")
