from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final, Literal, TypeAlias

from pydantic import BaseModel

from mvp_v2.domain import graph
from mvp_v2.domain.snapshot import GraphSnapshot

GRAPH_SCHEMA_VERSION: Final[Literal["mvp-v2-graph-v1"]] = "mvp-v2-graph-v1"
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


@dataclass(frozen=True, slots=True, kw_only=True)
class PropertySpec:
    name: str
    scalar_type: ScalarType
    primary_key: bool = False
    vector_dimension_field: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class NodeSpec:
    table_name: GraphNodeTableName
    snapshot_field: str
    label: GraphNodeTableName
    model: type[BaseModel]
    properties: tuple[PropertySpec, ...]

    @property
    def headers(self) -> tuple[str, ...]:
        return _headers_for(self.model)


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
        return _headers_for(self.model)


def _headers_for(model: type[BaseModel]) -> tuple[str, ...]:
    return tuple(
        field.serialization_alias or name for name, field in model.model_fields.items()
    )


def _property(name: str, scalar_type: ScalarType) -> PropertySpec:
    return PropertySpec(name=name, scalar_type=scalar_type)


def _id() -> PropertySpec:
    return PropertySpec(name="id", scalar_type=ScalarType.STRING, primary_key=True)


NODE_SPECS: tuple[NodeSpec, ...] = (
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
            _property("caption", ScalarType.STRING),
            _property("llm_description", ScalarType.STRING),
            _property("ocr_text", ScalarType.STRING),
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
            _property("purpose", ScalarType.STRING),
            _property("dimension", ScalarType.INT64),
            PropertySpec(
                name="vector",
                scalar_type=ScalarType.FLOAT_VECTOR,
                vector_dimension_field="dimension",
            ),
            _property("created_at", ScalarType.TIMESTAMP),
            _property("is_current", ScalarType.BOOLEAN),
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
    _relation("LINKS_TO", "links_to", graph.LinksTo, "Document", "Document"),
    _relation("DESCRIBES", "describes", graph.Describes, "Document", "Process"),
    _relation("FOR_ROLE", "for_roles", graph.ForRole, "Document", "Role"),
)


TABLE_SPECS: tuple[NodeSpec | RelationSpec, ...] = (*NODE_SPECS, *RELATION_SPECS)


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
