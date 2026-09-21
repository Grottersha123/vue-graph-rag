from __future__ import annotations

import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from mvp_v2.adapters.csv.snapshot import CsvSnapshotError, write_snapshot
from mvp_v2.application.dto.importing import canonical_json_bytes
from mvp_v2.application.manifests import DUMP_FORMAT, DumpManifest, compute_dump_run_id
from mvp_v2.domain.schema_registry import (
    GRAPH_SCHEMA_VERSION,
    SCHEMA_PROFILE_SHA256,
)
from mvp_v2.domain.snapshot import GraphSnapshot


class DumpPublicationError(RuntimeError):
    """A dump publication operation failed."""


class FilesystemDumpRepository:
    """Write immutable numbered dumps for the single-process local application."""

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
            number = self._next_number(dumps_root)
            final = dumps_root / f"dump-{number:06d}"
            if final.exists() or final.is_symlink():
                raise DumpPublicationError("next dump path already exists")
            stage = Path(
                tempfile.mkdtemp(prefix=f".dump-{number:06d}-", dir=dumps_root)
            )
        except OSError as error:
            raise DumpPublicationError("dump staging could not be created") from error
        published = False
        try:
            entries = write_snapshot(stage, snapshot)
            manifest = DumpManifest(
                format=DUMP_FORMAT,
                graph_schema_version=GRAPH_SCHEMA_VERSION,
                schema_profile_sha256=SCHEMA_PROFILE_SHA256,
                dump_number=number,
                published_at=published_at,
                run_id=compute_dump_run_id(
                    number,
                    published_at,
                    entries,
                    schema_profile_sha256=SCHEMA_PROFILE_SHA256,
                ),
                files=entries,
            )
            (stage / "manifest.json").write_bytes(
                canonical_json_bytes(manifest.canonical_payload())
            )
            stage.rename(final)
            published = True
            return manifest
        except (OSError, CsvSnapshotError) as error:
            raise DumpPublicationError("dump staging failed") from error
        finally:
            if not published:
                shutil.rmtree(stage, ignore_errors=True)

    @staticmethod
    def _next_number(root: Path) -> int:
        try:
            numbers = [
                int(entry.name[5:])
                for entry in root.iterdir()
                if entry.is_dir()
                and not entry.is_symlink()
                and entry.name.startswith("dump-")
                and len(entry.name) == 11
                and entry.name[5:].isascii()
                and entry.name[5:].isdigit()
            ]
        except OSError as error:
            raise DumpPublicationError("dumps root could not be scanned") from error
        return max(numbers, default=0) + 1
