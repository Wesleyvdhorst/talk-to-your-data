"""The schema context must be correct without any LLM involved."""

from twyd.ingest import TABLES
from twyd.schema import TABLE_NOTES, schema_context


def test_every_table_and_its_columns_appear(conn):
    context = schema_context(conn)
    for table in TABLES:
        assert f"TABLE {table}" in context
    # Spot-check columns and types from two different tables.
    assert "order_delivered_customer_date TIMESTAMP" in context
    assert "customer_unique_id VARCHAR" in context


def test_every_table_has_a_semantic_note(conn):
    """A table without a note would silently ship an unexplained trap."""
    assert set(TABLE_NOTES) == set(TABLES)
    context = schema_context(conn)
    assert context.count("NOTE:") == len(TABLES)


def test_the_traps_are_stated(conn):
    context = schema_context(conn)
    assert "COUNT(DISTINCT customer_unique_id)" in context
    assert "review_id is NOT unique" in context
    assert "Aggregate the child table first" in context
    assert "LEFT JOIN" in context
    assert "Portuguese" in context


def test_relationships_are_listed(conn):
    context = schema_context(conn)
    assert "order_items.order_id -> orders.order_id" in context
    assert "one-to-MANY" in context  # the geolocation hazard


def test_a_subset_gets_only_its_own_tables_relationships_and_rules(subset_conn):
    """A relationship or rule naming a table that is not loaded invites SQL
    that cannot bind, so neither is sent."""
    context = schema_context(subset_conn)
    assert "TABLE order_items" not in context
    assert "orders.customer_id -> customers.customer_id" in context
    assert "order_reviews.order_id -> orders.order_id" in context
    for absent in ("order_items", "order_payments", "geolocation", "products"):
        assert absent not in context, absent
    assert "Joining orders to order_reviews duplicates" in context
    assert "without reviews must still be" in context


def test_a_lone_table_gets_no_empty_relationship_or_rule_sections(tmp_path):
    from pathlib import Path

    from twyd.db import connect
    from twyd.ingest import ingest

    data = tmp_path / "data"
    data.mkdir()
    name = TABLES["orders"]
    (data / name).write_bytes((Path(__file__).parent / "fixtures" / name).read_bytes())
    ingest(data, tmp_path / "t.duckdb")
    with connect(tmp_path / "t.duckdb") as c:
        context = schema_context(c)
    assert "TABLE orders" in context
    assert "RELATIONSHIPS" not in context
    assert "JOIN RULES" not in context


def test_context_is_small_enough_to_send(conn):
    """Small context is a rule, not an aspiration. ~2.5k tokens at 4 chars."""
    assert len(schema_context(conn)) < 10_000


def test_the_column_overview_is_one_line_per_table_and_nothing_else(conn):
    """What the classifier needs: which columns exist. Not types, not row
    counts, not join hazards - those are about writing the query."""
    from twyd.schema import column_overview

    overview = column_overview(conn)
    lines = overview.splitlines()
    assert len(lines) == 9  # all nine tables
    assert "customers: customer_id, customer_unique_id" in overview
    assert "orders: order_id, customer_id, order_status" in overview
    # the things it deliberately leaves out
    assert "NOTE" not in overview
    assert "JOIN RULES" not in overview
    assert "VARCHAR" not in overview
    assert "rows" not in overview
