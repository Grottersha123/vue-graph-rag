from __future__ import annotations

import os
import re
import shutil
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from pydantic import ValidationError

from mvp_v2.adapters.filesystem.dumps import FilesystemDumpRepository
from mvp_v2.adapters.filesystem.media import (
    FilesystemMediaDeleter,
    FilesystemMediaResolver,
)
from mvp_v2.adapters.filesystem.runtime_store import ConfinedRuntimeFileStore
from mvp_v2.adapters.ingestion import LocalHttpUrlFetcher, LocalInputExtractor
from mvp_v2.adapters.ladybug import build_database
from mvp_v2.application.deletion_orchestration import (
    DeletionService,
    FileDeletionOutcome,
    FileFirstDeletionService,
)
from mvp_v2.application.dto.deletion import (
    DatabaseIdentity,
    DeletionCommand,
    DeletionImpact,
    DeletionResult,
)
from mvp_v2.application.dump_lifecycle.manifests import (
    DATABASE_FORMAT,
    DatabaseManifest,
    DumpManifest,
)
from mvp_v2.application.dump_lifecycle.publication import SnapshotPublicationService
from mvp_v2.application.graph_snapshot_operations.deletion import apply_deletion
from mvp_v2.application.graph_snapshot_operations.ingestion import IngestionService
from mvp_v2.application.ingestion_contracts import (
    IngestionPreview,
    IngestionRequest,
    SourceKind,
)
from mvp_v2.application.json_encoding import canonical_json_bytes
from mvp_v2.bootstrap.ui import UiRuntimeConfig
from mvp_v2.domain.schema_registry import SCHEMA_PROFILE_SHA256
from mvp_v2.domain.snapshot import GraphSnapshot
from mvp_v2.ports.ladybug import ReplacementBuildError
from mvp_v2.ports.media import MediaDeletionError

_SAFE_NAME = re.compile(r"(?![.-])(?!.*\.\.)[^/\\\x00-\x1f\x7f-\x9f]{1,128}\Z")
_MAX_UPLOAD_FILES = 32
_MAX_UPLOAD_FILE_BYTES = 20 * 1024 * 1024
_MAX_UPLOAD_TOTAL_BYTES = 100 * 1024 * 1024
_STAGING_MAX_AGE_SECONDS = 24 * 60 * 60


class UploadValue(Protocol):
    @property
    def name(self) -> str: ...
    def getvalue(self) -> bytes: ...


def primary_upload_name(kind: SourceKind, uploads: tuple[UploadValue, ...]) -> str:
    if kind == "url":
        raise ValueError("URL input does not use local uploads")
    if kind == "image_collection":
        return "."
    expected_suffixes = {
        "markdown": {".md"},
        "pdf": {".pdf"},
        "html": {".html", ".htm"},
    }[kind]
    candidates = tuple(
        upload.name
        for upload in uploads
        if Path(upload.name).suffix.lower() in expected_suffixes
    )
    if len(candidates) != 1:
        raise ValueError(f"{kind} upload requires exactly one primary file")
    return candidates[0]


def stage_uploads(working_root: Path, uploads: tuple[UploadValue, ...]) -> Path:
    if not working_root.is_dir() or working_root.is_symlink():
        raise ValueError("working data root must be an existing real directory")
    _cleanup_stale_staging(working_root)
    if not uploads or len(uploads) > _MAX_UPLOAD_FILES:
        raise ValueError("upload count exceeds configured limit")
    names = tuple(upload.name for upload in uploads)
    if len(names) != len(set(names)) or any(
        not _SAFE_NAME.fullmatch(name) or Path(name).name != name for name in names
    ):
        raise ValueError("upload names must be unique safe basenames")
    session_parent = working_root / "session-inputs"
    if session_parent.is_symlink():
        raise ValueError("session input root must not be a symlink")
    session_parent.mkdir(parents=True, exist_ok=True)
    session_root = session_parent / uuid.uuid4().hex
    session_root.mkdir(exist_ok=False)
    total = 0
    try:
        for upload in uploads:
            payload = upload.getvalue()
            if len(payload) > _MAX_UPLOAD_FILE_BYTES:
                raise ValueError("upload file exceeds configured limit")
            total += len(payload)
            if total > _MAX_UPLOAD_TOTAL_BYTES:
                raise ValueError("uploads exceed aggregate configured limit")
            target = session_root / upload.name
            with target.open("xb") as stream:
                written = stream.write(payload)
                if written != len(payload):
                    raise OSError("upload write was incomplete")
    except (OSError, ValueError):
        shutil.rmtree(session_root, ignore_errors=True)
        raise
    return session_root


def remove_staging_directory(path: Path, working_root: Path) -> None:
    parent = working_root / "session-inputs"
    if path.parent != parent or path.is_symlink():
        return
    shutil.rmtree(path, ignore_errors=True)


def _cleanup_stale_staging(working_root: Path) -> None:
    parent = working_root / "session-inputs"
    if not parent.is_dir() or parent.is_symlink():
        return
    cutoff = time.time() - _STAGING_MAX_AGE_SECONDS
    try:
        candidates = tuple(parent.iterdir())
    except OSError:
        return
    for candidate in candidates:
        try:
            unsafe = candidate.is_symlink() or not candidate.is_dir()
            unsafe = unsafe or any(entry.is_symlink() for entry in candidate.rglob("*"))
            if not unsafe and candidate.stat().st_mtime < cutoff:
                shutil.rmtree(candidate)
        except OSError:
            continue


def preview_ingestion(
    snapshot: GraphSnapshot,
    request: IngestionRequest,
    *,
    working_root: Path,
    input_root: Path,
) -> IngestionPreview:
    service = IngestionService(
        LocalInputExtractor(input_root, LocalHttpUrlFetcher()),
        ConfinedRuntimeFileStore(working_root),
    )
    return service.preview(snapshot, request)


def commit_ingestion(
    snapshot: GraphSnapshot,
    preview: IngestionPreview,
    *,
    working_root: Path,
) -> GraphSnapshot:
    service = IngestionService(
        LocalInputExtractor(working_root), ConfinedRuntimeFileStore(working_root)
    )
    return service.commit(snapshot, preview, created_at=datetime.now(UTC)).snapshot


def configure_ladybug_home(config: UiRuntimeConfig) -> None:
    """Bind the validated HOME before any lazy Ladybug import or native open."""
    home = config.ladybug_home
    if not home.is_dir() or home.is_symlink():
        raise ValueError("Ladybug HOME must be an existing real directory")
    os.environ["HOME"] = str(home.resolve(strict=True))


def parse_runtime_config(values: dict[str, str]) -> UiRuntimeConfig:
    """Validate user-entered paths without any fallback."""
    try:
        return UiRuntimeConfig(**{key: Path(value) for key, value in values.items()})
    except (ValueError, ValidationError) as error:
        raise ValueError(
            "Runtime configuration is invalid; all operations are blocked."
        ) from error


def scan_database_manifests(root: Path) -> tuple[tuple[Path, DatabaseManifest], ...]:
    """Validate artifact manifests without importing or opening Ladybug."""
    if not root.is_dir() or root.is_symlink():
        return ()
    found: list[tuple[Path, DatabaseManifest]] = []
    for artifact in sorted(root.iterdir()):
        if artifact.is_symlink() or not artifact.is_dir() or artifact.suffix != ".lbdb":
            continue
        manifest_path = artifact / "_mvp_v2_manifest.json"
        try:
            raw = manifest_path.read_bytes()
            manifest = DatabaseManifest.model_validate_json(raw)
            if manifest.format != DATABASE_FORMAT:
                continue
            if manifest.schema_profile_sha256 != SCHEMA_PROFILE_SHA256:
                continue
            payload = manifest.canonical_payload()
            if canonical_json_bytes(payload) != raw:
                continue
            if not (artifact / "database.lbug").is_file():
                continue
        except (OSError, ValueError, ValidationError):
            continue
        found.append((artifact, manifest))
    return tuple(found)


def database_is_stale(manifest: DatabaseManifest, dump: DumpManifest | None) -> bool:
    if dump is None:
        return True
    return (
        manifest.schema_profile_sha256 != SCHEMA_PROFILE_SHA256
        or dump.schema_profile_sha256 != SCHEMA_PROFILE_SHA256
        or manifest.source_dump_run_id != dump.run_id
        or manifest.source_dump_number != dump.dump_number
        or manifest.source_dump_published_at != dump.published_at
    )


def bind_snapshot(
    state: Any,
    snapshot: GraphSnapshot,
    dump: DumpManifest,
    dump_path: Path,
) -> None:
    state["snapshot"] = snapshot
    state["snapshot_revision"] = int(state.get("snapshot_revision", 0)) + 1
    state["dump_manifest"] = dump
    state["selected_dump"] = dump_path
    state["snapshot_dump_run_id"] = dump.run_id
    state["snapshot_dirty"] = False
    state.pop("search_evidence", None)


def mark_snapshot_dirty(state: Any, snapshot: GraphSnapshot) -> None:
    state["snapshot"] = snapshot
    state["snapshot_revision"] = int(state.get("snapshot_revision", 0)) + 1
    state["snapshot_dirty"] = True
    state["snapshot_dump_run_id"] = None
    state["active_db"] = None
    state.pop("search_evidence", None)


def active_database(
    state: Any, config: UiRuntimeConfig | None
) -> tuple[Path, DatabaseManifest] | None:
    if config is None or state.get("snapshot_dirty") is not False:
        return None
    dump = state.get("dump_manifest")
    if not isinstance(dump, DumpManifest):
        return None
    if state.get("snapshot_dump_run_id") != dump.run_id:
        return None
    value = state.get("active_db")
    if not isinstance(value, tuple) or len(value) != 2:
        return None
    path, manifest = value
    if not isinstance(path, Path) or not isinstance(manifest, DatabaseManifest):
        return None
    scanned = dict(scan_database_manifests(config.databases_root))
    if scanned.get(path) != manifest or database_is_stale(manifest, dump):
        return None
    return path, manifest


@dataclass(frozen=True, slots=True)
class DeletionExecution:
    snapshot: GraphSnapshot
    dump: DumpManifest
    database_path: Path | None
    database: DatabaseManifest | None
    result: DeletionResult


class _DeletionPublisher:
    def __init__(self, config: UiRuntimeConfig) -> None:
        self._config = config
        self.manifest: DumpManifest | None = None

    def publish(self, *, snapshot: GraphSnapshot) -> DumpManifest:
        self.manifest = SnapshotPublicationService(FilesystemDumpRepository()).publish(
            snapshot=snapshot,
            dumps_root=self._config.dumps_root,
            published_at=datetime.now(UTC),
            media_resolver=FilesystemMediaResolver(self._config.working_root),
        )
        return self.manifest


class _DeletionBuilder:
    def __init__(self, config: UiRuntimeConfig, current: DatabaseManifest) -> None:
        self._config = config
        self._current = current
        self.path: Path | None = None
        self.manifest: DatabaseManifest | None = None

    def build(self, *, dump: DumpManifest, target: Path) -> DatabaseManifest:
        self.path = target
        dump_path = self._config.dumps_root / f"dump-{dump.dump_number:06d}"
        try:
            self.manifest = build_database(
                dump_path=dump_path,
                target=target,
                database_name=self._current.database_name,
                space=self._current.embedding_space,
                vision_provider=self._current.vision_provider,
                vision_model=self._current.vision_model,
                vision_model_version=self._current.vision_model_version,
            )
        except (ValueError, OSError, RuntimeError) as error:
            raise ReplacementBuildError("replacement database build failed") from error
        return self.manifest


class _DeletionCatalog:
    def __init__(self, current: DatabaseManifest, path: Path) -> None:
        self._identity = DatabaseIdentity(
            database_name=current.database_name, build_id=current.build_id
        )
        self.path = path

    def active(self) -> DatabaseIdentity:
        return self._identity

    def identities(self) -> tuple[DatabaseIdentity, ...]:
        return (self._identity,)

    def switch(self, identity: DatabaseIdentity, path: Path) -> None:
        self._identity = identity
        self.path = path

    def mark_stale_before_dump(self, dump_number: int) -> None:
        del dump_number


def confirm_file_deletion(
    *,
    snapshot: GraphSnapshot,
    command: DeletionCommand,
    confirmation: DeletionImpact,
    config: UiRuntimeConfig,
) -> FileDeletionOutcome:
    publisher = _DeletionPublisher(config)
    service = FileFirstDeletionService(
        publisher, FilesystemMediaDeleter(config.working_root)
    )
    try:
        return service.confirm(snapshot, command, confirmation)
    except MediaDeletionError as error:
        raise RuntimeError("physical media deletion failed safely") from error


def confirm_deletion(
    *,
    snapshot: GraphSnapshot,
    command: DeletionCommand,
    confirmation: DeletionImpact,
    config: UiRuntimeConfig,
    active: tuple[Path, DatabaseManifest],
) -> DeletionExecution:
    current_path, current_manifest = active
    publisher = _DeletionPublisher(config)
    builder = _DeletionBuilder(config, current_manifest)
    catalog = _DeletionCatalog(current_manifest, current_path)
    service = DeletionService(publisher, builder, catalog, config.databases_root)
    result = service.confirm(snapshot, command, confirmation)
    if publisher.manifest is None:
        raise RuntimeError("deletion publication did not produce a manifest")
    updated = apply_deletion(snapshot, command)
    return DeletionExecution(
        snapshot=updated,
        dump=publisher.manifest,
        database_path=builder.path if result.status == "completed" else None,
        database=builder.manifest if result.status == "completed" else None,
        result=result,
    )
