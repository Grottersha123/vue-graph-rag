from __future__ import annotations

from pathlib import Path
from threading import RLock
from types import SimpleNamespace

import pytest

from mvp_v2.application.json_encoding import canonical_json_bytes
from mvp_v2.backend.config import GatewaySettings
from mvp_v2.backend.runtime import (
    LadybugDatabaseProbe,
    build_runtime,
    load_database_manifest,
    validate_database_contents,
)
from tests.backend.helpers import manifest


def _artifact(root: Path) -> Path:
    database_manifest = manifest()
    artifact = root / f"{database_manifest.database_name}.lbdb"
    artifact.mkdir()
    (artifact / "database.lbug").write_bytes(b"immutable")
    (artifact / "_mvp_v2_manifest.json").write_bytes(
        canonical_json_bytes(database_manifest.canonical_payload())
    )
    return artifact


def test_manifest_name_must_match_explicit_database_selection(tmp_path: Path) -> None:
    artifact = _artifact(tmp_path)

    assert (
        load_database_manifest(artifact, "gateway-test").database_name == "gateway-test"
    )
    with pytest.raises(ValueError, match="does not match"):
        load_database_manifest(artifact, "other")


class _ProbeResult:
    def __init__(self, row: list[object] | None) -> None:
        self._row = row

    def has_next(self) -> bool:
        return self._row is not None

    def get_next(self) -> list[object]:
        assert self._row is not None
        row = self._row
        self._row = None
        return row


class _ProbeConnection:
    def __init__(self, row: list[object] | None = None, *, fails: bool = False) -> None:
        self._row = row
        self._fails = fails

    def execute(self, query: str) -> _ProbeResult:
        if self._fails:
            raise RuntimeError("database unavailable")
        assert query == "RETURN 1"
        return _ProbeResult(self._row)


def test_ladybug_readiness_uses_live_safe_query() -> None:
    assert LadybugDatabaseProbe(_ProbeConnection([1]), RLock()).ready() is True
    assert LadybugDatabaseProbe(_ProbeConnection([2]), RLock()).ready() is False
    assert LadybugDatabaseProbe(_ProbeConnection(fails=True), RLock()).ready() is False


class _Database:
    calls: list[tuple[str, bool]] = []

    def __init__(self, path: str, read_only: bool = False) -> None:
        self.calls.append((path, read_only))

    def close(self) -> None:
        pass


class _Connection:
    def __init__(self, database: _Database) -> None:
        self.database = database

    def execute(self, query: str) -> _ProbeResult:
        if "RETURN count(" in query:
            return _ProbeResult([0])
        if "RETURN DISTINCT e.provider" in query:
            return _ProbeResult(None)
        raise AssertionError(f"unexpected query: {query}")

    def close(self) -> None:
        pass


class _Redis:
    def close(self) -> None:
        pass

    def ping(self) -> bool:
        return True

    def get(self, key: str) -> None:
        return None

    def set(self, key: str, value: str) -> bool:
        return True


class _MismatchedDatabaseConnection:
    def execute(self, query: str) -> _ProbeResult:
        if "RETURN count(" in query:
            return _ProbeResult([1])
        return _ProbeResult(None)


def test_runtime_rejects_database_contents_that_do_not_match_manifest() -> None:
    with pytest.raises(ValueError, match="row counts"):
        validate_database_contents(_MismatchedDatabaseConnection(), manifest())


def test_runtime_opens_selected_artifact_read_only_without_dump_dependency(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifact = _artifact(tmp_path)
    logs = tmp_path / "logs"
    home = tmp_path / "ladybug-home"
    settings = GatewaySettings(
        database_name="gateway-test",
        databases_root=tmp_path,
        audit_directory=logs,
        redis_url="redis://127.0.0.1:6379/0",
        ollama_base_url="http://127.0.0.1:11434",
        ollama_allowed_lan_hosts=frozenset(),
        chat_model="chat",
        chat_model_version="v1",
        backend_token="x" * 32,
        allowed_user_ids=frozenset({42}),
    )
    fake_module = SimpleNamespace(Database=_Database, Connection=_Connection)
    monkeypatch.setenv("MVP_V2_LADYBUG_HOME", str(home))
    monkeypatch.setattr(
        "mvp_v2.backend.runtime.redis.Redis.from_url",
        lambda *args, **kwargs: _Redis(),
    )
    monkeypatch.setattr(
        "mvp_v2.backend.runtime.importlib.import_module", lambda name: fake_module
    )

    runtime = build_runtime(settings)
    runtime.close()

    assert _Database.calls[-1] == (str(artifact / "database.lbug"), True)
    assert not (tmp_path / "dumps").exists()
    assert {path.name for path in logs.iterdir()} == {
        "backend.jsonl",
        "errors.jsonl",
        "feedback.jsonl",
        "prompt_traces.jsonl",
    }
