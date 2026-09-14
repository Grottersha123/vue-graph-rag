from __future__ import annotations

import fcntl
import hashlib
import os
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from mvp_v2.adapters.csv.snapshot import CsvSnapshotError, read_snapshot, write_snapshot
from mvp_v2.application.dto.importing import canonical_json_bytes
from mvp_v2.application.manifests import (
    DUMP_FORMAT,
    DumpManifest,
    compute_dump_run_id,
)
from mvp_v2.domain.invariants import validate_graph
from mvp_v2.domain.schema_registry import GRAPH_SCHEMA_VERSION
from mvp_v2.domain.snapshot import GraphSnapshot


class DumpPublicationError(RuntimeError):
    """A safe dump publication operation failed."""


class FilesystemDumpRepository:
    """Publish immutable sequential dumps under an exclusive filesystem lock."""

    def publish(
        self, *, dumps_root: Path, snapshot: GraphSnapshot, published_at: datetime
    ) -> DumpManifest:
        if published_at.tzinfo is None or published_at.utcoffset() != UTC.utcoffset(
            published_at
        ):
            raise DumpPublicationError("publication timestamp must use UTC")
        if dumps_root.is_symlink():
            raise DumpPublicationError("dumps root must not be a symlink")
        try:
            dumps_root.mkdir(parents=True, exist_ok=True)
            lock_path = dumps_root / ".publish.lock"
            descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        except OSError as error:
            raise DumpPublicationError(
                "dump publication lock is unavailable"
            ) from error
        try:
            with os.fdopen(descriptor, "r+b") as lock:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
                return self._publish_locked(dumps_root, snapshot, published_at)
        except OSError as error:
            raise DumpPublicationError(
                "dump publication filesystem operation failed"
            ) from error

    def _publish_locked(
        self, root: Path, snapshot: GraphSnapshot, published_at: datetime
    ) -> DumpManifest:
        existing = self._validate_existing(root)
        number = len(existing) + 1
        final = root / f"dump-{number:06d}"
        if final.exists() or final.is_symlink():
            raise DumpPublicationError("next dump path already exists")
        try:
            stage = Path(tempfile.mkdtemp(prefix=f".dump-{number:06d}-", dir=root))
        except OSError as error:
            raise DumpPublicationError("dump staging could not be created") from error
        published = False
        try:
            entries = write_snapshot(stage, snapshot)
            reread = read_snapshot(stage)
            validation = validate_graph(reread)
            if reread != snapshot or not validation.is_valid:
                raise DumpPublicationError("staged graph failed publication validation")
            for entry in entries:
                table_path = stage / entry.path
                if _hash_file(table_path) != entry.sha256:
                    raise DumpPublicationError("staged table hash verification failed")
            manifest = DumpManifest(
                format=DUMP_FORMAT,
                graph_schema_version=GRAPH_SCHEMA_VERSION,
                dump_number=number,
                published_at=published_at,
                run_id=compute_dump_run_id(number, published_at, entries),
                files=entries,
            )
            manifest_path = stage / "manifest.json"
            manifest_path.write_bytes(
                canonical_json_bytes(manifest.model_dump(mode="json"))
            )
            _fsync_file(manifest_path)
            for entry in entries:
                _fsync_file(stage / entry.path)
            _fsync_directory(stage / "tables")
            _fsync_directory(stage)
            stage.rename(final)
            _fsync_directory(root)
            published = True
            return manifest
        except (OSError, CsvSnapshotError, ValidationError) as error:
            raise DumpPublicationError("dump staging or validation failed") from error
        finally:
            if not published:
                shutil.rmtree(stage, ignore_errors=True)

    def _validate_existing(self, root: Path) -> tuple[Path, ...]:
        numbered: list[tuple[int, Path]] = []
        try:
            entries = tuple(root.iterdir())
        except OSError as error:
            raise DumpPublicationError("dumps root could not be scanned") from error
        for entry in entries:
            if entry.name.startswith(".dump-"):
                if entry.is_dir() and not entry.is_symlink():
                    shutil.rmtree(entry, ignore_errors=True)
                continue
            if not entry.name.startswith("dump-"):
                continue
            suffix = entry.name[5:]
            if (
                len(suffix) != 6
                or not suffix.isascii()
                or not suffix.isdigit()
                or entry.is_symlink()
                or not entry.is_dir()
            ):
                raise DumpPublicationError(
                    "existing dump sequence contains an invalid entry"
                )
            numbered.append((int(suffix), entry))
        numbered.sort()
        if [item[0] for item in numbered] != list(range(1, len(numbered) + 1)):
            raise DumpPublicationError("existing dump sequence is not contiguous")
        for number, path in numbered:
            self._validate_dump(path, number)
        return tuple(path for _, path in numbered)

    def _validate_dump(self, root: Path, number: int) -> None:
        try:
            if {item.name for item in root.iterdir()} != {"tables", "manifest.json"}:
                raise DumpPublicationError("existing dump contains unexpected entries")
            manifest_path = root / "manifest.json"
            if manifest_path.is_symlink() or not manifest_path.is_file():
                raise DumpPublicationError("existing dump manifest is invalid")
            raw = manifest_path.read_bytes()
            manifest = DumpManifest.model_validate_json(raw)
            if raw != canonical_json_bytes(manifest.model_dump(mode="json")):
                raise DumpPublicationError("existing dump manifest is not canonical")
            if manifest.dump_number != number:
                raise DumpPublicationError(
                    "existing dump number disagrees with manifest"
                )
            snapshot = read_snapshot(root)
            counts = _snapshot_counts(snapshot)
            for file_entry in manifest.files:
                path = root / file_entry.path
                if (
                    _hash_file(path) != file_entry.sha256
                    or counts[file_entry.path] != file_entry.row_count
                ):
                    raise DumpPublicationError("existing dump file facts do not match")
        except (OSError, ValidationError, CsvSnapshotError, KeyError) as error:
            raise DumpPublicationError("existing dump validation failed") from error


def _snapshot_counts(snapshot: GraphSnapshot) -> dict[str, int]:
    from mvp_v2.domain.schema_registry import TABLE_SPECS

    return {
        f"tables/{spec.table_name}.csv": len(
            snapshot.__getattribute__(spec.snapshot_field)
        )
        for spec in TABLE_SPECS
    }


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _fsync_file(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
