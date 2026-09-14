from __future__ import annotations

from datetime import datetime
from pathlib import Path

from mvp_v2.application.dto.importing import LegacyImportResult
from mvp_v2.application.manifests import DumpManifest
from mvp_v2.application.services.validation import validate_snapshot
from mvp_v2.ports.media import MediaResolver, MediaVerificationError
from mvp_v2.ports.migration import DumpRepository, LegacyImporter, MediaAuthority


class MigrationApprovalError(RuntimeError):
    """The explicit report/media/publication gate was not satisfied."""


class LegacyMigrationService:
    def __init__(
        self,
        *,
        importer: LegacyImporter,
        media_authority: MediaAuthority,
        dump_repository: DumpRepository,
    ) -> None:
        self._importer = importer
        self._media_authority = media_authority
        self._dump_repository = dump_repository

    def preview(self, *, source_root: Path) -> LegacyImportResult:
        return self._importer.preview(source_root)

    def copy_media(
        self,
        *,
        result: LegacyImportResult,
        approved_report_id: str,
        source_root: Path,
        media_root: Path,
    ) -> MediaResolver:
        self._check_approval(result, approved_report_id)
        return self._media_authority.copy(
            source_root=source_root, destination=media_root, plan=result.copy_plan
        )

    def copy_and_publish(
        self,
        *,
        result: LegacyImportResult,
        approved_report_id: str,
        source_root: Path,
        media_root: Path,
        dumps_root: Path,
        published_at: datetime,
    ) -> DumpManifest:
        """Execute the approved copy, target verification, and dump gates."""
        resolver = self.copy_media(
            result=result,
            approved_report_id=approved_report_id,
            source_root=source_root,
            media_root=media_root,
        )
        return self.publish(
            result=result,
            approved_report_id=approved_report_id,
            dumps_root=dumps_root,
            media_resolver=resolver,
            published_at=published_at,
        )

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
                "target graph or media failed publication validation"
            )
        self._verify_complete_authority(result, media_resolver)
        # Recheck immediately before the only permanent dump operation.
        self._check_approval(result, approved_report_id)
        return self._dump_repository.publish(
            dumps_root=dumps_root,
            snapshot=result.snapshot,
            published_at=published_at,
        )

    @staticmethod
    def _verify_complete_authority(
        result: LegacyImportResult, media_resolver: MediaResolver
    ) -> None:
        for entry in result.copy_plan.entries:
            try:
                verification = media_resolver.verify(entry.target_path, entry.sha256)
            except MediaVerificationError:
                raise MigrationApprovalError(
                    "complete target media authority verification failed"
                ) from None
            if verification.status != "verified":
                raise MigrationApprovalError(
                    "complete target media authority verification failed"
                )

    @staticmethod
    def _check_approval(result: LegacyImportResult, approved_report_id: str) -> None:
        if not result.report.is_publishable:
            raise MigrationApprovalError("import report contains blocking issues")
        if approved_report_id != result.report.report_id:
            raise MigrationApprovalError("approved report ID does not match preview")
