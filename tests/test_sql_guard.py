"""The validator is the second safety layer and must hold on its own."""

import pytest

from twyd.sql_guard import UnsafeSQL, validate

ALLOWED = [
    "SELECT 1",
    "SELECT count(*) FROM orders",
    "SELECT order_status, count(*) FROM orders GROUP BY order_status",
    "WITH per_order AS (SELECT order_id, sum(price) AS total FROM order_items "
    "GROUP BY order_id) SELECT avg(total) FROM per_order",
    "SELECT o.order_id FROM orders o LEFT JOIN order_reviews r USING (order_id)",
    "SELECT * FROM orders WHERE order_status = 'nonexistent'",
    "  select 1  ",  # leading space and lowercase are fine
    "SELECT 1 -- a trailing comment\n",
    "SELECT 1;",  # a single trailing semicolon is still one statement
    # DuckDB parses read-only metadata statements as SELECT. They return a
    # result set and change nothing, so letting them through is correct; the
    # side-effecting spellings parse as SET/PRAGMA and are rejected below.
    "DESCRIBE orders",
    "SHOW TABLES",
    # CTE names look like table references at their use site.
    "WITH orders_per_state AS (SELECT customer_state AS s FROM customers) "
    "SELECT s, count(*) FROM orders_per_state GROUP BY s",
    "WITH a AS (SELECT order_id FROM orders), b AS (SELECT order_id FROM a) "
    "SELECT count(*) FROM b",
    "SELECT (SELECT count(*) FROM order_items) AS n",
]


@pytest.mark.parametrize("sql", ALLOWED)
def test_single_read_only_queries_are_allowed(sql):
    assert validate(sql) == sql


REJECTED = [
    # The gap the engine cannot close: both run, only the last is returned.
    "SELECT 1; SELECT 2",
    "SELECT 1; DROP TABLE orders",
    "SELECT 1; CREATE TABLE evil (x INT)",
    # Writes and DDL, rejected by parsed statement type, not by keyword.
    "CREATE TABLE evil AS SELECT 1",
    "CREATE OR REPLACE TABLE orders AS SELECT 1",
    "DROP TABLE orders",
    "DELETE FROM orders",
    "UPDATE orders SET order_status = 'x'",
    "INSERT INTO orders (order_id) VALUES ('x')",
    "ATTACH 'other.duckdb'",
    "INSTALL httpfs",
    "SET threads = 2",
    "PRAGMA threads = 4",
    "PRAGMA enable_external_access = true",
    "PRAGMA disable_verification",
    "COPY orders TO '/tmp/leak.csv'",
    "EXPLAIN SELECT 1",  # not a result we can explain to a user
    # PRAGMA database_list parses as SELECT, but the serialiser rewrites it
    # into a pragma_database_list() reference, so the allowlist rejects it.
    # Fine: no question about the data needs it.
    "PRAGMA database_list",
    # Host access, blocked here as well as by the engine.
    "SELECT * FROM read_csv('/etc/passwd')",
    "SELECT * FROM read_parquet('/tmp/x.parquet')",
    "SELECT * FROM glob('/etc/*')",
    # A bare string IS a file reference in DuckDB. No function name appears
    # anywhere in this query, which is exactly what a denylist cannot see.
    "SELECT * FROM 'secrets.csv'",
    "SELECT * FROM '/etc/passwd'",
    "SELECT * FROM 's3://bucket/x.parquet'",
    # Tables that exist but are not ours, and internal catalogs.
    "SELECT * FROM information_schema.tables",
    "SELECT * FROM duckdb_settings()",
    "SELECT * FROM range(5)",
    "SELECT * FROM sqlite_master",
    # Nothing we cannot classify gets through.
    "SELCT 1",
    "SELECT * FROM",
    "",
    "   ",
]


@pytest.mark.parametrize("sql", REJECTED)
def test_everything_else_is_rejected(sql):
    with pytest.raises(UnsafeSQL):
        validate(sql)


def test_the_rejection_says_why():
    with pytest.raises(UnsafeSQL, match="single statement"):
        validate("SELECT 1; SELECT 2")
    with pytest.raises(UnsafeSQL, match="DROP"):
        validate("DROP TABLE orders")
    with pytest.raises(UnsafeSQL, match="does not parse"):
        validate("SELCT 1")


def test_the_table_allowlist_is_the_nine_ingested_tables():
    from twyd.ingest import TABLES
    from twyd.sql_guard import ALLOWED_TABLES

    assert ALLOWED_TABLES == frozenset(TABLES)


def test_a_narrower_allowlist_can_be_passed_in():
    only_orders = frozenset({"orders"})
    assert validate("SELECT count(*) FROM orders", only_orders)
    with pytest.raises(UnsafeSQL, match="unknown table"):
        validate("SELECT count(*) FROM customers", only_orders)


def test_a_write_hidden_inside_a_select_shaped_string_is_still_a_write():
    """Keyword checks on the first word would pass this; parsing does not."""
    with pytest.raises(UnsafeSQL, match="CREATE"):
        validate("/* SELECT */ CREATE TABLE evil AS SELECT * FROM orders")
