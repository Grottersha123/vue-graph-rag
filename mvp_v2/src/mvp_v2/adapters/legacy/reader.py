from __future__ import annotations

import csv
import hashlib
import io
from dataclasses import dataclass
from pathlib import Path

from mvp_v2.application.dto.importing import ImportIssue, SourceCsvFact

EXPECTED_HEADERS: dict[str, tuple[str, ...]] = {
    "SourceType": ("id", "code"),
    "Document": (
        "id",
        "title",
        "source_url",
        "external_id",
        "source_type",
        "sha256",
        "local_path",
        "content_format",
        "source_format",
        "source_name",
        "is_active",
        "status",
    ),
    "DocumentVersion": (
        "id",
        "version",
        "sha256",
        "local_path",
        "created_at",
        "is_current",
        "error",
    ),
    "Asset": (
        "id",
        "asset_type",
        "mime_type",
        "source_url",
        "local_path",
        "external_id",
        "sha256",
        "position",
        "caption",
        "ocr_text",
    ),
    "Role": ("id", "name"),
    "Status": ("id", "name", "description"),
    "HAS_VERSION": ("from", "to"),
    "HAS_ASSET": ("from", "to"),
    "HAS_STATUS": ("from", "to"),
    "FOR_ROLE": ("from", "to"),
    "LINKS_TO": ("from", "to"),
    "HAS_DOCUMENT": ("from", "to"),
    "HAS_ERROR": ("from", "to"),
    "manual_links": (
        "source_document_id",
        "target_document_id",
        "target_external_id",
        "source_url",
        "target_url",
        "link_type",
        "discovered_depth",
        "reason",
    ),
    "image_manifest": (
        "asset_id",
        "source_document_id",
        "source_url",
        "local_path",
        "mime_type",
        "sha256",
        "status",
        "reason",
    ),
}


@dataclass(frozen=True, slots=True)
class LegacyTable:
    name: str
    rows: tuple[tuple[int, dict[str, str]], ...]
    fact: SourceCsvFact | None
    issues: tuple[ImportIssue, ...]


def read_table(root: Path, name: str) -> LegacyTable:
    path = root / f"{name}.csv"
    try:
        if path.is_symlink() or not path.is_file():
            return LegacyTable(
                name,
                (),
                None,
                (
                    _issue(
                        name,
                        "MISSING_CSV",
                        "source",
                        "required CSV is missing or not a regular file",
                    ),
                ),
            )
        raw = path.read_bytes()
    except OSError:
        return LegacyTable(
            name,
            (),
            None,
            (
                _issue(
                    name, "CSV_READ_FAILED", "source", "required CSV could not be read"
                ),
            ),
        )
    fact = SourceCsvFact(
        table=name, sha256=hashlib.sha256(raw).hexdigest(), data_rows=0
    )
    try:
        text = raw.decode("utf-8", errors="strict")
        parsed = list(csv.reader(io.StringIO(text, newline=""), strict=True))
    except (UnicodeDecodeError, csv.Error):
        return LegacyTable(
            name,
            (),
            fact,
            (
                _issue(
                    name,
                    "MALFORMED_CSV",
                    "parse",
                    "CSV is not valid UTF-8 or RFC 4180 text",
                ),
            ),
        )
    fact = SourceCsvFact(
        table=name, sha256=fact.sha256, data_rows=max(0, len(parsed) - 1)
    )
    if not parsed:
        return LegacyTable(
            name,
            (),
            fact,
            (_issue(name, "MISSING_HEADER", "header", "CSV header is missing"),),
        )
    header = tuple(parsed[0])
    issues: list[ImportIssue] = []
    if len(header) != len(set(header)):
        issues.append(
            _issue(
                name, "DUPLICATE_HEADER", "header", "CSV contains a duplicate header"
            )
        )
    expected = EXPECTED_HEADERS[name]
    if header != expected:
        missing = tuple(value for value in expected if value not in header)
        extra = tuple(value for value in header if value not in expected)
        message = (
            "header does not match contract; "
            f"missing_count={len(missing)}; extra_count={len(extra)}"
        )
        issues.append(_issue(name, "INVALID_HEADER", "header", message))
        return LegacyTable(name, (), fact, tuple(issues))
    rows: list[tuple[int, dict[str, str]]] = []
    for number, values in enumerate(parsed[1:], start=2):
        if len(values) != len(header):
            issues.append(
                ImportIssue(
                    severity="error",
                    code="MALFORMED_ROW",
                    category="parse",
                    source_table=name,
                    row=number,
                    message=f"row has {len(values)} columns; expected {len(header)}",
                )
            )
            continue
        rows.append((number, dict(zip(header, values, strict=True))))
    return LegacyTable(name, tuple(rows), fact, tuple(issues))


def _issue(table: str, code: str, category: str, message: str) -> ImportIssue:
    return ImportIssue(
        severity="error",
        code=code,
        category=category,
        source_table=table,
        message=message,
    )
