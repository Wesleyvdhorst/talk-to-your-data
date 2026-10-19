"""Read-only query access to the DuckDB database.

Knows nothing about LLMs or prompts: it takes a SQL string and returns rows.
Failures are raised, never converted into empty results.
"""

import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb

# Cost bounds. Neither is a safety boundary - they stop an honest query from
# running away. DuckDB has no statement-timeout setting, so the timeout is a
# watchdog thread that calls conn.interrupt().
MAX_ROWS = 1000
TIMEOUT_SECONDS = 30.0


@dataclass(frozen=True)
class QueryResult:
    """The outcome of a successful query. An empty result is still a success."""

    columns: list[str]
    rows: list[tuple[Any, ...]]
    truncated: bool = False  # more rows existed than max_rows

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


def run_query(
    conn: duckdb.DuckDBPyConnection,
    sql: str,
    max_rows: int = MAX_ROWS,
    timeout_seconds: float = TIMEOUT_SECONDS,
) -> QueryResult:
    """Execute sql and return at most max_rows rows, within timeout_seconds.

    duckdb.Error propagates unchanged: its message already names the problem
    and its position, which is more useful than anything a wrapper would add.

    The row cap alone would not bound cost: `SELECT count(*)` over a cross join
    returns one row after counting a trillion, hence the timeout as well.
    """
    watchdog = threading.Timer(timeout_seconds, conn.interrupt)
    watchdog.start()
    try:
        result = conn.sql(sql)
        if result is None:
            # The statement ran but is not a query (e.g. SET). Not a rejection:
            # anything genuinely unsafe raises from DuckDB before reaching here.
            raise ValueError(f"not a query, it produced no result set: {sql!r}")
        # fetchmany streams: the cap is applied without materialising the whole
        # result, and the one extra row is how we know it truncated. The SQL is
        # left untouched, so errors still point at what the model wrote.
        columns, rows = list(result.columns), result.fetchmany(max_rows + 1)
    except duckdb.InterruptException as exc:
        raise TimeoutError(
            f"query exceeded {timeout_seconds}s and was cancelled: {sql!r}"
        ) from exc
    finally:
        watchdog.cancel()

    return QueryResult(
        columns=columns, rows=rows[:max_rows], truncated=len(rows) > max_rows
    )
