from __future__ import annotations

from datetime import datetime
from pathlib import Path

from mvp_v2.application.dump_lifecycle.manifests import DumpManifest
from mvp_v2.application.dump_lifecycle.validation import validate_snapshot
from mvp_v2.domain.snapshot import GraphSnapshot
from mvp_v2.ports.dumps import DumpRepository
from mvp_v2.ports.media import MediaResolver, MediaVerificationError

PUBLICATION_ERROR = "snapshot or referenced files failed publication validation"


class SnapshotPublicationError(RuntimeError):
    """The snapshot did not satisfy the publication gate."""


class SnapshotPublicationService:
    """Validate a current snapshot immediately before immutable publication."""

    def __init__(self, repository: DumpRepository) -> None:
        self._repository = repository

    def publish(
        self,
        *,
        snapshot: GraphSnapshot,
        dumps_root: Path,
        published_at: datetime,
        media_resolver: MediaResolver,
    ) -> DumpManifest:
        validation = validate_snapshot(
            snapshot, mode="publication", media_resolver=media_resolver
        )
        if not validation.is_valid:
            raise SnapshotPublicationError(PUBLICATION_ERROR)
        self._verify_versions(snapshot, media_resolver)
        return self._repository.publish(
            dumps_root=dumps_root, snapshot=snapshot, published_at=published_at
        )

    @staticmethod
    def _verify_versions(snapshot: GraphSnapshot, resolver: MediaResolver) -> None:
        for version in snapshot.document_versions:
            # Старую (неактуальную) версию файл могут удалить: её содержимое
            # уже в дампе, а активная проекция использует только текущие.
            if not version.local_path or not version.is_current:
                continue
            try:
                result = resolver.verify(version.local_path, version.sha256)
            except MediaVerificationError:
                raise SnapshotPublicationError(PUBLICATION_ERROR) from None
            if result.status != "verified":
                raise SnapshotPublicationError(PUBLICATION_ERROR)
