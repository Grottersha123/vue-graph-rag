from __future__ import annotations

import os
import tempfile
from pathlib import Path, PurePosixPath


class FileStoreError(RuntimeError):
    pass


class ConfinedRuntimeFileStore:
    def __init__(self, root: Path) -> None:
        if not root.exists() or not root.is_dir() or root.is_symlink():
            raise FileStoreError("runtime data root must be an existing real directory")
        self._root = root.resolve()

    def write_files(self, files: tuple[tuple[str, bytes], ...]) -> tuple[str, ...]:
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
                raise FileStoreError("refusing to replace an existing runtime file")
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
                staged_path.replace(target)
                finalized.append(target)
        except (OSError, FileStoreError) as error:
            for staged_path, _ in staged:
                staged_path.unlink(missing_ok=True)
            for target in finalized:
                target.unlink(missing_ok=True)
            if isinstance(error, FileStoreError):
                raise
            raise FileStoreError("runtime file write failed") from error
        return tuple(item[2] for item in targets)

    def _check_parents(self, target: Path) -> None:
        if not target.resolve(strict=False).is_relative_to(self._root):
            raise FileStoreError("output escapes runtime data root")
        current = target.parent
        while current != self._root:
            if current.exists() and current.is_symlink():
                raise FileStoreError("output parent symlinks are refused")
            current = current.parent
