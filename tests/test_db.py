"""All SQL here is hand-written. No LLM is involved in this milestone."""

import duckdb
import pytest

from twyd.db import connect, run_query


def test_connect_rejects_a_missing_database(tmp_path):
    with pytest.raises(FileNotFoundError, match="Run ingest"):
        connect(tmp_path / "nope.duckdb")


def test_count(conn):
    result = run_query(conn, "SELECT count(*) AS n FROM orders")
    assert result.columns == ["n"]
    assert result.rows == [(4,)]
    assert result.row_count == 1


def test_group_by(conn):
    result = run_query(
        conn,
        "SELECT order_status, count(*) AS n FROM orders "
        "GROUP BY order_status ORDER BY order_status",
    )
    assert result.rows == [("delivered", 3), ("shipped", 1)]


def test_join(conn):
    result = run_query(
        conn,
        "SELECT o.order_id, c.customer_state FROM orders o "
        "JOIN customers c USING (customer_id) ORDER BY o.order_id",
    )
    assert result.rows == [
        ("ord_1", "SP"),
        ("ord_2", "SP"),
        ("ord_3", "RJ"),
        ("ord_4", "MG"),
    ]


def test_empty_result_is_a_success_not_a_failure(conn):
    result = run_query(conn, "SELECT * FROM orders WHERE order_status = 'nonexistent'")
    assert result.row_count == 0
    assert result.rows == []
    assert result.columns  # the shape of the result is still known


def test_invalid_sql_raises(conn):
    with pytest.raises(duckdb.Error):
        run_query(conn, "SELECT * FROM table_that_does_not_exist")


def test_syntax_error_raises(conn):
    with pytest.raises(duckdb.Error):
        run_query(conn, "SELCT 1")


@pytest.mark.parametrize(
    "sql",
    [
        "CREATE TABLE evil (x INT)",
        "DROP TABLE orders",
        "DELETE FROM orders",
        "UPDATE orders SET order_status = 'x'",
        "INSERT INTO orders (order_id) VALUES ('x')",
    ],
)
def test_the_read_only_connection_rejects_writes(conn, sql):
    """The engine refuses writes, independently of any validator we add later."""
    with pytest.raises(duckdb.Error):
        run_query(conn, sql)


# --- The semantic traps from CLAUDE.md, proven against constructed data. ---


def test_customer_id_is_per_order_but_customer_unique_id_is_per_person(conn):
    result = run_query(
        conn,
        "SELECT count(DISTINCT customer_id), count(DISTINCT customer_unique_id) "
        "FROM customers",
    )
    assert result.rows == [(4, 3)]  # 4 order-level ids, 3 real people


def test_joining_reviews_multiplies_order_rows(conn):
    naive = run_query(
        conn, "SELECT count(*) FROM orders JOIN order_reviews USING (order_id)"
    )
    assert naive.rows == [(5,)]  # 4 orders become 5 rows: ord_2 has two reviews

    correct = run_query(
        conn,
        "SELECT count(DISTINCT order_id) FROM orders JOIN order_reviews USING (order_id)",
    )
    assert correct.rows == [(4,)]


def test_joining_items_and_payments_inflates_counts_further(conn):
    result = run_query(
        conn,
        "SELECT count(*) FROM orders "
        "JOIN order_items USING (order_id) JOIN order_payments USING (order_id)",
    )
    # 4 orders become 7 rows: ord_1 3x1, ord_2 1x2, ord_3 1x1, ord_4 1x1.
    assert result.rows == [(7,)]


def test_late_delivery_excludes_undelivered_orders(conn):
    late = run_query(
        conn,
        "SELECT order_id FROM orders "
        "WHERE order_delivered_customer_date > order_estimated_delivery_date",
    )
    assert late.rows == [("ord_2",)]

    # ord_3 is undelivered: its null date is neither late nor on time.
    undelivered = run_query(
        conn, "SELECT count(*) FROM orders WHERE order_delivered_customer_date IS NULL"
    )
    assert undelivered.rows == [(1,)]
