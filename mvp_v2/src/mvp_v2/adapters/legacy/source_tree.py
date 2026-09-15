from __future__ import annotations

import hashlib
import os
from collections import Counter, defaultdict
from pathlib import Path
from typing import Literal

from mvp_v2.application.dto.importing import ImportIssue, canonical_json_bytes
from mvp_v2.domain import graph
from mvp_v2.domain.snapshot import GraphSnapshot


def scan_source_tree(
    root: Path, snapshot: GraphSnapshot, issues: list[ImportIssue]
) -> tuple[int, int, str]:
    entries: list[tuple[str, int, str]] = []
    for directory in ("documents", "images", "uploads"):
        top = root / directory
        try:
            if top.is_symlink() or not top.is_dir():
                issues.append(
                    _issue(
                        "error",
                        "MEDIA_DIRECTORY_INVALID",
                        "media",
                        directory,
                        None,
                        "",
                        "",
                        (
                            "required media directory is missing, unreadable, "
                            "or a symlink"
                        ),
                    )
                )
                continue
            for current, directory_names, file_names in os.walk(top, followlinks=False):
                current_path = Path(current)
                for name in tuple(directory_names):
                    child = current_path / name
                    if child.is_symlink():
                        directory_names.remove(name)
                        issues.append(
                            _issue(
                                "error",
                                "MEDIA_SYMLINK",
                                "media",
                                directory,
                                None,
                                "",
                                _safe_id(name),
                                "symlink is not allowed in media source",
                            )
                        )
                for name in file_names:
                    path = current_path / name
                    relative = path.relative_to(root).as_posix()
                    if path.is_symlink() or not path.is_file():
                        issues.append(
                            _issue(
                                "error",
                                "MEDIA_NONREGULAR",
                                "media",
                                directory,
                                None,
                                "",
                                _safe_id(relative),
                                "media entry is not a regular file",
                            )
                        )
                        continue
                    try:
                        size, digest = _hash_file(path)
                        entries.append((relative, size, digest))
                    except OSError:
                        issues.append(
                            _issue(
                                "error",
                                "MEDIA_READ_FAILED",
                                "media",
                                directory,
                                None,
                                "",
                                _safe_id(relative),
                                "media file could not be read",
                            )
                        )
        except OSError:
            issues.append(
                _issue(
                    "error",
                    "MEDIA_SCAN_FAILED",
                    "media",
                    directory,
                    None,
                    "",
                    "",
                    "media directory could not be scanned",
                )
            )
    entries.sort(key=lambda item: item[0])
    by_path = {item[0]: item for item in entries}
    referenced = {
        (item.local_path, item.sha256, item.id, "DocumentVersion")
        for item in snapshot.document_versions
        if item.local_path
    }
    referenced.update(
        (item.local_path, item.sha256, item.id, "Asset") for item in snapshot.assets
    )
    ref_paths: set[str] = set()
    for relative_path, digest, identifier, table in sorted(referenced):
        ref_paths.add(relative_path)
        referenced_entry = by_path.get(relative_path)
        if referenced_entry is None:
            issues.append(
                _issue(
                    "error",
                    "REFERENCED_MEDIA_MISSING",
                    "media",
                    table,
                    None,
                    "local_path",
                    _safe_id(identifier),
                    "referenced media file is missing from source tree",
                )
            )
        elif referenced_entry[2] != digest:
            issues.append(
                _issue(
                    "error",
                    "REFERENCED_MEDIA_HASH_MISMATCH",
                    "media",
                    table,
                    None,
                    "sha256",
                    _safe_id(identifier),
                    "referenced media SHA-256 does not match source bytes",
                )
            )
    versions_by_document: dict[str, list[graph.DocumentVersion]] = defaultdict(list)
    version_by_id = {item.id: item for item in snapshot.document_versions}
    for ownership in snapshot.has_versions:
        version = version_by_id.get(ownership.target_id)
        if version is not None:
            versions_by_document[ownership.source_id].append(version)
    upload_prefix = "file://mvp/mvp_data/uploads/"
    for document in snapshot.documents:
        if document.source_url.startswith(upload_prefix):
            upload_path = "uploads/" + document.source_url[len(upload_prefix) :]
            upload_entry = by_path.get(upload_path)
            if upload_entry is not None:
                ref_paths.add(upload_path)
                current_versions = [
                    item
                    for item in versions_by_document.get(document.id, [])
                    if item.is_current
                ]
                if (
                    len(current_versions) == 1
                    and upload_entry[2] != current_versions[0].sha256
                ):
                    issues.append(
                        _issue(
                            "info",
                            "RAW_UPLOAD_HASH_DIFFERS_FROM_DERIVED_VERSION",
                            "media",
                            "uploads",
                            None,
                            "sha256",
                            document.id,
                            "raw upload differs from normalized derived version",
                        )
                    )
        elif document.source_url.startswith("file://"):
            issues.append(
                _issue(
                    "warning",
                    "EXTERNAL_SOURCE_NOT_VERIFIED",
                    "provenance",
                    "Document",
                    None,
                    "source_url",
                    document.id,
                    "file provenance is outside authorized source directories",
                )
            )
    hash_counts = Counter(item[2] for item in entries)
    for relative_path in sorted(set(by_path) - ref_paths):
        entry = by_path[relative_path]
        duplicate = hash_counts[entry[2]] > 1
        issues.append(
            _issue(
                "info",
                "UNREFERENCED_FILE_DUPLICATE_CONTENT"
                if duplicate
                else "UNREFERENCED_FILE_UNIQUE_CONTENT",
                "media",
                relative_path.split("/", 1)[0],
                None,
                "",
                _safe_id(relative_path),
                f"unreferenced source file; size={entry[1]}; sha256={entry[2]}",
            )
        )
    tree_facts = [
        {"path": path, "size": size, "sha256": digest} for path, size, digest in entries
    ]
    tree_digest = hashlib.sha256(canonical_json_bytes(tree_facts)).hexdigest()
    return len(entries), sum(item[1] for item in entries), tree_digest


def _hash_file(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
            size += len(block)
    return size, digest.hexdigest()


def _safe_id(value: str) -> str:
    if (
        len(value) <= 160
        and value
        and all(ord(character) >= 32 and character not in "\r\n" for character in value)
    ):
        return value
    return (
        "sha256:"
        + hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()[:16]
    )


def _issue(
    severity: Literal["error", "warning", "info"],
    code: str,
    category: str,
    table: str,
    row: int | None,
    column: str,
    record_id: str,
    message: str,
) -> ImportIssue:
    return ImportIssue(
        severity=severity,
        code=code,
        category=category,
        source_table=table,
        row=row,
        column=column,
        record_id=record_id,
        message=message,
    )
