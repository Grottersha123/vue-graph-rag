from __future__ import annotations

# ruff: noqa: E501
import hashlib
import importlib
import math
import shutil
import tempfile
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any, Final, cast

from mvp_v2.adapters.csv.snapshot import read_snapshot
from mvp_v2.application.dto.retrieval import (
    EmbeddingPurpose,
    EmbeddingSpaceIdentity,
    SubjectKind,
    SubjectRef,
    VectorHit,
    VectorQuery,
)
from mvp_v2.application.dump_lifecycle.manifests import (
    DATABASE_FORMAT,
    DatabaseManifest,
    DumpManifest,
    TableCount,
    compute_database_build_id,
)
from mvp_v2.application.json_encoding import canonical_json_bytes
from mvp_v2.application.rag.retrieval.projection import active_projection
from mvp_v2.domain.invariants import validate_graph
from mvp_v2.domain.schema_registry import (
    GRAPH_SCHEMA_VERSION,
    NODE_SPECS,
    RELATION_SPECS,
    SCHEMA_PROFILE_SHA256,
    PropertySpec,
    ScalarType,
    VectorPolicy,
)
from mvp_v2.domain.snapshot import GraphSnapshot
from mvp_v2.ports.ladybug import ReplacementBuildError

VECTOR_INDEX: Final = "embedding_cosine_idx"
_PURPOSE_OVERFETCH_FACTOR: Final = 10
_MAX_PURPOSE_CANDIDATES: Final = 1_000
DATABASE_MANIFEST: Final = "_mvp_v2_manifest.json"


class LadybugCapabilityError(RuntimeError):
    pass


class LadybugBuildError(ReplacementBuildError):
    pass


def node_ddl(dimension: int) -> tuple[str, ...]:
    def kind(prop: PropertySpec) -> str:
        if prop.scalar_type is ScalarType.FLOAT_VECTOR:
            return f"FLOAT[{dimension}]"
        return prop.scalar_type.value

    return tuple(
        "CREATE NODE TABLE "
        + s.label
        + " ("
        + ", ".join(
            f"{p.name} {kind(p)}" + (" PRIMARY KEY" if p.primary_key else "")
            for p in s.properties
        )
        + ")"
        for s in NODE_SPECS
    )


def relation_ddl() -> tuple[str, ...]:
    return tuple(
        f"CREATE REL TABLE {s.relation_type} (FROM {s.source_label} TO {s.target_label})"
        for s in RELATION_SPECS
    )


def load_verified_dump(path: Path) -> tuple[DumpManifest, GraphSnapshot]:
    try:
        if path.is_symlink() or not path.is_dir():
            raise ValueError("invalid dump directory")
        actual: set[str] = set()
        for entry in path.rglob("*"):
            relative = entry.relative_to(path).as_posix()
            if entry.is_symlink() or not entry.is_file():
                if relative != "tables":
                    raise ValueError("dump contains non-regular entry")
                continue
            actual.add(relative)
        manifest_bytes = (path / "manifest.json").read_bytes()
        manifest = DumpManifest.model_validate_json(manifest_bytes)
        if manifest.schema_profile_sha256 != SCHEMA_PROFILE_SHA256:
            raise ValueError("dump schema profile does not match current config")
        expected = {"manifest.json", *(item.path for item in manifest.files)}
        if actual != expected:
            raise ValueError("dump file set mismatch")
        if canonical_json_bytes(manifest.canonical_payload()) != manifest_bytes:
            raise ValueError("manifest is not canonical")
        for fact in manifest.files:
            table_file = path / fact.path
            if table_file.is_symlink() or not table_file.is_file():
                raise ValueError("invalid table file")
            raw = table_file.read_bytes()
            if hashlib.sha256(raw).hexdigest() != fact.sha256:
                raise ValueError("hash mismatch")
        snapshot = read_snapshot(path)
        counts: dict[str, int] = {
            spec.table_name: len(getattr(snapshot, spec.snapshot_field))
            for spec in NODE_SPECS
        }
        counts.update(
            {
                spec.table_name: len(getattr(snapshot, spec.snapshot_field))
                for spec in RELATION_SPECS
            }
        )
        if any(
            counts[Path(fact.path).stem] != fact.row_count for fact in manifest.files
        ):
            raise ValueError("row count mismatch")
        if not validate_graph(snapshot).is_valid:
            raise ValueError("graph invariants failed")
        return manifest, snapshot
    except Exception as error:
        if isinstance(error, LadybugBuildError):
            raise
        raise LadybugBuildError(
            "selected canonical dump verification failed"
        ) from error


def _rows(result: Any) -> list[list[object]]:
    values: list[list[object]] = []
    try:
        while result.has_next():
            values.append(list(result.get_next()))
    finally:
        result.close()
    return values


def _execute(
    connection: Any, query: str, parameters: dict[str, object] | None = None
) -> list[list[object]]:
    result = (
        connection.execute(query, parameters)
        if parameters is not None
        else connection.execute(query)
    )
    return _rows(result)


def _ladybug_scalar(value: object, prop: PropertySpec, dimension: int) -> object:
    if prop.scalar_type is ScalarType.STRING:
        if isinstance(value, Enum):
            value = value.value
        if not isinstance(value, str):
            raise LadybugBuildError("invalid STRING property")
        return value
    if prop.scalar_type is ScalarType.INT64:
        if isinstance(value, bool) or not isinstance(value, int):
            raise LadybugBuildError("invalid INT64 property")
        return value
    if prop.scalar_type is ScalarType.BOOLEAN:
        if not isinstance(value, bool):
            raise LadybugBuildError("invalid BOOLEAN property")
        return value
    if prop.scalar_type is ScalarType.TIMESTAMP:
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise LadybugBuildError("invalid TIMESTAMP property")
        return value.astimezone(UTC).replace(tzinfo=None)
    if not isinstance(value, (list, tuple)):
        raise LadybugBuildError("invalid FLOAT_VECTOR value")
    if prop.vector_policy is VectorPolicy.EMPTY_OR_DIMENSION and not value:
        return None
    if dimension < 1 or len(value) != dimension:
        raise LadybugBuildError("invalid FLOAT_VECTOR dimension")
    if any(
        isinstance(item, bool) or not isinstance(item, (int, float)) for item in value
    ):
        raise LadybugBuildError("invalid FLOAT_VECTOR value")
    converted = [float(item) for item in value]
    if not all(math.isfinite(item) for item in converted):
        raise LadybugBuildError("invalid FLOAT_VECTOR value")
    return converted


def _properties(model: Any, spec: Any, dimension: int) -> dict[str, object]:
    raw = cast(dict[str, object], model.model_dump(mode="python", by_alias=True))
    custom_values = {
        item.name: item.value for item in getattr(model, "custom_properties", ())
    }
    values: dict[str, object] = {}
    for prop in spec.properties:
        value = (
            custom_values.get(prop.name, prop.default)
            if prop.custom
            else raw[prop.name]
        )
        values[prop.name] = _ladybug_scalar(value, prop, dimension)
    return values


def load_graph(connection: Any, snapshot: GraphSnapshot, dimension: int) -> None:
    for statement in (*node_ddl(dimension), *relation_ddl()):
        _execute(connection, statement)
    for spec in NODE_SPECS:
        columns = tuple(p.name for p in spec.properties)
        query = (
            f"CREATE (n:{spec.label} {{{', '.join(f'{c}: ${c}' for c in columns)}}})"
        )
        for model in getattr(snapshot, spec.snapshot_field):
            _execute(connection, query, _properties(model, spec, dimension))
    for relation_spec in RELATION_SPECS:
        query = f"MATCH (a:{relation_spec.source_label} {{id: $from}}), (b:{relation_spec.target_label} {{id: $to}}) CREATE (a)-[:{relation_spec.relation_type}]->(b)"
        for edge in getattr(snapshot, relation_spec.snapshot_field):
            _execute(
                connection,
                query,
                {"from": str(edge.source_id), "to": str(edge.target_id)},
            )


def enable_ann(connection: Any) -> None:
    try:
        _execute(connection, "LOAD vector")
        _execute(
            connection,
            f"CALL CREATE_VECTOR_INDEX('Embedding', '{VECTOR_INDEX}', 'vector', metric := 'cosine')",
        )
    except Exception as error:
        raise LadybugCapabilityError(
            "vector capability is unavailable; provision it explicitly"
        ) from error


def build_database(
    *,
    dump_path: Path,
    target: Path,
    database_name: str,
    space: EmbeddingSpaceIdentity,
    vision_provider: str,
    vision_model: str,
    vision_model_version: str,
    built_at: datetime | None = None,
) -> DatabaseManifest:
    parent = target.parent
    if (
        not parent.exists()
        or not parent.is_dir()
        or parent.is_symlink()
        or parent.resolve() != parent.absolute()
        or target.parent != parent
        or target.suffix != ".lbdb"
        or target.name in {"", ".", ".."}
        or "/" in target.name
        or "\\" in target.name
    ):
        raise LadybugBuildError("database target must be a safe direct .lbdb child")
    if (
        not database_name
        or database_name in {".", ".."}
        or "/" in database_name
        or "\\" in database_name
        or Path(database_name).name != database_name
        or any(
            ord(character) < 32 or ord(character) == 127 for character in database_name
        )
    ):
        raise LadybugBuildError("database name must be a safe path segment")
    if target.exists() or target.is_symlink():
        raise LadybugBuildError("database target already exists")
    dump, source = load_verified_dump(dump_path)
    projected = active_projection(source, space)
    stage = (
        Path(tempfile.mkdtemp(prefix=f".{target.name}-", dir=target.parent))
        / target.name
    )
    database_file = stage / "database.lbug"
    db: Any = None
    connection: Any = None
    try:
        ladybug = importlib.import_module("ladybug")
        stage.mkdir()
        db = ladybug.Database(str(database_file))
        connection = ladybug.Connection(db)
        load_graph(connection, projected, space.dimension)
        node_counts = tuple(
            TableCount(
                table=s.table_name, count=len(getattr(projected, s.snapshot_field))
            )
            for s in NODE_SPECS
        )
        relation_counts = tuple(
            TableCount(
                table=s.table_name, count=len(getattr(projected, s.snapshot_field))
            )
            for s in RELATION_SPECS
        )
        counts = (*node_counts, *relation_counts)
        for count in counts:
            rows = (
                _execute(connection, f"MATCH (n:{count.table}) RETURN count(n)")
                if count.table in {s.table_name for s in NODE_SPECS}
                else _execute(
                    connection, f"MATCH ()-[r:{count.table}]->() RETURN count(r)"
                )
            )
            if not rows or rows[0][0] != count.count:
                raise LadybugBuildError("database row-count verification failed")
        enable_ann(connection)
        known = projected.embeddings[0]
        smoke = _execute(
            connection,
            f"CALL QUERY_VECTOR_INDEX('Embedding', '{VECTOR_INDEX}', $vector, $k, efs := 32) YIELD node, distance RETURN node.id, distance",
            {"vector": list(known.vector), "k": 1},
        )
        projected_embedding_ids = {item.id for item in projected.embeddings}
        valid_smoke = any(
            str(row[0]) in projected_embedding_ids
            and math.isfinite(float(cast(str | int | float, row[1])))
            and abs(float(cast(str | int | float, row[1]))) <= 1e-6
            for row in smoke
        )
        if not valid_smoke:
            raise LadybugBuildError("vector smoke query failed")
        connection.close()
        connection = None
        db.close()
        db = None
        now = built_at or datetime.now(UTC)
        build_id = compute_database_build_id(
            database_name=database_name,
            built_at=now,
            source_dump_number=dump.dump_number,
            source_dump_published_at=dump.published_at,
            source_dump_run_id=dump.run_id,
            embedding_space=space,
            vision_provider=vision_provider,
            vision_model=vision_model,
            vision_model_version=vision_model_version,
            row_counts=counts,
        )
        manifest = DatabaseManifest(
            format=DATABASE_FORMAT,
            graph_schema_version=GRAPH_SCHEMA_VERSION,
            schema_profile_sha256=SCHEMA_PROFILE_SHA256,
            build_id=build_id,
            database_name=database_name,
            built_at=now,
            source_dump_number=dump.dump_number,
            source_dump_published_at=dump.published_at,
            source_dump_run_id=dump.run_id,
            embedding_space=space,
            vision_provider=vision_provider,
            vision_model=vision_model,
            vision_model_version=vision_model_version,
            row_counts=counts,
        )
        # Reopen and verify the staged database before adding its internal manifest.
        db = ladybug.Database(str(database_file))
        connection = ladybug.Connection(db)
        _execute(connection, "MATCH (n:Embedding) RETURN count(n)")
        connection.close()
        connection = None
        db.close()
        db = None
        manifest_path = stage / DATABASE_MANIFEST
        manifest_bytes = canonical_json_bytes(manifest.canonical_payload())
        manifest_path.write_bytes(manifest_bytes)
        parsed_manifest = DatabaseManifest.model_validate_json(
            manifest_path.read_bytes()
        )
        if parsed_manifest != manifest or manifest_path.read_bytes() != manifest_bytes:
            raise LadybugBuildError("database manifest verification failed")
        stage.rename(target)
        return manifest
    except (LadybugBuildError, LadybugCapabilityError):
        raise
    except Exception as error:
        raise LadybugBuildError("Ladybug database build failed safely") from error
    finally:
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass
        if db is not None:
            try:
                db.close()
            except Exception:
                pass
        shutil.rmtree(stage.parent, ignore_errors=True)


class LadybugAnn:
    def __init__(self, connection: Any, manifest: DatabaseManifest) -> None:
        self._connection, self._manifest = connection, manifest

    def query(self, request: VectorQuery) -> tuple[VectorHit, ...]:
        if request.space != self._manifest.embedding_space:
            raise ValueError(
                "embedding-space identity does not match database manifest"
            )
        if request.limit < 1 or request.limit > 100:
            raise ValueError("limit must be between 1 and 100")
        if len(request.vector) != self._manifest.embedding_space.dimension or any(
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not math.isfinite(float(value))
            for value in request.vector
        ):
            raise ValueError("query vector is invalid for database dimension")
        if not request.purposes or any(
            purpose
            not in {
                EmbeddingPurpose.CHUNK_RETRIEVAL,
                EmbeddingPurpose.ASSET_DESCRIPTION_RETRIEVAL,
            }
            for purpose in request.purposes
        ):
            raise ValueError("query purposes are invalid")
        _execute(self._connection, "LOAD vector")
        all_purposes = {
            EmbeddingPurpose.CHUNK_RETRIEVAL,
            EmbeddingPurpose.ASSET_DESCRIPTION_RETRIEVAL,
        }
        embedding_count = next(
            (
                item.count
                for item in self._manifest.row_counts
                if item.table == "Embedding"
            ),
            request.limit,
        )
        candidate_limit = request.limit
        if set(request.purposes) != all_purposes:
            available = max(request.limit, embedding_count)
            budget = min(
                request.limit * _PURPOSE_OVERFETCH_FACTOR,
                _MAX_PURPOSE_CANDIDATES,
            )
            candidate_limit = min(available, budget)
        rows = _execute(
            self._connection,
            f"CALL QUERY_VECTOR_INDEX('Embedding', '{VECTOR_INDEX}', $vector, $k, efs := 32) YIELD node, distance RETURN node.id, distance",
            {"vector": list(request.vector), "k": candidate_limit},
        )
        ownership_queries = (
            (
                EmbeddingPurpose.CHUNK_RETRIEVAL,
                SubjectKind.CHUNK,
                "HAS_EMBEDDING_CHUNK",
            ),
            (
                EmbeddingPurpose.ASSET_DESCRIPTION_RETRIEVAL,
                SubjectKind.ASSET,
                "HAS_EMBEDDING_ASSET",
            ),
        )
        hits = []
        for embedding_id, distance_raw in rows:
            distance = float(cast(str | int | float, distance_raw))
            if not math.isfinite(distance):
                raise LadybugCapabilityError("ANN returned non-finite distance")
            owners: list[tuple[EmbeddingPurpose, SubjectKind, str]] = []
            for purpose, kind, relation in ownership_queries:
                if purpose not in request.purposes:
                    continue
                owner = _execute(
                    self._connection,
                    f"MATCH (s)-[:{relation}]->(e:Embedding {{id: $id}}) RETURN s.id",
                    {"id": str(embedding_id)},
                )
                if len(owner) > 1:
                    raise LadybugCapabilityError("embedding ownership is invalid")
                if owner:
                    owners.append((purpose, kind, str(owner[0][0])))
            if not owners:
                continue
            if len(owners) != 1:
                raise LadybugCapabilityError("embedding owner kind is ambiguous")
            purpose, kind, subject_id = owners[0]
            hits.append(
                VectorHit(
                    subject=SubjectRef(kind=kind, id=subject_id),
                    embedding_id=str(embedding_id),
                    purpose=purpose,
                    score=1.0 - distance,
                )
            )
            if len(hits) == request.limit:
                break
        return tuple(sorted(hits, key=lambda x: (-x.score, x.embedding_id)))
