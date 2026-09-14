from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from pathlib import Path, PurePosixPath

from mvp_v2.application.dto.importing import MediaCopyPlan
from mvp_v2.ports.media import MediaResolver, MediaVerification, MediaVerificationError


class FilesystemMediaResolver(MediaResolver):
    def __init__(self, authority_root: Path) -> None:
        self._root = authority_root

    def verify(self, relative_path: str, expected_sha256: str) -> MediaVerification:
        try:
            path = _confined_path(self._root, relative_path)
            if not self._root.is_dir() or self._root.is_symlink():
                raise MediaVerificationError("media authority is unavailable")
            _reject_symlink_components(self._root, path)
            if not path.exists():
                return MediaVerification(
                    status="missing", detail="media file is missing"
                )
            if path.is_symlink() or not path.is_file():
                return MediaVerification(
                    status="unreadable", detail="media path is not a regular file"
                )
            digest = _hash(path)[1]
        except MediaVerificationError:
            raise
        except OSError as error:
            raise MediaVerificationError(
                "media verification could not access the file"
            ) from error
        if digest != expected_sha256:
            return MediaVerification(
                status="hash_mismatch", detail="media SHA-256 does not match"
            )
        return MediaVerification(status="verified")


class FilesystemMediaAuthority:
    """Copy a pre-hashed plan into a new authority by atomic directory rename."""

    def copy(
        self, *, source_root: Path, destination: Path, plan: MediaCopyPlan
    ) -> FilesystemMediaResolver:
        if destination.exists() or destination.is_symlink():
            raise MediaVerificationError("destination authority already exists")
        parent = destination.parent
        try:
            parent.mkdir(parents=True, exist_ok=True)
            if parent.is_symlink() or not parent.is_dir():
                raise MediaVerificationError("destination parent is unavailable")
            stage = Path(
                tempfile.mkdtemp(prefix=f".{destination.name}-copy-", dir=parent)
            )
        except OSError as error:
            raise MediaVerificationError(
                "media staging could not be created"
            ) from error
        published = False
        try:
            for entry in plan.entries:
                source = _confined_path(source_root, entry.source_path)
                target = _confined_path(stage, entry.target_path)
                _reject_symlink_components(source_root, source)
                if source.is_symlink() or not source.is_file():
                    raise MediaVerificationError("planned source is not a regular file")
                source_size, source_hash = _hash(source)
                if source_size != entry.size or source_hash != entry.sha256:
                    raise MediaVerificationError("planned source changed after preview")
                target.parent.mkdir(parents=True, exist_ok=True)
                _stream_copy(source, target)
                target_size, target_hash = _hash(target)
                if target_size != entry.size or target_hash != entry.sha256:
                    raise MediaVerificationError("copied media verification failed")
            try:
                stage.rename(destination)
            except OSError as error:
                raise MediaVerificationError(
                    "media authority could not be finalized"
                ) from error
            published = True
            return FilesystemMediaResolver(destination)
        except OSError as error:
            raise MediaVerificationError("media copy operation failed") from error
        finally:
            if not published:
                shutil.rmtree(stage, ignore_errors=True)


def _confined_path(root: Path, relative: str) -> Path:
    if "\\" in relative:
        raise MediaVerificationError("media path is not lexical and relative")
    pure = PurePosixPath(relative)
    if (
        pure.is_absolute()
        or not relative
        or any(part in {"", ".", ".."} for part in relative.split("/"))
    ):
        raise MediaVerificationError("media path is not lexical and relative")
    return root.joinpath(*pure.parts)


def _reject_symlink_components(root: Path, path: Path) -> None:
    current = root
    if current.is_symlink():
        raise MediaVerificationError("media path contains a symlink")
    for part in path.relative_to(root).parts:
        current = current / part
        if current.is_symlink():
            raise MediaVerificationError("media path contains a symlink")


def _hash(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
            size += len(block)
    return size, digest.hexdigest()


def _stream_copy(source: Path, target: Path) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    descriptor = os.open(target, flags, 0o644)
    try:
        with source.open("rb") as incoming, os.fdopen(descriptor, "wb") as outgoing:
            descriptor = -1
            shutil.copyfileobj(incoming, outgoing, length=1024 * 1024)
            outgoing.flush()
            os.fsync(outgoing.fileno())
    finally:
        if descriptor >= 0:
            os.close(descriptor)
