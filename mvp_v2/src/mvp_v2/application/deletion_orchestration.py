from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from mvp_v2.application.dto.deletion import (
    DatabaseIdentity,
    DeletionCommand,
    DeletionImpact,
    DeletionResult,
)
from mvp_v2.application.dump_lifecycle.manifests import DatabaseManifest, DumpManifest
from mvp_v2.application.graph_snapshot_operations.deletion import (
    apply_deletion,
    preview_deletion,
)
from mvp_v2.domain.snapshot import GraphSnapshot
from mvp_v2.ports.ladybug import ReplacementBuildError
from mvp_v2.ports.media import MediaDeleter, MediaDeletionTarget


class DumpPublisher(Protocol):
    def publish(self, *, snapshot: GraphSnapshot) -> DumpManifest: ...


class ReplacementBuilder(Protocol):
    def build(self, *, dump: DumpManifest, target: Path) -> DatabaseManifest: ...


class DatabaseCatalog(Protocol):
    def active(self) -> DatabaseIdentity: ...
    def identities(self) -> tuple[DatabaseIdentity, ...]: ...
    def switch(self, identity: DatabaseIdentity, path: Path) -> None: ...
    def mark_stale_before_dump(self, dump_number: int) -> None: ...


@dataclass(frozen=True, slots=True)
class FileDeletionOutcome:
    snapshot: GraphSnapshot
    dump: DumpManifest
    deleted_paths: tuple[str, ...]
    missing_paths: tuple[str, ...]
    failed_paths: tuple[str, ...]


class FileFirstDeletionService:
    def __init__(self, publisher: DumpPublisher, media_deleter: MediaDeleter) -> None:
        self._publisher = publisher
        self._media_deleter = media_deleter

    def preview(
        self, snapshot: GraphSnapshot, command: DeletionCommand
    ) -> DeletionImpact:
        return preview_deletion(snapshot, command)

    def confirm(
        self,
        snapshot: GraphSnapshot,
        command: DeletionCommand,
        confirmation: DeletionImpact,
    ) -> FileDeletionOutcome:
        expected = self.preview(snapshot, command)
        if confirmation != expected or confirmation.command != command:
            raise ValueError("deletion confirmation is stale or mismatched")
        updated = apply_deletion(snapshot, command)
        deletion_targets = _build_media_deletion_targets(
            snapshot, expected.referenced_physical_paths
        )
        self._media_deleter.validate(deletion_targets)
        dump = self._publisher.publish(snapshot=updated)
        media_report = self._media_deleter.delete(deletion_targets)
        return FileDeletionOutcome(
            snapshot=updated,
            dump=dump,
            deleted_paths=media_report.deleted_paths,
            missing_paths=media_report.missing_paths,
            failed_paths=media_report.failed_paths,
        )


def _build_media_deletion_targets(
    snapshot: GraphSnapshot, relative_paths: tuple[str, ...]
) -> tuple[MediaDeletionTarget, ...]:
    hashes_by_path: dict[str, set[str]] = {}
    for document_version in snapshot.document_versions:
        if document_version.local_path in relative_paths:
            hashes_by_path.setdefault(document_version.local_path, set()).add(
                document_version.sha256
            )
    for asset in snapshot.assets:
        if asset.local_path in relative_paths:
            hashes_by_path.setdefault(asset.local_path, set()).add(asset.sha256)
    targets: list[MediaDeletionTarget] = []
    for relative_path in relative_paths:
        expected_hashes = hashes_by_path.get(relative_path, set())
        if len(expected_hashes) != 1:
            raise ValueError("physical media deletion requires one expected hash")
        targets.append(
            MediaDeletionTarget(
                relative_path=relative_path,
                expected_sha256=next(iter(expected_hashes)),
            )
        )
    return tuple(targets)


class DeletionService:
    def __init__(
        self,
        publisher: DumpPublisher,
        builder: ReplacementBuilder,
        catalog: DatabaseCatalog,
        replacement_root: Path,
    ) -> None:
        self._publisher, self._builder, self._catalog, self._root = (
            publisher,
            builder,
            catalog,
            replacement_root,
        )

    def preview(
        self, snapshot: GraphSnapshot, command: DeletionCommand
    ) -> DeletionImpact:
        return preview_deletion(snapshot, command, self._catalog.identities())

    def confirm(
        self,
        snapshot: GraphSnapshot,
        command: DeletionCommand,
        confirmation: DeletionImpact,
    ) -> DeletionResult:
        expected = self.preview(snapshot, command)
        if confirmation != expected or confirmation.command != command:
            raise ValueError("deletion confirmation is stale or mismatched")
        updated = apply_deletion(snapshot, command)
        previous = self._catalog.active()
        dump = self._publisher.publish(snapshot=updated)
        target = (
            self._root / f"{previous.database_name}-dump-{dump.dump_number:06d}.lbdb"
        )
        stale = self._catalog.identities()
        self._catalog.mark_stale_before_dump(dump.dump_number)
        try:
            manifest = self._builder.build(dump=dump, target=target)
        except ReplacementBuildError:
            return DeletionResult(
                command=command,
                status="pending",
                published_dump_number=dump.dump_number,
                active_database=previous,
                stale_databases=stale,
                error=(
                    "replacement database build failed; previous database retained "
                    "as stale pending replacement"
                ),
            )
        identity = DatabaseIdentity(
            database_name=manifest.database_name, build_id=manifest.build_id
        )
        self._catalog.switch(identity, target)
        return DeletionResult(
            command=command,
            status="completed",
            published_dump_number=dump.dump_number,
            active_database=identity,
            stale_databases=stale,
        )


class InMemoryDatabaseCatalog:
    def __init__(
        self, active: DatabaseIdentity, values: tuple[DatabaseIdentity, ...] = ()
    ) -> None:
        self._active = active
        self._values = values or (active,)
        self.stale: set[str] = set()
        self.path: Path | None = None

    def active(self) -> DatabaseIdentity:
        return self._active

    def identities(self) -> tuple[DatabaseIdentity, ...]:
        return tuple(sorted(self._values, key=lambda x: (x.database_name, x.build_id)))

    def switch(self, identity: DatabaseIdentity, path: Path) -> None:
        self._active, self.path = identity, path
        self._values = (*self._values, identity)

    def mark_stale_before_dump(self, dump_number: int) -> None:
        del dump_number
        self.stale.update(x.build_id for x in self._values)
