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


# read_only blocks writes to the database. It does NOT block access to the host
# filesystem: on a plain read-only connection, COPY ... TO writes a file and
# read_csv('/etc/passwd') reads one. enable_external_access closes that, along
# with ATTACH and INSTALL. Both are engine guarantees; the milestone-2 validator
# is the second, independent layer.
_CONFIG = {"enable_external_access": "false"}


def connect(db_path: Path) -> duckdb.DuckDBPyConnection:
    """Open a connection that can neither write the database nor touch the host."""
    if not db_path.is_file():
        raise FileNotFoundError(f"database not found: {db_path}. Run ingest() first.")
    return duckdb.connect(db_path, read_only=True, config=_CONFIG)


def run_query(conn: duckdb.DuckDBPyConnection, sql: str) -> QueryResult:
    """Execute sql and return its rows.

    duckdb.Error propagates unchanged: its message already names the problem
    and its position, which is more useful than anything a wrapper would add.
    """
    result = conn.sql(sql)
    if result is None:
        # The statement ran but is not a query (e.g. SET). Not a rejection:
        # anything genuinely unsafe raises from DuckDB before reaching here.
        raise ValueError(f"not a query, it produced no result set: {sql!r}")
    return QueryResult(columns=list(result.columns), rows=result.fetchall())
