from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Final, Literal, TypeAlias

from pydantic import BaseModel

from mvp_v2.domain import graph
from mvp_v2.domain.field_extensions import ExtensionDefault, load_field_extensions
from mvp_v2.domain.snapshot import GraphSnapshot

LEGACY_GRAPH_SCHEMA_VERSION: Final[Literal["mvp-v2-graph-v1"]] = "mvp-v2-graph-v1"
GRAPH_SCHEMA_VERSION: Final[Literal["mvp-v2-graph-v2"]] = "mvp-v2-graph-v2"
NODE_TABLE_NAMES = (
    "SourceType",
    "Document",
    "DocumentVersion",
    "Chunk",
    "Asset",
    "Embedding",
    "Role",
    "Process",
    "Status",
)
RELATION_TABLE_NAMES = (
    "HAS_SOURCE_TYPE",
    "HAS_VERSION",
    "SUPERSEDES",
    "HAS_STATUS",
    "HAS_CHUNK",
    "HAS_ASSET",
    "CONTAINS_ASSET",
    "HAS_EMBEDDING_CHUNK",
    "HAS_EMBEDDING_ASSET",
    "HAS_SUMMARY_EMBEDDING",
    "LINKS_TO",
    "DESCRIBES",
    "FOR_ROLE",
)
TABLE_NAMES = (*NODE_TABLE_NAMES, *RELATION_TABLE_NAMES)
GraphNodeTableName: TypeAlias = Literal[
    "SourceType",
    "Document",
    "DocumentVersion",
    "Chunk",
    "Asset",
    "Embedding",
    "Role",
    "Process",
    "Status",
]
GraphRelationType: TypeAlias = Literal[
    "HAS_SOURCE_TYPE",
    "HAS_VERSION",
    "SUPERSEDES",
    "HAS_STATUS",
    "HAS_CHUNK",
    "HAS_ASSET",
    "CONTAINS_ASSET",
    "HAS_EMBEDDING_CHUNK",
    "HAS_EMBEDDING_ASSET",
    "HAS_SUMMARY_EMBEDDING",
    "LINKS_TO",
    "DESCRIBES",
    "FOR_ROLE",
]
GraphTableName: TypeAlias = GraphNodeTableName | GraphRelationType


class ScalarType(StrEnum):
    STRING = "STRING"
    INT64 = "INT64"
    BOOLEAN = "BOOLEAN"
    TIMESTAMP = "TIMESTAMP"
    FLOAT_VECTOR = "FLOAT_VECTOR"


class VectorPolicy(StrEnum):
    NONE = "none"
    EXACT_DIMENSION = "exact_dimension"
    EMPTY_OR_DIMENSION = "empty_or_dimension"


@dataclass(frozen=True, slots=True, kw_only=True)
class PropertySpec:
    name: str
    scalar_type: ScalarType
    primary_key: bool = False
    vector_dimension_field: str | None = None
    vector_policy: VectorPolicy = VectorPolicy.NONE
    required_on_read: bool = True
    default: ExtensionDefault | None = None
    custom: bool = False


@dataclass(frozen=True, slots=True, kw_only=True)
class NodeSpec:
    table_name: GraphNodeTableName
    snapshot_field: str
    label: GraphNodeTableName
    model: type[BaseModel]
    properties: tuple[PropertySpec, ...]

    @property
    def headers(self) -> tuple[str, ...]:
        return tuple(item.name for item in self.properties)


@dataclass(frozen=True, slots=True, kw_only=True)
class RelationSpec:
    table_name: GraphRelationType
    snapshot_field: str
    relation_type: GraphRelationType
    model: type[BaseModel]
    source_label: GraphNodeTableName
    target_label: GraphNodeTableName
    properties: tuple[PropertySpec, ...] = (
        PropertySpec(name="from", scalar_type=ScalarType.STRING),
        PropertySpec(name="to", scalar_type=ScalarType.STRING),
    )

    @property
    def headers(self) -> tuple[str, ...]:
        return tuple(item.name for item in self.properties)


def _property(
    name: str,
    scalar_type: ScalarType,
    *,
    required_on_read: bool = True,
    default: ExtensionDefault | None = None,
    vector_policy: VectorPolicy = VectorPolicy.NONE,
) -> PropertySpec:
    return PropertySpec(
        name=name,
        scalar_type=scalar_type,
        required_on_read=required_on_read,
        default=default,
        vector_policy=vector_policy,
        vector_dimension_field=(
            "dimension" if vector_policy is not VectorPolicy.NONE else None
        ),
    )


def _id() -> PropertySpec:
    return PropertySpec(name="id", scalar_type=ScalarType.STRING, primary_key=True)


_CORE_NODE_SPECS: tuple[NodeSpec, ...] = (
    NodeSpec(
        table_name="SourceType",
        snapshot_field="source_types",
        label="SourceType",
        model=graph.SourceType,
        properties=(_id(), _property("code", ScalarType.STRING)),
    ),
    NodeSpec(
        table_name="Document",
        snapshot_field="documents",
        label="Document",
        model=graph.Document,
        properties=(
            _id(),
            _property("title", ScalarType.STRING),
            _property("source_name", ScalarType.STRING),
            _property("content_format", ScalarType.STRING),
            _property("source_url", ScalarType.STRING),
            _property("external_id", ScalarType.STRING),
            _property("is_active", ScalarType.BOOLEAN),
            _property("summary", ScalarType.STRING, required_on_read=False, default=""),
        ),
    ),
    NodeSpec(
        table_name="DocumentVersion",
        snapshot_field="document_versions",
        label="DocumentVersion",
        model=graph.DocumentVersion,
        properties=(
            _id(),
            _property("version", ScalarType.INT64),
            _property("sha256", ScalarType.STRING),
            _property("local_path", ScalarType.STRING),
            _property("created_at", ScalarType.TIMESTAMP),
            _property("is_current", ScalarType.BOOLEAN),
            _property("error", ScalarType.STRING),
        ),
    ),
    NodeSpec(
        table_name="Chunk",
        snapshot_field="chunks",
        label="Chunk",
        model=graph.Chunk,
        properties=(
            _id(),
            _property("position", ScalarType.INT64),
            _property("content", ScalarType.STRING),
            _property("content_hash", ScalarType.STRING),
            _property("summary", ScalarType.STRING, required_on_read=False, default=""),
        ),
    ),
    NodeSpec(
        table_name="Asset",
        snapshot_field="assets",
        label="Asset",
        model=graph.Asset,
        properties=(
            _id(),
            _property("asset_type", ScalarType.STRING),
            _property("mime_type", ScalarType.STRING),
            _property("source_url", ScalarType.STRING),
            _property("local_path", ScalarType.STRING),
            _property("external_id", ScalarType.STRING),
            _property("sha256", ScalarType.STRING),
            _property("position", ScalarType.INT64),
            _property(
                "description", ScalarType.STRING, required_on_read=False, default=""
            ),
            _property(
                "ocr_text", ScalarType.STRING, required_on_read=False, default=""
            ),
        ),
    ),
    NodeSpec(
        table_name="Embedding",
        snapshot_field="embeddings",
        label="Embedding",
        model=graph.Embedding,
        properties=(
            _id(),
            _property("provider", ScalarType.STRING),
            _property("model", ScalarType.STRING),
            _property("model_version", ScalarType.STRING),
            _property("dimension", ScalarType.INT64),
            _property(
                "vector",
                ScalarType.FLOAT_VECTOR,
                vector_policy=VectorPolicy.EXACT_DIMENSION,
            ),
            _property("created_at", ScalarType.TIMESTAMP),
            _property("is_current", ScalarType.BOOLEAN),
            _property(
                "summary_vector",
                ScalarType.FLOAT_VECTOR,
                required_on_read=False,
                default=(),
                vector_policy=VectorPolicy.EMPTY_OR_DIMENSION,
            ),
        ),
    ),
    NodeSpec(
        table_name="Role",
        snapshot_field="roles",
        label="Role",
        model=graph.Role,
        properties=(_id(), _property("name", ScalarType.STRING)),
    ),
    NodeSpec(
        table_name="Process",
        snapshot_field="processes",
        label="Process",
        model=graph.Process,
        properties=(
            _id(),
            _property("name", ScalarType.STRING),
            _property("status", ScalarType.STRING),
            _property("owner", ScalarType.STRING),
        ),
    ),
    NodeSpec(
        table_name="Status",
        snapshot_field="statuses",
        label="Status",
        model=graph.Status,
        properties=(
            _id(),
            _property("name", ScalarType.STRING),
            _property("description", ScalarType.STRING),
        ),
    ),
)


def build_node_specs(config_path: Path | None = None) -> tuple[NodeSpec, ...]:
    configured = load_field_extensions(config_path)
    known_tables = {item.table_name for item in _CORE_NODE_SPECS}
    unknown_tables = set(configured) - known_tables
    if unknown_tables:
        raise ValueError(
            f"unknown node tables in graph field config: {sorted(unknown_tables)}"
        )
    result: list[NodeSpec] = []
    for spec in _CORE_NODE_SPECS:
        core_names = {item.name for item in spec.properties}
        extension_specs: list[PropertySpec] = []
        for extension in configured.get(spec.table_name, ()):
            if extension.scalar_type == "FLOAT_VECTOR" and not any(
                item.name == "dimension" for item in spec.properties
            ):
                raise ValueError(
                    f"FLOAT_VECTOR extension requires dimension in {spec.table_name}"
                )
            if extension.name in core_names or extension.name == "custom_properties":
                raise ValueError(
                    f"extension field collides with {spec.table_name}.{extension.name}"
                )
            extension_specs.append(
                PropertySpec(
                    name=extension.name,
                    scalar_type=ScalarType(extension.scalar_type),
                    required_on_read=False,
                    default=extension.default,
                    custom=True,
                    vector_policy=(
                        VectorPolicy.EMPTY_OR_DIMENSION
                        if extension.scalar_type == "FLOAT_VECTOR"
                        else VectorPolicy.NONE
                    ),
                    vector_dimension_field=(
                        "dimension"
                        if extension.scalar_type == "FLOAT_VECTOR"
                        and any(item.name == "dimension" for item in spec.properties)
                        else None
                    ),
                )
            )
        result.append(
            NodeSpec(
                table_name=spec.table_name,
                snapshot_field=spec.snapshot_field,
                label=spec.label,
                model=spec.model,
                properties=(*spec.properties, *extension_specs),
            )
        )
    return tuple(result)


NODE_SPECS: tuple[NodeSpec, ...] = build_node_specs()


def _relation(
    name: GraphRelationType,
    snapshot_field: str,
    model: type[BaseModel],
    source: GraphNodeTableName,
    target: GraphNodeTableName,
) -> RelationSpec:
    return RelationSpec(
        table_name=name,
        snapshot_field=snapshot_field,
        relation_type=name,
        model=model,
        source_label=source,
        target_label=target,
    )


RELATION_SPECS: tuple[RelationSpec, ...] = (
    _relation(
        "HAS_SOURCE_TYPE",
        "has_source_types",
        graph.HasSourceType,
        "Document",
        "SourceType",
    ),
    _relation(
        "HAS_VERSION", "has_versions", graph.HasVersion, "Document", "DocumentVersion"
    ),
    _relation(
        "SUPERSEDES",
        "supersedes",
        graph.Supersedes,
        "DocumentVersion",
        "DocumentVersion",
    ),
    _relation(
        "HAS_STATUS", "has_statuses", graph.HasStatus, "DocumentVersion", "Status"
    ),
    _relation("HAS_CHUNK", "has_chunks", graph.HasChunk, "DocumentVersion", "Chunk"),
    _relation("HAS_ASSET", "has_assets", graph.HasAsset, "DocumentVersion", "Asset"),
    _relation(
        "CONTAINS_ASSET", "contains_assets", graph.ContainsAsset, "Chunk", "Asset"
    ),
    _relation(
        "HAS_EMBEDDING_CHUNK",
        "has_embedding_chunks",
        graph.HasEmbeddingChunk,
        "Chunk",
        "Embedding",
    ),
    _relation(
        "HAS_EMBEDDING_ASSET",
        "has_embedding_assets",
        graph.HasEmbeddingAsset,
        "Asset",
        "Embedding",
    ),
    _relation(
        "HAS_SUMMARY_EMBEDDING",
        "has_summary_embeddings",
        graph.HasSummaryEmbedding,
        "Document",
        "Embedding",
    ),
    _relation("LINKS_TO", "links_to", graph.LinksTo, "Document", "Document"),
    _relation("DESCRIBES", "describes", graph.Describes, "Document", "Process"),
    _relation("FOR_ROLE", "for_roles", graph.ForRole, "Document", "Role"),
)


TABLE_SPECS: tuple[NodeSpec | RelationSpec, ...] = (*NODE_SPECS, *RELATION_SPECS)
LEGACY_TABLE_NAMES: Final = tuple(
    name for name in TABLE_NAMES if name != "HAS_SUMMARY_EMBEDDING"
)


def _schema_profile_sha256() -> str:
    payload = [
        {
            "table": spec.table_name,
            "properties": [
                {
                    "custom": prop.custom,
                    "default": prop.default,
                    "name": prop.name,
                    "required_on_read": prop.required_on_read,
                    "type": prop.scalar_type.value,
                    "vector_policy": prop.vector_policy.value,
                }
                for prop in spec.properties
            ],
        }
        for spec in TABLE_SPECS
    ]
    encoded = json.dumps(
        payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


SCHEMA_PROFILE_SHA256: Final = _schema_profile_sha256()


def registry_errors(
    node_specs: tuple[NodeSpec, ...] = NODE_SPECS,
    relation_specs: tuple[RelationSpec, ...] = RELATION_SPECS,
) -> tuple[str, ...]:
    """Return explicit registry collision and completeness errors."""
    errors: list[str] = []
    node_names = tuple(item.table_name for item in node_specs)
    relation_names = tuple(item.table_name for item in relation_specs)
    snapshot_fields = tuple(
        item.snapshot_field
        for item in tuple[NodeSpec | RelationSpec, ...]((*node_specs, *relation_specs))
    )
    if snapshot_fields != tuple(GraphSnapshot.model_fields):
        errors.append(
            "snapshot field ownership/order does not match the graph contract"
        )
    if node_names != NODE_TABLE_NAMES:
        errors.append("node table names/order do not match the graph contract")
    if relation_names != RELATION_TABLE_NAMES:
        errors.append("relation table names/order do not match the graph contract")
    if len(set((*node_names, *relation_names))) != len(node_names) + len(
        relation_names
    ):
        errors.append("graph table names collide")
    labels = {item.label for item in node_specs}
    for node_spec in node_specs:
        if node_spec.label != node_spec.table_name:
            errors.append(f"node label does not match table {node_spec.table_name}")
        names = tuple(prop.name for prop in node_spec.properties)
        if names != node_spec.headers:
            errors.append(f"property names/order do not match {node_spec.table_name}")
        model_names = tuple(
            name for name in node_spec.model.model_fields if name != "custom_properties"
        )
        declared_core_names = tuple(
            prop.name for prop in node_spec.properties if not prop.custom
        )
        if model_names != declared_core_names:
            errors.append(f"model fields/order do not match {node_spec.table_name}")
        primary_keys = tuple(prop for prop in node_spec.properties if prop.primary_key)
        if len(primary_keys) != 1 or primary_keys[0].name != "id":
            errors.append(
                f"{node_spec.table_name} must have exactly one id primary key"
            )
        if len(names) != len(set(names)):
            errors.append(f"duplicate property name in {node_spec.table_name}")
        canonical = next(
            (item for item in NODE_SPECS if item.table_name == node_spec.table_name),
            None,
        )
        actual_types = tuple(prop.scalar_type for prop in node_spec.properties)
        if canonical is not None and actual_types != tuple(
            prop.scalar_type for prop in canonical.properties
        ):
            errors.append(f"property types do not match {node_spec.table_name}")
        for prop in node_spec.properties:
            if prop.scalar_type is ScalarType.FLOAT_VECTOR:
                if prop.vector_dimension_field not in names:
                    errors.append(
                        f"missing vector dimension policy in {node_spec.table_name}"
                    )
                else:
                    dimension = next(
                        item
                        for item in node_spec.properties
                        if item.name == prop.vector_dimension_field
                    )
                    if dimension.scalar_type is not ScalarType.INT64:
                        errors.append(
                            "vector dimension must reference INT64 in "
                            f"{node_spec.table_name}"
                        )
            elif prop.vector_dimension_field is not None:
                errors.append(
                    "non-vector property has dimension policy in "
                    f"{node_spec.table_name}"
                )
    for relation_spec in relation_specs:
        if relation_spec.relation_type != relation_spec.table_name:
            errors.append(
                f"relation type does not match table {relation_spec.table_name}"
            )
        canonical_relation = next(
            (
                item
                for item in RELATION_SPECS
                if item.table_name == relation_spec.table_name
            ),
            None,
        )
        if canonical_relation is not None and (
            relation_spec.source_label,
            relation_spec.target_label,
        ) != (canonical_relation.source_label, canonical_relation.target_label):
            errors.append(
                f"endpoint direction does not match {relation_spec.table_name}"
            )
        if (
            tuple(prop.name for prop in relation_spec.properties)
            != relation_spec.headers
        ):
            errors.append(
                f"property names/order do not match {relation_spec.table_name}"
            )
        if any(prop.primary_key for prop in relation_spec.properties):
            errors.append(
                f"relation {relation_spec.table_name} must not define a primary key"
            )
        if tuple(prop.scalar_type for prop in relation_spec.properties) != (
            ScalarType.STRING,
            ScalarType.STRING,
        ):
            errors.append(
                f"relation property types do not match {relation_spec.table_name}"
            )
        if (
            relation_spec.source_label not in labels
            or relation_spec.target_label not in labels
        ):
            errors.append(f"unknown endpoint label for {relation_spec.relation_type}")
    return tuple(errors)


def assert_registry_complete() -> None:
    errors = registry_errors()
    if errors:
        raise ValueError("; ".join(errors))
