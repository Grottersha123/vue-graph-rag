from __future__ import annotations

from dataclasses import FrozenInstanceError, replace

import pytest

from mvp_v2.application.dto.retrieval import SubjectRef, VectorQuery
from mvp_v2.domain.schema_registry import (
    NODE_SPECS,
    NODE_TABLE_NAMES,
    RELATION_SPECS,
    RELATION_TABLE_NAMES,
    NodeSpec,
    PropertySpec,
    ScalarType,
    assert_registry_complete,
    registry_errors,
)


def test_registry_has_exact_graph_tables_and_directions() -> None:
    assert tuple(spec.table_name for spec in NODE_SPECS) == NODE_TABLE_NAMES
    assert tuple(spec.table_name for spec in RELATION_SPECS) == RELATION_TABLE_NAMES
    assert {
        spec.relation_type: (spec.source_label, spec.target_label)
        for spec in RELATION_SPECS
    } == {
        "HAS_SOURCE_TYPE": ("Document", "SourceType"),
        "HAS_VERSION": ("Document", "DocumentVersion"),
        "SUPERSEDES": ("DocumentVersion", "DocumentVersion"),
        "HAS_STATUS": ("DocumentVersion", "Status"),
        "HAS_CHUNK": ("DocumentVersion", "Chunk"),
        "HAS_ASSET": ("DocumentVersion", "Asset"),
        "CONTAINS_ASSET": ("Chunk", "Asset"),
        "HAS_EMBEDDING_CHUNK": ("Chunk", "Embedding"),
        "HAS_EMBEDDING_ASSET": ("Asset", "Embedding"),
        "LINKS_TO": ("Document", "Document"),
        "DESCRIBES": ("Document", "Process"),
        "FOR_ROLE": ("Document", "Role"),
    }


def test_all_node_property_types_headers_and_primary_keys_are_exact() -> None:
    actual = {
        spec.table_name: tuple(
            (prop.name, prop.scalar_type.value, prop.primary_key)
            for prop in spec.properties
        )
        for spec in NODE_SPECS
    }
    assert actual == {
        "SourceType": (("id", "STRING", True), ("code", "STRING", False)),
        "Document": (
            ("id", "STRING", True),
            ("title", "STRING", False),
            ("source_name", "STRING", False),
            ("content_format", "STRING", False),
            ("source_url", "STRING", False),
            ("external_id", "STRING", False),
            ("is_active", "BOOLEAN", False),
        ),
        "DocumentVersion": (
            ("id", "STRING", True),
            ("version", "INT64", False),
            ("sha256", "STRING", False),
            ("local_path", "STRING", False),
            ("created_at", "TIMESTAMP", False),
            ("is_current", "BOOLEAN", False),
            ("error", "STRING", False),
        ),
        "Chunk": (
            ("id", "STRING", True),
            ("position", "INT64", False),
            ("content", "STRING", False),
            ("content_hash", "STRING", False),
        ),
        "Asset": (
            ("id", "STRING", True),
            ("asset_type", "STRING", False),
            ("mime_type", "STRING", False),
            ("source_url", "STRING", False),
            ("local_path", "STRING", False),
            ("external_id", "STRING", False),
            ("sha256", "STRING", False),
            ("position", "INT64", False),
            ("caption", "STRING", False),
            ("llm_description", "STRING", False),
            ("ocr_text", "STRING", False),
        ),
        "Embedding": (
            ("id", "STRING", True),
            ("provider", "STRING", False),
            ("model", "STRING", False),
            ("model_version", "STRING", False),
            ("purpose", "STRING", False),
            ("dimension", "INT64", False),
            ("vector", "FLOAT_VECTOR", False),
            ("created_at", "TIMESTAMP", False),
            ("is_current", "BOOLEAN", False),
        ),
        "Role": (("id", "STRING", True), ("name", "STRING", False)),
        "Process": (
            ("id", "STRING", True),
            ("name", "STRING", False),
            ("status", "STRING", False),
            ("owner", "STRING", False),
        ),
        "Status": (
            ("id", "STRING", True),
            ("name", "STRING", False),
            ("description", "STRING", False),
        ),
    }
    assert all(
        spec.headers == tuple(prop.name for prop in spec.properties)
        for spec in NODE_SPECS
    )
    embedding = next(spec for spec in NODE_SPECS if spec.table_name == "Embedding")
    vector = next(prop for prop in embedding.properties if prop.name == "vector")
    assert vector.vector_dimension_field == "dimension"


def test_relation_headers_derive_from_serialization_aliases() -> None:
    assert all(spec.headers == ("from", "to") for spec in RELATION_SPECS)
    assert all(
        not prop.primary_key for spec in RELATION_SPECS for prop in spec.properties
    )


def test_registry_validation_is_complete_and_excludes_application_models() -> None:
    assert registry_errors() == ()
    assert_registry_complete()
    registered = {spec.model for spec in NODE_SPECS}
    assert SubjectRef not in registered
    assert VectorQuery not in registered


def test_registry_negative_helper_detects_bad_property_without_global_mutation() -> (
    None
):
    source = NODE_SPECS[0]
    bad_properties = (
        PropertySpec(name="id", scalar_type=ScalarType.STRING, primary_key=False),
        PropertySpec(name="wrong", scalar_type=ScalarType.BOOLEAN),
    )
    bad_source: NodeSpec = replace(source, properties=bad_properties)
    errors = registry_errors((bad_source, *NODE_SPECS[1:]), RELATION_SPECS)
    assert any("property names/order" in error for error in errors)
    assert any("primary key" in error for error in errors)
    assert any("property types" in error for error in errors)
    assert NODE_SPECS[0] is source


def test_registry_negative_helper_detects_wrong_existing_endpoint() -> None:
    relation = RELATION_SPECS[0]
    wrong_direction = replace(relation, target_label="Role")
    errors = registry_errors(NODE_SPECS, (wrong_direction, *RELATION_SPECS[1:]))
    assert any("endpoint direction" in error for error in errors)


def test_registry_specs_are_immutable() -> None:
    with pytest.raises(FrozenInstanceError):
        NODE_SPECS[0].table_name = "Document"  # type: ignore[misc]
