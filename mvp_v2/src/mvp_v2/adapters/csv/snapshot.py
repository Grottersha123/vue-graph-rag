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
from mvp_v2.domain import graph
from mvp_v2.domain.schema_registry import (
    LEGACY_TABLE_NAMES,
    TABLE_SPECS,
    NodeSpec,
    PropertySpec,
    RelationSpec,
    ScalarType,
)
from mvp_v2.domain.snapshot import GraphSnapshot

TableSpec: TypeAlias = NodeSpec | RelationSpec


class CsvSnapshotError(ValueError):
    """A graph snapshot CSV table violates its registry contract."""


def write_snapshot(
    root: Path,
    snapshot: GraphSnapshot,
    table_specs: tuple[TableSpec, ...] = TABLE_SPECS,
) -> tuple[DumpFileEntry, ...]:
    """Stage all tables before exposing them; a future publisher owns TOCTOU safety."""
    root_directory = _prepare_write_root(root)
    final_directory = root_directory / "tables"
    temporary_directory = Path(tempfile.mkdtemp(prefix=".tables-", dir=root_directory))
    published = False
    try:
        entries = _write_temporary_tables(temporary_directory, snapshot, table_specs)
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
    temporary_directory: Path,
    snapshot: GraphSnapshot,
    table_specs: tuple[TableSpec, ...],
) -> tuple[DumpFileEntry, ...]:
    entries: list[DumpFileEntry] = []
    for table_spec in table_specs:
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


def read_snapshot(
    root: Path, table_specs: tuple[TableSpec, ...] = TABLE_SPECS
) -> GraphSnapshot:
    """Parse canonical tables; a future publisher owns TOCTOU safety."""
    table_directory = _validate_read_directory(root, table_specs)
    snapshot_values: dict[str, object] = {}
    for table_spec in table_specs:
        table_path = table_directory / f"{table_spec.table_name}.csv"
        snapshot_values[table_spec.snapshot_field] = (
            _read_table(table_path, table_spec) if table_path.exists() else ()
        )
    snapshot = GraphSnapshot.model_validate(snapshot_values)
    if not (table_directory / "HAS_SUMMARY_EMBEDDING.csv").exists():
        _validate_legacy_embedding_purposes(table_directory, snapshot)
    return snapshot


def _validate_legacy_embedding_purposes(
    table_directory: Path, snapshot: GraphSnapshot
) -> None:
    embedding_path = table_directory / "Embedding.csv"
    try:
        with embedding_path.open(encoding="utf-8", newline="") as stream:
            rows = tuple(csv.DictReader(stream))
    except (OSError, UnicodeDecodeError, csv.Error) as error:
        raise CsvSnapshotError(
            "legacy Embedding purpose could not be checked"
        ) from error
    purposes = {row.get("id", ""): row.get("purpose", "") for row in rows}
    expected = (
        (snapshot.has_embedding_chunks, "chunk_retrieval"),
        (snapshot.has_embedding_assets, "asset_description_retrieval"),
    )
    for relations, purpose in expected:
        for relation in relations:
            if purposes.get(relation.target_id) != purpose:
                raise CsvSnapshotError(
                    "legacy Embedding purpose does not match its owner relation"
                )


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


def _validate_read_directory(root: Path, table_specs: tuple[TableSpec, ...]) -> Path:
    if root.is_symlink():
        raise CsvSnapshotError("snapshot root must not be a symlink")
    table_directory = root / "tables"
    if table_directory.is_symlink() or not table_directory.is_dir():
        raise CsvSnapshotError("tables must be a regular directory, not a symlink")
    expected = {f"{table_spec.table_name}.csv" for table_spec in table_specs}
    legacy_expected = {f"{name}.csv" for name in LEGACY_TABLE_NAMES}
    entries = tuple(table_directory.iterdir())
    actual = {entry.name for entry in entries}
    if actual not in {frozenset(expected), frozenset(legacy_expected)}:
        raise CsvSnapshotError(
            "tables directory must contain exactly one supported registry file set"
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
    custom_values = {
        item.name: item.value for item in getattr(model, "custom_properties", ())
    }
    declared_custom = {item.name for item in table_spec.properties if item.custom}
    unknown_custom = set(custom_values) - declared_custom
    if unknown_custom:
        raise CsvSnapshotError(
            f"{table_spec.table_name} row {row_number} contains undeclared "
            f"custom fields: {sorted(unknown_custom)}"
        )
    record_id = _record_context(values)
    encoded: list[str] = []
    for property_spec in table_spec.properties:
        try:
            value = (
                custom_values.get(property_spec.name, property_spec.default)
                if property_spec.custom
                else values[property_spec.name]
            )
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


_LEGACY_ASSET_PROPERTIES = (
    PropertySpec(name="id", scalar_type=ScalarType.STRING),
    PropertySpec(name="asset_type", scalar_type=ScalarType.STRING),
    PropertySpec(name="mime_type", scalar_type=ScalarType.STRING),
    PropertySpec(name="source_url", scalar_type=ScalarType.STRING),
    PropertySpec(name="local_path", scalar_type=ScalarType.STRING),
    PropertySpec(name="external_id", scalar_type=ScalarType.STRING),
    PropertySpec(name="sha256", scalar_type=ScalarType.STRING),
    PropertySpec(name="position", scalar_type=ScalarType.INT64),
    PropertySpec(name="caption", scalar_type=ScalarType.STRING),
    PropertySpec(name="llm_description", scalar_type=ScalarType.STRING),
    PropertySpec(name="ocr_text", scalar_type=ScalarType.STRING),
)
_LEGACY_EMBEDDING_PROPERTIES = (
    PropertySpec(name="id", scalar_type=ScalarType.STRING),
    PropertySpec(name="provider", scalar_type=ScalarType.STRING),
    PropertySpec(name="model", scalar_type=ScalarType.STRING),
    PropertySpec(name="model_version", scalar_type=ScalarType.STRING),
    PropertySpec(name="purpose", scalar_type=ScalarType.STRING),
    PropertySpec(name="dimension", scalar_type=ScalarType.INT64),
    PropertySpec(name="vector", scalar_type=ScalarType.FLOAT_VECTOR),
    PropertySpec(name="created_at", scalar_type=ScalarType.TIMESTAMP),
    PropertySpec(name="is_current", scalar_type=ScalarType.BOOLEAN),
)


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
    source_properties = _source_properties(header, table_spec)
    models = tuple(
        _parse_row(row, row_number, table_spec, source_properties)
        for row_number, row in enumerate(rows[1:], start=2)
    )
    if raw_bytes != _serialize_source_bytes(header, rows[1:]):
        raise CsvSnapshotError(f"{table_spec.table_name} bytes are not canonical")
    return models


def _source_properties(
    header: tuple[str, ...], table_spec: TableSpec
) -> tuple[PropertySpec, ...]:
    if table_spec.table_name == "Asset" and header == tuple(
        item.name for item in _LEGACY_ASSET_PROPERTIES
    ):
        return _LEGACY_ASSET_PROPERTIES
    if table_spec.table_name == "Embedding" and header == tuple(
        item.name for item in _LEGACY_EMBEDDING_PROPERTIES
    ):
        return _LEGACY_EMBEDDING_PROPERTIES
    source: list[PropertySpec] = []
    header_index = 0
    for property_spec in table_spec.properties:
        if header_index < len(header) and header[header_index] == property_spec.name:
            source.append(property_spec)
            header_index += 1
        elif property_spec.required_on_read:
            raise CsvSnapshotError(
                f"{table_spec.table_name} required column "
                f"{property_spec.name} is missing or reordered"
            )
    if header_index != len(header):
        raise CsvSnapshotError(f"{table_spec.table_name} header or order is invalid")
    return tuple(source)


def _serialize_source_bytes(header: tuple[str, ...], rows: list[list[str]]) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, dialect="excel", lineterminator="\r\n")
    writer.writerow(header)
    writer.writerows(sorted(tuple(row) for row in rows))
    return stream.getvalue().encode("utf-8")


def parse_table_model(
    table_spec: TableSpec,
    header: tuple[str, ...],
    row: list[str],
    row_number: int,
) -> BaseModel:
    """Parse one current or supported legacy row for repair workflows."""
    source_properties = _source_properties(header, table_spec)
    return _parse_row(row, row_number, table_spec, source_properties)


def _parse_row(
    row: list[str],
    row_number: int,
    table_spec: TableSpec,
    source_properties: tuple[PropertySpec, ...],
) -> BaseModel:
    if len(row) != len(source_properties):
        raise CsvSnapshotError(
            f"{table_spec.table_name} row {row_number} column count is {len(row)}; "
            f"expected {len(source_properties)}"
        )
    decoded: dict[str, object] = {}
    for property_spec, encoded in zip(source_properties, row, strict=True):
        try:
            decoded[property_spec.name] = decode_scalar(
                encoded, property_spec.scalar_type
            )
        except CsvScalarError as error:
            raise CsvSnapshotError(
                f"{table_spec.table_name} row {row_number} "
                f"column {property_spec.name}: {error}"
            ) from error
    _migrate_legacy_values(table_spec, decoded)
    custom_properties: list[graph.CustomProperty] = []
    for property_spec in table_spec.properties:
        if property_spec.name not in decoded:
            if property_spec.required_on_read:
                raise CsvSnapshotError(
                    f"{table_spec.table_name} row {row_number} "
                    f"required column {property_spec.name} is missing"
                )
            decoded[property_spec.name] = property_spec.default
        if property_spec.custom:
            custom_properties.append(
                graph.CustomProperty.model_validate(
                    {
                        "name": property_spec.name,
                        "value": decoded.pop(property_spec.name),
                    }
                )
            )
    if isinstance(table_spec, NodeSpec):
        decoded["custom_properties"] = tuple(custom_properties)
    try:
        return table_spec.model.model_validate(decoded)
    except ValidationError as error:
        location = error.errors()[0].get("loc", ("unknown",))
        column = str(location[0]) if location else "unknown"
        raise CsvSnapshotError(
            f"{table_spec.table_name} row {row_number} column {column}: {error}"
        ) from error


def _migrate_legacy_values(table_spec: TableSpec, decoded: dict[str, object]) -> None:
    if table_spec.table_name == "Asset" and "caption" in decoded:
        decoded["description"] = decoded.pop("caption")
        decoded.pop("llm_description", None)
    if table_spec.table_name == "Embedding":
        purpose = decoded.pop("purpose", None)
        if purpose is not None and purpose not in {
            "chunk_retrieval",
            "asset_description_retrieval",
        }:
            raise CsvSnapshotError("legacy Embedding purpose is invalid")
