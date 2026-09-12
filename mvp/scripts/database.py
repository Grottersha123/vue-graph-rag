"""LadybugDB schema creation and CSV loading for the MVP."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

try:
    from .schemas import CSV_TABLES, NODE_COLUMNS, NODE_TABLES, REL_TABLES
except ImportError:  # Direct execution: python mvp/scripts/database.py
    from schemas import CSV_TABLES, NODE_COLUMNS, NODE_TABLES, REL_TABLES


class DatabaseError(RuntimeError):
    """A user-actionable database creation error."""


class GraphLoader:
    """Create a fresh LadybugDB database and load the canonical CSV files."""

    def __init__(self, data_dir: Path, database_path: Path):
        self.data_dir = data_dir.expanduser().resolve()
        self.database_path = database_path.expanduser().resolve()
        if self.database_path == self.data_dir or self.database_path.is_dir():
            raise DatabaseError(
                "database_path must be a database file, not a directory: "
                f"{self.database_path}"
            )

    def recreate(self) -> None:
        try:
            import ladybug as lbug
        except ImportError as exc:
            raise DatabaseError(
                "ladybug is required to create the graph database"
            ) from exc
        self._remove_existing_database()
        db = lbug.Database(database_path=str(self.database_path))

        conn = lbug.Connection(db)
        for query in NODE_TABLES.values():
            conn.execute(query)
        for query in REL_TABLES.values():
            conn.execute(query)
        for table in CSV_TABLES:
            path = (self.data_dir / f"{table}.csv").resolve()
            if not path.exists():
                continue
            escaped = str(path).replace("'", "''")
            conn.execute(f"COPY {table} FROM '{escaped}' (HEADER=true)")

    def _remove_existing_database(self) -> None:
        for path in (self.database_path, Path(str(self.database_path) + ".wal")):
            print(path)
            if path.is_dir():
                raise DatabaseError(
                    "Refusing to remove a database directory: " f"{path}"
                )
            if path.exists():
                path.unlink()

    def sync(self) -> None:
        """Upsert the canonical CSV graph without dropping unrelated tables."""
        try:
            import ladybug as lbug
        except ImportError as exc:
            raise DatabaseError(
                "ladybug is required to synchronize the graph database"
            ) from exc
        if not self.database_path.exists():
            self.recreate()
            return
        db = lbug.Database(database_path=str(self.database_path))
        conn = lbug.Connection(db)
        for query in NODE_TABLES.values():
            conn.execute(
                query.replace("CREATE NODE TABLE ", "CREATE NODE TABLE IF NOT EXISTS ")
            )
        for query in REL_TABLES.values():
            conn.execute(
                query.replace("CREATE REL TABLE ", "CREATE REL TABLE IF NOT EXISTS ")
            )
        for table in REL_TABLES:
            conn.execute(f"MATCH ()-[r:{table}]->() DELETE r")
        for table, columns in self._node_columns().items():
            for row in self._rows(table):
                self._upsert_node(conn, table, columns, row)
        for table, (
            source_table,
            target_table,
        ) in self._relationship_endpoints().items():
            for row in self._rows(table):
                source_id = self._quote(row.get("from", ""))
                target_id = self._quote(row.get("to", ""))
                conn.execute(
                    f"MATCH (a:{source_table} {{id:{source_id}}}), "
                    f"(b:{target_table} {{id:{target_id}}}) "
                    f"MERGE (a)-[:{table}]->(b)"
                )

    def delete_documents(self, document_ids: set[str]) -> None:
        """Delete selected documents and owned versions/assets in-place."""
        if not document_ids or not self.database_path.exists():
            return
        try:
            import ladybug as lbug
        except ImportError as exc:
            raise DatabaseError(
                "ladybug is required to delete graph documents"
            ) from exc
        db = lbug.Database(database_path=str(self.database_path))
        conn = lbug.Connection(db)
        for document_id in document_ids:
            conn.execute(
                "MATCH (d:Document {id:" + self._quote(document_id) + "}) "
                "OPTIONAL MATCH (d)-[:HAS_VERSION]->(v:DocumentVersion) "
                "OPTIONAL MATCH (v)-[:HAS_ASSET]->(a:Asset) "
                "DETACH DELETE d,v,a"
            )

    def _rows(self, table: str) -> list[dict[str, str]]:
        path = self.data_dir / f"{table}.csv"
        if not path.exists():
            return []
        with path.open("r", encoding="utf-8", newline="") as handle:
            return list(csv.DictReader(handle))

    @staticmethod
    def _quote(value: str) -> str:
        """Quote a Cypher string literal using Ladybug's escape syntax."""
        escaped = value.replace("\\", "\\\\").replace("'", "\\'")
        return "'" + escaped + "'"

    @staticmethod
    def _node_columns() -> dict[str, tuple[str, ...]]:
        """Return CSV columns derived from the canonical Pydantic models."""
        return NODE_COLUMNS

    @staticmethod
    def _relationship_endpoints() -> dict[str, tuple[str, str]]:
        return {
            "HAS_DOCUMENT": ("SourceType", "Document"),
            "HAS_VERSION": ("Document", "DocumentVersion"),
            "HAS_STATUS": ("DocumentVersion", "Status"),
            "HAS_ERROR": ("Document", "Status"),
            "LINKS_TO": ("Document", "Document"),
            "HAS_ASSET": ("DocumentVersion", "Asset"),
            "FOR_ROLE": ("Document", "Role"),
        }

    def _upsert_node(
        self, conn: object, table: str, columns: tuple[str, ...], row: dict[str, str]
    ) -> None:
        identity = self._quote(row["id"])
        assignments: list[str] = []
        for column in columns:
            if column == "id":
                continue
            value = row.get(column, "")
            if column in {"version", "position"}:
                literal = value or "0"
            elif column in {"is_current", "is_active"}:
                literal = "true" if value.lower() == "true" else "false"
            elif column == "created_at":
                literal = f"TIMESTAMP({self._quote(value)})"
            else:
                literal = self._quote(value)
            assignments.append(f"n.{column}={literal}")
        query = f"MERGE (n:{table} {{id:{identity}}})"
        if assignments:
            query += " SET " + ",".join(assignments)
        conn.execute(query)

    def rebuild(self) -> None:
        """Backward-compatible alias for :meth:`recreate`."""
        self.recreate()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    default_root = Path(__file__).resolve().parents[2]
    parser.add_argument(
        "--data-dir", type=Path, default=default_root / "mvp" / "mvp_data"
    )
    parser.add_argument("--database-path", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    database_path = args.database_path or args.data_dir / "vue_rag.lbdb"
    try:
        GraphLoader(args.data_dir, database_path).recreate()
    except Exception as exc:
        raise SystemExit(
            f"error: database recreate failed: {type(exc).__name__}: {exc}"
        ) from exc
    print(f"LadybugDB recreated at {database_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
