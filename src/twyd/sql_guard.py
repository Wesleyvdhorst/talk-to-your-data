"""Treat model-generated SQL as untrusted input.

The engine (db.connect) is the first safety layer: read_only blocks writes and
enable_external_access blocks the host. This is the second, independent one.
It exists because the engine cannot reject multi-statement SQL - DuckDB runs
`SELECT 1; SELECT 2` happily and returns only the last result.

Deliberately not a keyword blocklist: DuckDB's own parser classifies the
statement, so `CREATE TABLE evil AS SELECT 1` is rejected for being a CREATE,
not for containing a word.
"""

import duckdb

# Functions that reach the filesystem or the network. The engine already
# refuses these (tests/test_db.py proves it), so this list is redundant on
# purpose: the validator must also be correct when read in isolation.
_FORBIDDEN_FUNCTIONS = (
    "read_csv",
    "read_csv_auto",
    "read_parquet",
    "read_json",
    "read_json_auto",
    "read_text",
    "read_blob",
    "glob",
    "parquet_scan",
    "sniff_csv",
)


class UnsafeSQL(ValueError):
    """The SQL is not a single read-only query. Never raised silently."""


def validate(sql: str) -> str:
    """Return sql unchanged if it is one read-only SELECT/WITH, else raise.

    A parse failure raises UnsafeSQL too: SQL we cannot classify is SQL we
    cannot vouch for.
    """
    if not sql or not sql.strip():
        raise UnsafeSQL("empty SQL")

    try:
        statements = duckdb.extract_statements(sql)
    except duckdb.Error as exc:
        raise UnsafeSQL(f"SQL does not parse: {exc}") from exc

    if len(statements) != 1:
        kinds = ", ".join(s.type.name for s in statements)
        raise UnsafeSQL(
            f"expected a single statement, got {len(statements)} ({kinds}). "
            "Multi-statement SQL is never executed."
        )

    kind = statements[0].type
    # == not `is`: DuckDB's pybind enum members are not interned singletons.
    if kind != duckdb.StatementType.SELECT:
        raise UnsafeSQL(
            f"only SELECT/WITH queries are allowed, this is a {kind.name} statement"
        )

    # Crude but sufficient: a bare word match on the stripped statement. False
    # positives (a column literally named read_csv) do not exist in this
    # database, and rejecting too much here is safe.
    lowered = statements[0].query.lower()
    for name in _FORBIDDEN_FUNCTIONS:
        if f"{name}(" in lowered.replace(" (", "("):
            raise UnsafeSQL(f"{name}() reaches outside the database and is not allowed")

    return sql
