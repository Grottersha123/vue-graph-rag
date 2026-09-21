from __future__ import annotations

import json
from pathlib import Path

import pytest

from mvp_v2.adapters.csv.snapshot import read_snapshot, write_snapshot
from mvp_v2.domain import graph
from mvp_v2.domain.field_extensions import FieldExtensionError, load_field_extensions
from mvp_v2.domain.invariants import validate_graph
from mvp_v2.domain.schema_registry import (
    RELATION_SPECS,
    NodeSpec,
    RelationSpec,
    build_node_specs,
)
from tests.factories import build_valid_snapshot


def _write_config(path: Path, nodes: object) -> Path:
    path.write_text(json.dumps({"version": 1, "nodes": nodes}), encoding="utf-8")
    return path


def test_declared_optional_field_flattens_into_csv_and_round_trips(
    tmp_path: Path,
) -> None:
    config = _write_config(
        tmp_path / "fields.json",
        {"Document": [{"name": "review_note", "type": "STRING", "default": ""}]},
    )
    node_specs = build_node_specs(config)
    table_specs: tuple[NodeSpec | RelationSpec, ...] = (
        *node_specs,
        *RELATION_SPECS,
    )
    snapshot = build_valid_snapshot()
    document = snapshot.documents[0].model_copy(
        update={
            "custom_properties": (
                graph.CustomProperty(name="review_note", value="check"),
            )
        }
    )
    snapshot = snapshot.model_copy(update={"documents": (document,)})

    write_snapshot(tmp_path / "dump", snapshot, table_specs)
    loaded = read_snapshot(tmp_path / "dump", table_specs)

    header = (tmp_path / "dump/tables/Document.csv").read_text().splitlines()[0]
    assert header.endswith(",review_note")
    assert loaded.documents[0].custom_properties == (
        graph.CustomProperty(name="review_note", value="check"),
    )


def test_missing_configured_value_writes_and_reads_declared_default(
    tmp_path: Path,
) -> None:
    config = _write_config(
        tmp_path / "fields.json",
        {
            "Document": [
                {"name": "priority", "type": "INT64", "default": 0},
                {"name": "reviewed", "type": "BOOLEAN", "default": False},
            ]
        },
    )
    node_specs = build_node_specs(config)
    table_specs: tuple[NodeSpec | RelationSpec, ...] = (
        *node_specs,
        *RELATION_SPECS,
    )

    write_snapshot(tmp_path / "dump", build_valid_snapshot(), table_specs)
    loaded = read_snapshot(tmp_path / "dump", table_specs)

    assert loaded.documents[0].custom_properties == (
        graph.CustomProperty(name="priority", value=0),
        graph.CustomProperty(name="reviewed", value=False),
    )


@pytest.mark.parametrize(
    "nodes",
    (
        {"Unknown": []},
        {"Document": [{"name": "summary", "type": "STRING", "default": ""}]},
        {"Document": [{"name": "vector", "type": "FLOAT_VECTOR", "default": []}]},
    ),
)
def test_config_rejects_unknown_tables_collisions_and_unbounded_vectors(
    tmp_path: Path, nodes: object
) -> None:
    config = _write_config(tmp_path / "fields.json", nodes)

    with pytest.raises((FieldExtensionError, ValueError)):
        build_node_specs(config)


def test_config_parser_rejects_unknown_keys(tmp_path: Path) -> None:
    config = tmp_path / "fields.json"
    config.write_text('{"version":1,"nodes":{},"unexpected":true}', encoding="utf-8")

    with pytest.raises(FieldExtensionError):
        load_field_extensions(config)


def test_graph_validation_rejects_undeclared_custom_property() -> None:
    snapshot = build_valid_snapshot()
    document = snapshot.documents[0].model_copy(
        update={
            "custom_properties": (
                graph.CustomProperty(name="typo_field", value="value"),
            )
        }
    )

    result = validate_graph(snapshot.model_copy(update={"documents": (document,)}))

    assert any(item.code == "UNDECLARED_CUSTOM_PROPERTY" for item in result.errors)
