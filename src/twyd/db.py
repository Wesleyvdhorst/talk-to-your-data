"""Read-only query access to the DuckDB database.

Knows nothing about LLMs or prompts: it takes a SQL string and returns rows.
Failures are raised, never converted into empty results.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb


@dataclass(frozen=True)
class QueryResult:
    """The outcome of a successful query. An empty result is still a success."""

    columns: list[str]
    rows: list[tuple[Any, ...]]

    @property
    def row_count(self) -> int:
        return len(self.rows)


def connect(db_path: Path) -> duckdb.DuckDBPyConnection:
    """Open a read-only connection. Writes fail at the engine, not just in review."""
    if not db_path.is_file():
        raise FileNotFoundError(f"database not found: {db_path}. Run ingest() first.")
    return duckdb.connect(db_path, read_only=True)


def run_query(conn: duckdb.DuckDBPyConnection, sql: str) -> QueryResult:
    """Execute sql and return its rows.

    duckdb.Error propagates unchanged: its message already names the problem
    and its position, which is more useful than anything a wrapper would add.
    """
    result = conn.sql(sql)
    if result is None:
        raise ValueError(f"query returned no result set: {sql!r}")
    return QueryResult(columns=list(result.columns), rows=result.fetchall())
