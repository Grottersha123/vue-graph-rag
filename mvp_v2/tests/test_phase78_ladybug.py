from __future__ import annotations

import importlib
import importlib.util
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from mvp_v2.adapters.ladybug import load_graph, node_ddl, relation_ddl
from mvp_v2.bootstrap.provision_vector import main
from mvp_v2.domain.schema_registry import NODE_SPECS, RELATION_SPECS
from tests.factories import NOW, build_valid_snapshot


def _all(result: Any) -> list[list[object]]:
    rows: list[list[object]] = []
    while result.has_next():
        rows.append(list(result.get_next()))
    result.close()
    return rows


def test_registry_generates_exact_ladybug_ddl_counts() -> None:
    nodes = node_ddl(2)
    relations = relation_ddl()
    assert len(nodes) == 9
    assert len(relations) == 13
    assert all(value.startswith("CREATE NODE TABLE ") for value in nodes)
    assert all(value.startswith("CREATE REL TABLE ") for value in relations)


def test_production_install_is_confined_to_provisioner() -> None:
    source_root = Path(__file__).parents[1] / "src" / "mvp_v2"
    containing = [
        path.relative_to(source_root).as_posix()
        for path in source_root.rglob("*.py")
        if "INSTALL vector" in path.read_text()
    ]
    assert containing == ["bootstrap/provision_vector.py"]


def test_provisioner_without_yes_makes_zero_calls(tmp_path: Path) -> None:
    calls: list[Path] = []
    assert main(["--home", str(tmp_path)], provisioner=calls.append) == 2
    assert calls == []


def test_provisioner_executes_install_then_load(tmp_path: Path) -> None:
    statements: list[str] = []

    class Result:
        def close(self) -> None:
            pass

    class Connection:
        def __init__(self, database: object) -> None:
            del database

        def execute(self, statement: str) -> Result:
            statements.append(statement)
            return Result()

        def close(self) -> None:
            pass

    class Database:
        def __init__(self, path: str) -> None:
            del path

        def close(self) -> None:
            pass

    module = SimpleNamespace(Database=Database, Connection=Connection)
    from mvp_v2.bootstrap.provision_vector import provision

    provision(tmp_path, module_loader=lambda name: module)
    assert statements == ["INSTALL vector", "LOAD vector"]


def test_real_ladybug_load_counts_timestamp_vector_and_reopens(tmp_path: Path) -> None:
    path = tmp_path / "graph.lbdb"
    path.mkdir()
    database_path = path / "database.lbug"
    snapshot = build_valid_snapshot()
    embeddings = tuple(
        item.model_copy(update={"summary_vector": (0.25, 0.75)})
        if item.id == "embedding-chunk"
        else item
        for item in snapshot.embeddings
    )
    snapshot = snapshot.model_copy(update={"embeddings": embeddings})
    if importlib.util.find_spec("ladybug") is None:
        pytest.skip("ladybug 0.19.1 is unavailable in this test interpreter")
    ladybug = importlib.import_module("ladybug")
    database = ladybug.Database(str(database_path))
    connection = ladybug.Connection(database)
    load_graph(connection, snapshot, 2)
    assert _all(connection.execute("MATCH (n) RETURN count(n)")) == [[10]]
    assert _all(connection.execute("MATCH ()-[r]->() RETURN count(r)")) == [[10]]
    for spec in NODE_SPECS:
        expected = len(getattr(snapshot, spec.snapshot_field))
        assert _all(connection.execute(f"MATCH (n:{spec.label}) RETURN count(n)")) == [
            [expected]
        ]
    for relation_spec in RELATION_SPECS:
        expected = len(getattr(snapshot, relation_spec.snapshot_field))
        query = f"MATCH ()-[r:{relation_spec.relation_type}]->() RETURN count(r)"
        assert _all(connection.execute(query)) == [[expected]]
    row = _all(
        connection.execute(
            "MATCH (e:Embedding {id: 'embedding-chunk'}) "
            "RETURN e.created_at, e.vector, e.summary_vector"
        )
    )[0]
    assert isinstance(row[0], datetime)
    assert row[0].tzinfo is None
    assert list(cast(Iterable[object], row[1])) == [1.0, 2.5]
    assert list(cast(Iterable[object], row[2])) == [0.25, 0.75]
    asset_summary = _all(
        connection.execute(
            "MATCH (e:Embedding {id: 'embedding-asset'}) RETURN e.summary_vector"
        )
    )[0][0]
    assert asset_summary is None
    connection.close()
    database.close()
    (path / "_mvp_v2_manifest.json").write_bytes(b"{}")
    reopened = ladybug.Database(str(database_path))
    reopened_connection = ladybug.Connection(reopened)
    assert _all(reopened_connection.execute("MATCH (n) RETURN count(n)")) == [[10]]
    reopened_connection.close()
    reopened.close()
    assert NOW.tzinfo is not None
