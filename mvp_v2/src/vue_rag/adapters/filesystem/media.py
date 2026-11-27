from __future__ import annotations

import hashlib
from pathlib import Path, PurePosixPath

from vue_rag.ports.media import (
    MediaDeleter,
    MediaDeletionError,
    MediaDeletionReport,
    MediaDeletionTarget,
    MediaResolver,
    MediaVerification,
    MediaVerificationError,
)


class FilesystemMediaResolver(MediaResolver):
    def __init__(self, runtime_data_root: Path) -> None:
        self._root = runtime_data_root

    def verify(self, relative_path: str, expected_sha256: str) -> MediaVerification:
        try:
            path = _confined_path(self._root, relative_path)
            if not self._root.is_dir() or self._root.is_symlink():
                raise MediaVerificationError("runtime data root is unavailable")
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

    def read_verified(self, relative_path: str, expected_sha256: str) -> bytes:
        verification = self.verify(relative_path, expected_sha256)
        if verification.status != "verified":
            raise MediaVerificationError("media integrity verification failed")
        path = _confined_path(self._root, relative_path)
        try:
            content = path.read_bytes()
        except OSError as error:
            raise MediaVerificationError("media could not be read") from error
        if hashlib.sha256(content).hexdigest() != expected_sha256:
            raise MediaVerificationError("media changed during verification")
        return content


class FilesystemMediaDeleter(MediaDeleter):
    def __init__(self, runtime_data_root: Path) -> None:
        self._root = runtime_data_root

    def validate(self, targets: tuple[MediaDeletionTarget, ...]) -> None:
        for target in targets:
            _deletable_path(self._root, target)

    def delete(self, targets: tuple[MediaDeletionTarget, ...]) -> MediaDeletionReport:
        deleted: list[str] = []
        missing: list[str] = []
        failed: list[str] = []
        for target in targets:
            try:
                path = _deletable_path(self._root, target)
                if path.exists():
                    path.unlink()
                    deleted.append(target.relative_path)
                else:
                    missing.append(target.relative_path)
            except (MediaDeletionError, OSError):
                failed.append(target.relative_path)
        return MediaDeletionReport(
            deleted_paths=tuple(deleted),
            missing_paths=tuple(missing),
            failed_paths=tuple(failed),
        )


def _deletable_path(root: Path, target: MediaDeletionTarget) -> Path:
    try:
        path = _confined_path(root, target.relative_path)
        if not root.is_dir() or root.is_symlink():
            raise MediaDeletionError("runtime data root is unavailable")
        _reject_symlink_components(root, path)
        if path.exists() and not path.is_file():
            raise MediaDeletionError("media path is not a regular file")
        if path.exists() and _hash(path)[1] != target.expected_sha256:
            raise MediaDeletionError("media SHA-256 changed before deletion")
    except MediaVerificationError as error:
        raise MediaDeletionError("media path is not safely confined") from error
    except OSError as error:
        raise MediaDeletionError("media deletion could not access the file") from error
    return path


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
