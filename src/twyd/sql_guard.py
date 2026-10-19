"""Treat model-generated SQL as untrusted input.

The engine (db.connect) is the first safety layer: read_only blocks writes and
enable_external_access blocks the host. This is the second, independent one.
It exists because the engine cannot reject multi-statement SQL - DuckDB runs
`SELECT 1; SELECT 2` happily and returns only the last result.

Nothing here matches keywords or substrings. Both checks use DuckDB's own
parser: `extract_statements` classifies the statement, and
`json_serialize_sql` gives the parse tree, from which we allow only the known
tables and no table functions at all. An allowlist, not a denylist - a
denylist of function names cannot see `SELECT * FROM 'secrets.csv'`, which is
a bare table reference that DuckDB resolves to a file.
"""

import json
import re

import duckdb

from twyd.ingest import TABLES

# The only tables any generated query may read. Same source as ingestion, so
# the allowlist cannot drift from what was loaded.
ALLOWED_TABLES = frozenset(TABLES)

# A CTE name must be a plain identifier. A model writing analytical SQL has no
# reason to produce anything else, while `WITH "secrets.csv" AS (...)` would
# excuse a same-named table reference. DuckDB happens to resolve that
# reference to the CTE rather than the file, so it leaks nothing today - this
# refuses to depend on that.
_PLAIN_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class UnsafeSQL(ValueError):
    """The SQL is not a single read-only query. Never raised silently."""


def validate(sql: str, allowed_tables: frozenset[str] | None = None) -> str:
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

    _check_references(statements[0].query, allowed_tables or ALLOWED_TABLES)
    return sql


def _check_references(sql: str, allowed_tables: frozenset[str]) -> None:
    """Reject anything the query reads that is not a known table.

    Serialising uses DuckDB's default in-memory connection: the SQL is parsed,
    never executed, so no data or file is touched here.
    """
    try:
        tree = json.loads(
            duckdb.execute("SELECT json_serialize_sql(?)", [sql]).fetchone()[0]
        )
    except duckdb.Error as exc:
        raise UnsafeSQL(f"SQL could not be parsed for inspection: {exc}") from exc
    if tree.get("error"):
        # Statements the serialiser does not support (PRAGMA, for one). SQL we
        # cannot inspect is SQL we cannot vouch for.
        raise UnsafeSQL(
            f"SQL could not be inspected: {tree.get('error_message', 'unsupported')}"
        )

    tables: set[tuple[str, str, str]] = set()
    functions: set[str] = set()
    cte_names: set[str] = set()
    _walk(tree, tables, functions, cte_names)

    if functions:
        raise UnsafeSQL(
            f"table functions are not allowed: {', '.join(sorted(functions))}. "
            "Only the known tables can be read."
        )

    odd_ctes = sorted(n for n in cte_names if not _PLAIN_IDENTIFIER.match(n))
    if odd_ctes:
        raise UnsafeSQL(
            f"CTE name(s) must be plain identifiers: {', '.join(odd_ctes)}"
        )

    for catalog, schema, name in sorted(tables):
        if catalog or schema not in ("", "main"):
            raise UnsafeSQL(
                f"qualified table reference not allowed: "
                f"{'.'.join(p for p in (catalog, schema, name) if p)}"
            )
        # ponytail: CTE names are matched without tracking scope, so a CTE in
        # one subquery excuses that name anywhere in the statement. Harmless
        # here - an out-of-scope name fails to bind in the engine, and a
        # file-like name is already rejected above. Add a scope stack if CTE
        # names ever stop being plain identifiers.
        if name not in allowed_tables and name not in cte_names:
            raise UnsafeSQL(
                f"unknown table reference: {name}. "
                f"Allowed: {', '.join(sorted(allowed_tables))}"
            )


def _walk(
    node: object,
    tables: set[tuple[str, str, str]],
    functions: set[str],
    ctes: set[str],
) -> None:
    """Collect every table reference, table function and CTE name in the tree."""
    if isinstance(node, dict):
        kind = node.get("type")
        if kind == "BASE_TABLE":
            # A bare string here is how DuckDB spells a file: FROM 'x.csv'.
            # Catalog and schema are kept: a CTE can never carry either, so a
            # qualified reference must never be excused by a CTE name.
            tables.add(
                (
                    str(node.get("catalog_name", "")),
                    str(node.get("schema_name", "")),
                    str(node.get("table_name", "")),
                )
            )
        elif kind == "TABLE_FUNCTION":
            function = node.get("function")
            name = function.get("function_name") if isinstance(function, dict) else None
            functions.add(str(name or "unnamed"))
        cte_map = node.get("cte_map")
        if isinstance(cte_map, dict):
            # CTE names look like table references at their use site, so they
            # have to be collected before the allowlist is applied.
            ctes.update(str(entry.get("key", "")) for entry in cte_map.get("map", []))
        for value in node.values():
            _walk(value, tables, functions, ctes)
    elif isinstance(node, list):
        for value in node:
            _walk(value, tables, functions, ctes)
