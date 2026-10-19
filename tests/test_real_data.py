"""Integration tests against the real CSVs in data/.

Marked `integration` and excluded from the default run: they need the ~120 MB
dataset present. They exist to catch a changed or truncated dataset, since
every number in docs/data_model.md is derived from it.

Run with: uv run pytest -m integration
"""

from pathlib import Path

import pytest

from twyd.db import connect, run_query
from twyd.ingest import ingest

DATA_DIR = Path(__file__).parent.parent / "data"

ROW_COUNTS = {
    "customers": 99_441,
    "geolocation": 1_000_163,
    "order_items": 112_650,
    "order_payments": 103_886,
    "order_reviews": 99_224,
    "orders": 99_441,
    "products": 32_951,
    "sellers": 3_095,
    "product_category_translation": 71,
}

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def real_db(tmp_path_factory) -> Path:
    if not DATA_DIR.is_dir():
        pytest.skip(f"{DATA_DIR} not present")
    db_path = tmp_path_factory.mktemp("real") / "olist.duckdb"
    ingest(DATA_DIR, db_path)
    return db_path


@pytest.fixture(scope="module")
def conn(real_db):
    with connect(real_db) as c:
        yield c


def test_row_counts_match_the_documented_data_model(real_db):
    with connect(real_db) as conn:
        actual = {
            t: run_query(conn, f"SELECT count(*) FROM {t}").rows[0][0] for t in ROW_COUNTS
        }
    assert actual == ROW_COUNTS


def test_ingestion_is_reproducible(tmp_path):
    """Two independent ingestions of the same CSVs produce the same counts."""
    if not DATA_DIR.is_dir():
        pytest.skip(f"{DATA_DIR} not present")
    assert ingest(DATA_DIR, tmp_path / "a.duckdb") == ingest(DATA_DIR, tmp_path / "b.duckdb")


# The claims in docs/data_model.md, each asserted against the real data.


def test_customer_unique_id_count(conn):
    assert run_query(
        conn,
        "SELECT count(DISTINCT customer_id), count(DISTINCT customer_unique_id) FROM customers",
    ).rows == [(99_441, 96_096)]


def test_review_id_is_not_unique(conn):
    assert run_query(
        conn,
        "SELECT count(*), count(DISTINCT review_id), count(DISTINCT order_id) FROM order_reviews",
    ).rows == [(99_224, 98_410, 98_673)]


def test_late_on_time_and_undecidable_order_counts(conn):
    assert run_query(
        conn,
        """SELECT
             count(*) FILTER (WHERE order_delivered_customer_date > order_estimated_delivery_date),
             count(*) FILTER (WHERE order_delivered_customer_date <= order_estimated_delivery_date),
             count(*) FILTER (WHERE order_delivered_customer_date IS NULL)
           FROM orders""",
    ).rows == [(7_827, 88_649, 2_965)]


def test_some_delivered_orders_have_no_delivery_date(conn):
    """A data-quality contradiction worth remembering: trust the timestamp."""
    assert run_query(
        conn,
        "SELECT count(*) FROM orders "
        "WHERE order_status = 'delivered' AND order_delivered_customer_date IS NULL",
    ).rows == [(8,)]


def test_naive_join_distorts_average_review_score(conn):
    """The documented 4.1269 vs 4.1741 discrepancy for SP customers."""
    naive = run_query(
        conn,
        "SELECT round(avg(r.review_score), 4) FROM orders o "
        "JOIN customers c USING (customer_id) "
        "JOIN order_reviews r USING (order_id) "
        "JOIN order_items i USING (order_id) WHERE c.customer_state = 'SP'",
    ).rows[0][0]
    correct = run_query(
        conn,
        "SELECT round(avg(score), 4) FROM ("
        "  SELECT o.order_id, avg(r.review_score) AS score FROM orders o"
        "  JOIN customers c USING (customer_id)"
        "  JOIN order_reviews r USING (order_id)"
        "  WHERE c.customer_state = 'SP' GROUP BY 1)",
    ).rows[0][0]
    assert float(naive) == 4.1269
    assert float(correct) == 4.1741


def test_orders_can_be_missing_reviews_items_or_payments(conn):
    assert run_query(
        conn,
        """SELECT
             (SELECT count(*) FROM orders o WHERE NOT EXISTS
                (SELECT 1 FROM order_reviews r WHERE r.order_id = o.order_id)),
             (SELECT count(*) FROM orders o WHERE NOT EXISTS
                (SELECT 1 FROM order_items i WHERE i.order_id = o.order_id)),
             (SELECT count(*) FROM orders o WHERE NOT EXISTS
                (SELECT 1 FROM order_payments p WHERE p.order_id = o.order_id))""",
    ).rows == [(768, 775, 1)]


def test_geolocation_is_denormalised_not_a_zip_lookup(conn):
    assert run_query(
        conn,
        "SELECT count(*), count(DISTINCT geolocation_zip_code_prefix) FROM geolocation",
    ).rows == [(1_000_163, 19_015)]
