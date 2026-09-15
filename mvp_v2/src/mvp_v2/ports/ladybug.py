from __future__ import annotations

from pathlib import Path
from typing import Protocol

from mvp_v2.application.dto.retrieval import VectorHit, VectorQuery


class QueryResult(Protocol):
    def has_next(self) -> bool: ...
    def get_next(self) -> list[object]: ...
    def close(self) -> None: ...


class GraphConnection(Protocol):
    def execute(
        self, query: str, parameters: dict[str, object] | None = None
    ) -> QueryResult: ...
    def close(self) -> None: ...


class GraphDatabase(Protocol):
    def connect(self) -> GraphConnection: ...
    def close(self) -> None: ...


class LadybugFactory(Protocol):
    def open(self, path: Path) -> GraphDatabase: ...


class AnnPort(Protocol):
    def query(self, request: VectorQuery) -> tuple[VectorHit, ...]: ...


class ReplacementBuildError(RuntimeError):
    """An expected replacement database build failure."""
