from __future__ import annotations

import hashlib
import json
from typing import Annotated, Final, Literal, TypeAlias

from pydantic import Field, model_validator

from mvp_v2.application.dto.retrieval import EmbeddingSpaceIdentity
from mvp_v2.domain.base import (
    ContractModel,
    NonBlankText,
    RelativePath,
    Sha256,
    UtcDateTime,
)
from mvp_v2.domain.schema_registry import (
    GRAPH_SCHEMA_VERSION,
    SCHEMA_PROFILE_SHA256,
    TABLE_NAMES,
)

DUMP_FORMAT: Final[Literal["mvp-v2-dump-v1"]] = "mvp-v2-dump-v1"
DATABASE_FORMAT: Final[Literal["mvp-v2-db-v1"]] = "mvp-v2-db-v1"
EXPECTED_TABLE_PATHS: Final = tuple(f"tables/{name}.csv" for name in TABLE_NAMES)
GraphSchemaVersion: TypeAlias = Literal["mvp-v2-graph-v2"]
PositiveInt = Annotated[int, Field(strict=True, ge=1, le=9_223_372_036_854_775_807)]
NonNegativeInt = Annotated[int, Field(strict=True, ge=0, le=9_223_372_036_854_775_807)]


class DumpFileEntry(ContractModel):
    path: Annotated[RelativePath, Field(max_length=4096)]
    sha256: Sha256
    row_count: NonNegativeInt


class TableCount(ContractModel):
    table: NonBlankText
    count: NonNegativeInt


def _utc_text(value: UtcDateTime) -> str:
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _canonical_hash(payload: object) -> str:
    encoded = json.dumps(
        payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def compute_dump_run_id(
    dump_number: int,
    published_at: UtcDateTime,
    files: tuple[DumpFileEntry, ...],
    *,
    graph_schema_version: GraphSchemaVersion = GRAPH_SCHEMA_VERSION,
    schema_profile_sha256: str = SCHEMA_PROFILE_SHA256,
) -> str:
    """Hash all immutable dump identity and table-file facts canonically."""
    canonical_files = [
        {"path": entry.path, "row_count": entry.row_count, "sha256": entry.sha256}
        for entry in sorted(files, key=lambda entry: entry.path)
    ]
    payload: dict[str, object] = {
        "dump_number": dump_number,
        "files": canonical_files,
        "format": DUMP_FORMAT,
        "graph_schema_version": graph_schema_version,
        "published_at": _utc_text(published_at),
    }
    payload["schema_profile_sha256"] = schema_profile_sha256
    return _canonical_hash(payload)


class DumpManifest(ContractModel):
    format: Literal["mvp-v2-dump-v1"]
    graph_schema_version: GraphSchemaVersion
    schema_profile_sha256: Sha256 = SCHEMA_PROFILE_SHA256
    dump_number: PositiveInt
    published_at: UtcDateTime
    run_id: Sha256
    files: Annotated[tuple[DumpFileEntry, ...], Field(min_length=22, max_length=22)]

    def canonical_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json")

    @model_validator(mode="after")
    def validate_manifest(self) -> DumpManifest:
        paths = tuple(entry.path for entry in self.files)
        if len(paths) != len(set(paths)):
            raise ValueError("dump file paths must be unique")
        if frozenset(paths) != frozenset(EXPECTED_TABLE_PATHS):
            raise ValueError("dump files do not match the current graph schema")
        expected = compute_dump_run_id(
            self.dump_number,
            self.published_at,
            self.files,
            graph_schema_version=self.graph_schema_version,
            schema_profile_sha256=self.schema_profile_sha256,
        )
        if self.run_id != expected:
            raise ValueError("run_id does not match canonical dump identity")
        return self


def compute_database_build_id(
    *,
    database_name: str,
    built_at: UtcDateTime,
    source_dump_number: int,
    source_dump_published_at: UtcDateTime,
    source_dump_run_id: str,
    embedding_space: EmbeddingSpaceIdentity,
    vision_provider: str,
    vision_model: str,
    vision_model_version: str,
    row_counts: tuple[TableCount, ...],
    graph_schema_version: GraphSchemaVersion = GRAPH_SCHEMA_VERSION,
    schema_profile_sha256: str = SCHEMA_PROFILE_SHA256,
) -> str:
    """Hash every immutable database identity and count fact canonically."""
    counts = [
        {"count": entry.count, "table": entry.table}
        for entry in sorted(row_counts, key=lambda entry: entry.table)
    ]
    payload: dict[str, object] = {
        "built_at": _utc_text(built_at),
        "database_name": database_name,
        "embedding_space": embedding_space.model_dump(mode="json"),
        "format": DATABASE_FORMAT,
        "graph_schema_version": graph_schema_version,
        "row_counts": counts,
        "source_dump_number": source_dump_number,
        "source_dump_published_at": _utc_text(source_dump_published_at),
        "source_dump_run_id": source_dump_run_id,
        "vision_model": vision_model,
        "vision_model_version": vision_model_version,
        "vision_provider": vision_provider,
    }
    payload["schema_profile_sha256"] = schema_profile_sha256
    return _canonical_hash(payload)


class DatabaseManifest(ContractModel):
    format: Literal["mvp-v2-db-v1"]
    graph_schema_version: GraphSchemaVersion
    schema_profile_sha256: Sha256 = SCHEMA_PROFILE_SHA256
    build_id: Sha256
    database_name: NonBlankText
    built_at: UtcDateTime
    source_dump_number: PositiveInt
    source_dump_published_at: UtcDateTime
    source_dump_run_id: Sha256
    embedding_space: EmbeddingSpaceIdentity
    vision_provider: NonBlankText
    vision_model: NonBlankText
    vision_model_version: NonBlankText
    row_counts: Annotated[tuple[TableCount, ...], Field(min_length=22, max_length=22)]

    def canonical_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json")

    @model_validator(mode="after")
    def validate_manifest(self) -> DatabaseManifest:
        tables = tuple(entry.table for entry in self.row_counts)
        if len(tables) != len(set(tables)) or frozenset(tables) != frozenset(
            TABLE_NAMES
        ):
            raise ValueError("row counts do not match the current graph schema")
        expected = compute_database_build_id(
            database_name=self.database_name,
            built_at=self.built_at,
            source_dump_number=self.source_dump_number,
            source_dump_published_at=self.source_dump_published_at,
            source_dump_run_id=self.source_dump_run_id,
            embedding_space=self.embedding_space,
            vision_provider=self.vision_provider,
            vision_model=self.vision_model,
            vision_model_version=self.vision_model_version,
            row_counts=self.row_counts,
            graph_schema_version=self.graph_schema_version,
            schema_profile_sha256=self.schema_profile_sha256,
        )
        if self.build_id != expected:
            raise ValueError("build_id does not match canonical database identity")
        return self
