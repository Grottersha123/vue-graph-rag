from __future__ import annotations

from pathlib import Path
from typing import Protocol

from mvp_v2.application.deletion import apply_deletion, preview_deletion
from mvp_v2.application.dto.deletion import (
    DatabaseIdentity,
    DeletionCommand,
    DeletionImpact,
    DeletionResult,
)
from mvp_v2.application.manifests import DatabaseManifest, DumpManifest
from mvp_v2.domain.snapshot import GraphSnapshot
from mvp_v2.ports.ladybug import ReplacementBuildError


class DumpPublisher(Protocol):
    def publish(self, *, snapshot: GraphSnapshot) -> DumpManifest: ...


class ReplacementBuilder(Protocol):
    def build(self, *, dump: DumpManifest, target: Path) -> DatabaseManifest: ...


class DatabaseCatalog(Protocol):
    def active(self) -> DatabaseIdentity: ...
    def identities(self) -> tuple[DatabaseIdentity, ...]: ...
    def switch(self, identity: DatabaseIdentity, path: Path) -> None: ...
    def mark_stale_before_dump(self, dump_number: int) -> None: ...


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
