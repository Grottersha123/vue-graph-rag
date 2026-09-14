"""Canonical CSV staging.

Path checks reject unsafe snapshots but cannot prevent TOCTOU races. A future
filesystem staging/publication adapter owns race resistance.
"""

from __future__ import annotations

import csv
import hashlib
import io
import shutil
import tempfile
from pathlib import Path
from typing import TypeAlias

from pydantic import BaseModel, ValidationError

from mvp_v2.adapters.csv.scalars import CsvScalarError, decode_scalar, encode_scalar
from mvp_v2.application.manifests import DumpFileEntry
from mvp_v2.domain.schema_registry import TABLE_SPECS, NodeSpec, RelationSpec
from mvp_v2.domain.snapshot import GraphSnapshot

TableSpec: TypeAlias = NodeSpec | RelationSpec


class CsvSnapshotError(ValueError):
    """A graph snapshot CSV table violates its registry contract."""


def write_snapshot(root: Path, snapshot: GraphSnapshot) -> tuple[DumpFileEntry, ...]:
    """Stage all tables before exposing them; a future publisher owns TOCTOU safety."""
    root_directory = _prepare_write_root(root)
    final_directory = root_directory / "tables"
    temporary_directory = Path(tempfile.mkdtemp(prefix=".tables-", dir=root_directory))
    published = False
    try:
        entries = _write_temporary_tables(temporary_directory, snapshot)
        try:
            temporary_directory.rename(final_directory)
        except OSError as error:
            raise CsvSnapshotError(
                f"cannot finalize tables directory: {error}"
            ) from error
        published = True
        return entries
    finally:
        if not published:
            shutil.rmtree(temporary_directory, ignore_errors=True)


def _write_temporary_tables(
    temporary_directory: Path, snapshot: GraphSnapshot
) -> tuple[DumpFileEntry, ...]:
    entries: list[DumpFileEntry] = []
    for table_spec in TABLE_SPECS:
        models = snapshot.__getattribute__(table_spec.snapshot_field)
        encoded = _serialize_table_bytes(table_spec, models)
        table_path = temporary_directory / f"{table_spec.table_name}.csv"
        try:
            table_path.write_bytes(encoded)
        except OSError as error:
            raise CsvSnapshotError(
                f"cannot write {table_spec.table_name} table: {error}"
            ) from error
        entries.append(
            DumpFileEntry(
                path=f"tables/{table_spec.table_name}.csv",
                sha256=hashlib.sha256(encoded).hexdigest(),
                row_count=len(models),
            )
        )
    return tuple(entries)


def read_snapshot(root: Path) -> GraphSnapshot:
    """Parse canonical tables; a future publisher owns TOCTOU safety."""
    table_directory = _validate_read_directory(root)
    snapshot_values: dict[str, object] = {}
    for table_spec in TABLE_SPECS:
        table_path = table_directory / f"{table_spec.table_name}.csv"
        snapshot_values[table_spec.snapshot_field] = _read_table(table_path, table_spec)
    return GraphSnapshot.model_validate(snapshot_values)


def _prepare_write_root(root: Path) -> Path:
    if root.is_symlink():
        raise CsvSnapshotError("snapshot root must not be a symlink")
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise CsvSnapshotError(f"cannot prepare snapshot root: {error}") from error
    table_directory = root / "tables"
    if table_directory.is_symlink():
        raise CsvSnapshotError("tables directory must not be a symlink")
    if table_directory.exists():
        raise CsvSnapshotError("tables directory already exists")
    return root


def _validate_read_directory(root: Path) -> Path:
    if root.is_symlink():
        raise CsvSnapshotError("snapshot root must not be a symlink")
    table_directory = root / "tables"
    if table_directory.is_symlink() or not table_directory.is_dir():
        raise CsvSnapshotError("tables must be a regular directory, not a symlink")
    expected = {f"{table_spec.table_name}.csv" for table_spec in TABLE_SPECS}
    entries = tuple(table_directory.iterdir())
    actual = {entry.name for entry in entries}
    if actual != expected:
        raise CsvSnapshotError(
            "tables directory must contain exactly 21 registry files"
        )
    for entry in entries:
        if entry.is_symlink() or not entry.is_file():
            raise CsvSnapshotError(f"table entry {entry.name} must be a regular file")
    return table_directory


def _serialize_table_bytes(
    table_spec: TableSpec, models: tuple[BaseModel, ...]
) -> bytes:
    serialized_rows: list[tuple[str, ...]] = []
    for row_number, model in enumerate(models, start=2):
        serialized_rows.append(_serialize_model(model, table_spec, row_number))
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, dialect="excel", lineterminator="\r\n")
    writer.writerow(table_spec.headers)
    writer.writerows(sorted(serialized_rows))
    return stream.getvalue().encode("utf-8")


def _serialize_model(
    model: BaseModel, table_spec: TableSpec, row_number: int
) -> tuple[str, ...]:
    values = model.model_dump(by_alias=True)
    record_id = _record_context(values)
    encoded: list[str] = []
    for property_spec in table_spec.properties:
        try:
            value = values[property_spec.name]
            encoded.append(encode_scalar(value, property_spec.scalar_type))
        except (KeyError, CsvScalarError) as error:
            raise CsvSnapshotError(
                f"{table_spec.table_name} row {row_number} record {record_id} "
                f"column {property_spec.name}: {error}"
            ) from error
    return tuple(encoded)


def _record_context(values: dict[str, object]) -> str:
    identifier = values.get("id")
    if isinstance(identifier, str):
        return identifier
    return f"{values.get('from', '?')}->{values.get('to', '?')}"


def _read_table(table_path: Path, table_spec: TableSpec) -> tuple[BaseModel, ...]:
    try:
        raw_bytes = table_path.read_bytes()
        text = raw_bytes.decode("utf-8", errors="strict")
        rows = list(
            csv.reader(io.StringIO(text, newline=""), dialect="excel", strict=True)
        )
    except (OSError, UnicodeDecodeError, csv.Error) as error:
        raise CsvSnapshotError(
            f"cannot parse {table_spec.table_name}: {error}"
        ) from error
    if not rows:
        raise CsvSnapshotError(f"{table_spec.table_name} is missing its header")
    header = tuple(rows[0])
    if len(header) != len(set(header)):
        raise CsvSnapshotError(f"{table_spec.table_name} contains duplicate headers")
    if header != table_spec.headers:
        raise CsvSnapshotError(f"{table_spec.table_name} header or order is invalid")
    models = tuple(
        _parse_row(row, row_number, table_spec)
        for row_number, row in enumerate(rows[1:], start=2)
    )
    if raw_bytes != _serialize_table_bytes(table_spec, models):
        raise CsvSnapshotError(f"{table_spec.table_name} bytes are not canonical")
    return models


def _parse_row(row: list[str], row_number: int, table_spec: TableSpec) -> BaseModel:
    if len(row) != len(table_spec.properties):
        raise CsvSnapshotError(
            f"{table_spec.table_name} row {row_number} column count is {len(row)}; "
            f"expected {len(table_spec.properties)}"
        )
    decoded: dict[str, object] = {}
    for property_spec, encoded in zip(table_spec.properties, row, strict=True):
        try:
            decoded[property_spec.name] = decode_scalar(
                encoded, property_spec.scalar_type
            )
        except CsvScalarError as error:
            raise CsvSnapshotError(
                f"{table_spec.table_name} row {row_number} "
                f"column {property_spec.name}: {error}"
            ) from error
    try:
        return table_spec.model.model_validate(decoded)
    except ValidationError as error:
        location = error.errors()[0].get("loc", ("unknown",))
        column = str(location[0]) if location else "unknown"
        raise CsvSnapshotError(
            f"{table_spec.table_name} row {row_number} column {column}: {error}"
        ) from error
