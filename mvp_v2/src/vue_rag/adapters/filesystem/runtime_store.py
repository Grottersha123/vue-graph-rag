from __future__ import annotations

import fcntl
import hashlib
import os
import stat
import sys
import tempfile
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import ClassVar


class FileStoreError(RuntimeError):
    pass


class ConfinedRuntimeFileStore:
    _process_lock: ClassVar[threading.Lock] = threading.Lock()

    def __init__(self, root: Path) -> None:
        if not root.exists() or not root.is_dir() or root.is_symlink():
            raise FileStoreError("runtime data root must be an existing real directory")
        self._root = root.resolve()

    def write_files(self, files: tuple[tuple[str, bytes], ...]) -> tuple[str, ...]:
        with self._exclusive_lock():
            return self._write_files_locked(files)

    def _write_files_locked(
        self, files: tuple[tuple[str, bytes], ...]
    ) -> tuple[str, ...]:
        targets: list[tuple[Path, bytes, str]] = []
        relative_paths = [relative for relative, _ in files]
        if len(relative_paths) != len(set(relative_paths)):
            raise FileStoreError("duplicate relative output path")
        for relative, content in files:
            pure = PurePosixPath(relative)
            if pure.is_absolute() or any(
                part in {"", ".", ".."} for part in pure.parts
            ):
                raise FileStoreError("unsafe relative output path")
            target = self._root.joinpath(*pure.parts)
            self._check_parents(target)
            if target.exists() or target.is_symlink():
                if not self._existing_file_matches(target, content):
                    raise FileStoreError("refusing to replace an existing runtime file")
                continue
            targets.append((target, content, relative))
        staged: list[tuple[Path, Path]] = []
        finalized: list[Path] = []
        try:
            for target, content, _ in targets:
                target.parent.mkdir(parents=True, exist_ok=True)
                self._check_parents(target)
                descriptor, temporary_name = tempfile.mkstemp(
                    prefix=".ingest-", dir=target.parent
                )
                temp_path = Path(temporary_name)
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(content)
                    stream.flush()
                    os.fsync(stream.fileno())
                staged.append((temp_path, target))
            for staged_path, target in staged:
                try:
                    os.link(staged_path, target, follow_symlinks=False)
                except FileExistsError:
                    content = staged_path.read_bytes()
                    if not self._existing_file_matches(target, content):
                        raise FileStoreError(
                            "refusing to replace an existing runtime file"
                        ) from None
                else:
                    finalized.append(target)
                staged_path.unlink()
        except (OSError, FileStoreError) as error:
            for staged_path, _ in staged:
                staged_path.unlink(missing_ok=True)
            for target in finalized:
                target.unlink(missing_ok=True)
            if isinstance(error, FileStoreError):
                raise
            raise FileStoreError("runtime file write failed") from error
        return tuple(relative_paths)

    @contextmanager
    def _exclusive_lock(self) -> Iterator[None]:
        with self._process_lock:
            lock_path = self._root / ".ingestion.lock"
            flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
            try:
                descriptor = os.open(lock_path, flags, 0o600)
            except OSError as error:
                raise FileStoreError(
                    "runtime ingestion lock could not be opened"
                ) from error
            try:
                if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                    raise FileStoreError(
                        "runtime ingestion lock must be a regular file"
                    )
                fcntl.flock(descriptor, fcntl.LOCK_EX)
                yield
            except OSError as error:
                raise FileStoreError("runtime ingestion lock failed") from error
            finally:
                primary_error_is_active = sys.exception() is not None
                try:
                    os.close(descriptor)
                except OSError as error:
                    if not primary_error_is_active:
                        raise FileStoreError(
                            "runtime ingestion lock cleanup failed"
                        ) from error

    @staticmethod
    def _existing_file_matches(path: Path, content: bytes) -> bool:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(path, flags)
            with os.fdopen(descriptor, "rb") as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    return False
                digest = hashlib.sha256()
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(block)
        except OSError as error:
            raise FileStoreError(
                "existing runtime file could not be verified"
            ) from error
        return digest.digest() == hashlib.sha256(content).digest()

    def _check_parents(self, target: Path) -> None:
        if not target.resolve(strict=False).is_relative_to(self._root):
            raise FileStoreError("output escapes runtime data root")
        current = target.parent
        while current != self._root:
            if current.exists() and current.is_symlink():
                raise FileStoreError("output parent symlinks are refused")
            current = current.parent
