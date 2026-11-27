from __future__ import annotations

import importlib
from collections.abc import Sequence
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Protocol, cast


class DuckCursor(Protocol):
    @property
    def description(self) -> Sequence[Sequence[object]]: ...

    def execute(
        self, query: str, parameters: Sequence[object] | None = None
    ) -> DuckCursor: ...

    def fetchall(self) -> Sequence[Sequence[object]]: ...


class DuckModule(Protocol):
    def connect(self, database: str) -> AbstractContextManager[DuckCursor]: ...


duckdb = cast(DuckModule, importlib.import_module("duckdb"))


class ChatLogAnalytics:
    def __init__(self, directory: Path) -> None:
        self._directory = directory

    def daily_status(self) -> tuple[dict[str, object], ...]:
        return self._query(
            "backend.jsonl",
            """
            SELECT CAST(date_trunc('day', event_ts) AS VARCHAR) AS day,
                   status, count(*) AS requests
            FROM events GROUP BY 1, 2 ORDER BY 1, 2
            """,
        )

    def latency(self) -> tuple[dict[str, object], ...]:
        return self._query(
            "backend.jsonl",
            """
            SELECT status, avg(latency_ms) AS average_ms,
                   quantile_cont(latency_ms, 0.95) AS p95_ms
            FROM events GROUP BY status ORDER BY status
            """,
        )

    def errors(self) -> tuple[dict[str, object], ...]:
        return self._query(
            "errors.jsonl",
            """
            SELECT component, error_code, count(*) AS errors
            FROM events GROUP BY component, error_code
            ORDER BY component, error_code
            """,
        )

    def profiles(self) -> tuple[dict[str, object], ...]:
        return self._query(
            "backend.jsonl",
            """
            SELECT profile_id, count(*) AS requests
            FROM events WHERE status = 'success'
            GROUP BY profile_id ORDER BY profile_id
            """,
        )

    def telegram_users(self) -> tuple[dict[str, object], ...]:
        return self._query(
            "backend.jsonl",
            """
            SELECT telegram_user_id, count(*) AS requests
            FROM events GROUP BY telegram_user_id ORDER BY telegram_user_id
            """,
        )

    def feedback(self) -> tuple[dict[str, object], ...]:
        feedback = self._directory / "feedback.jsonl"
        backend = self._directory / "backend.jsonl"
        if not self._usable(feedback) or not self._usable(backend):
            return ()
        with duckdb.connect(":memory:") as connection:
            self._view(connection, "feedback_events", feedback)
            self._view(connection, "backend_events", backend)
            cursor = connection.execute(
                """
                WITH grouped AS (
                    SELECT b.profile_id, f.rating, count(*) AS ratings
                    FROM feedback_events f
                    LEFT JOIN backend_events b USING (request_id)
                    GROUP BY b.profile_id, f.rating
                )
                SELECT profile_id, rating, ratings,
                       sum(rating * ratings) OVER (PARTITION BY profile_id)::DOUBLE
                       / sum(ratings) OVER (PARTITION BY profile_id) AS average_rating
                FROM grouped
                ORDER BY profile_id, rating
                """
            )
            return self._records(cursor)

    def feedback_periods(self) -> tuple[dict[str, object], ...]:
        return self._query(
            "feedback.jsonl",
            """
            WITH grouped AS (
                SELECT CAST(date_trunc('day', event_ts) AS VARCHAR) AS day,
                       rating, count(*) AS ratings
                FROM events
                GROUP BY 1, rating
            )
            SELECT day, rating, ratings,
                   sum(rating * ratings) OVER (PARTITION BY day)::DOUBLE
                   / sum(ratings) OVER (PARTITION BY day) AS average_rating
            FROM grouped
            ORDER BY day, rating
            """,
        )

    def debug_request(self, request_id: str) -> dict[str, object] | None:
        backend = self._first("backend.jsonl", request_id)
        telegram = self._all("telegram.jsonl", request_id)
        errors = self._all("errors.jsonl", request_id)
        prompt = self._last("prompt_traces.jsonl", request_id)
        if backend is None and not telegram and not errors and prompt is None:
            return None
        return {
            "request_id": request_id,
            "backend": backend,
            "telegram": telegram,
            "errors": errors,
            "prompt_trace": prompt,
            "prompt_available": prompt is not None,
        }

    def _first(self, filename: str, request_id: str) -> dict[str, object] | None:
        rows = self._filtered(filename, request_id, limit=1)
        return None if not rows else rows[0]

    def _last(self, filename: str, request_id: str) -> dict[str, object] | None:
        rows = self._filtered(filename, request_id, limit=1, descending=True)
        return None if not rows else rows[0]

    def _all(self, filename: str, request_id: str) -> tuple[dict[str, object], ...]:
        return self._filtered(filename, request_id)

    def _filtered(
        self,
        filename: str,
        request_id: str,
        *,
        limit: int | None = None,
        descending: bool = False,
    ) -> tuple[dict[str, object], ...]:
        path = self._directory / filename
        if not self._usable(path):
            return ()
        suffix = " LIMIT 1" if limit == 1 else ""
        with duckdb.connect(":memory:") as connection:
            self._view(connection, "events", path)
            columns = {
                str(row[0]) for row in connection.execute("DESCRIBE events").fetchall()
            }
            if "request_id" not in columns:
                return ()
            predicate = "request_id = ?"
            parameters = [request_id]
            if "related_request_id" in columns:
                predicate += " OR related_request_id = ?"
                parameters.append(request_id)
            direction = " DESC" if descending else ""
            cursor = connection.execute(
                f"SELECT * FROM events WHERE {predicate} ORDER BY event_ts{direction}"
                + suffix,
                parameters,
            )
            return self._records(cursor)

    def _query(self, filename: str, query: str) -> tuple[dict[str, object], ...]:
        path = self._directory / filename
        if not self._usable(path):
            return ()
        with duckdb.connect(":memory:") as connection:
            self._view(connection, "events", path)
            return self._records(connection.execute(query))

    @staticmethod
    def _view(connection: DuckCursor, name: str, path: Path) -> None:
        escaped = str(path).replace("'", "''")
        connection.execute(
            f"CREATE TEMP VIEW {name} AS SELECT * FROM "
            f"read_json_auto('{escaped}', format='newline_delimited', "
            "union_by_name=true, ignore_errors=true)"
        )

    @staticmethod
    def _records(cursor: DuckCursor) -> tuple[dict[str, object], ...]:
        names = tuple(str(item[0]) for item in cursor.description)
        return tuple(dict(zip(names, row, strict=True)) for row in cursor.fetchall())

    @staticmethod
    def _usable(path: Path) -> bool:
        return path.is_file() and not path.is_symlink() and path.stat().st_size > 0


def standard_reports(directory: Path) -> dict[str, tuple[dict[str, object], ...]]:
    analytics = ChatLogAnalytics(directory)
    return {
        "daily_status": analytics.daily_status(),
        "latency": analytics.latency(),
        "errors": analytics.errors(),
        "profiles": analytics.profiles(),
        "telegram_users": analytics.telegram_users(),
        "feedback": analytics.feedback(),
        "feedback_periods": analytics.feedback_periods(),
    }
