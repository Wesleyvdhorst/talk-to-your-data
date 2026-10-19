from pathlib import Path

import duckdb
import pytest

from twyd.ingest import TABLES, ingest

FIXTURE_DIR = Path(__file__).parent / "fixtures"

# Hand-counted from the fixture CSVs. The traps are deliberate: ord_2 has two
# reviews, ord_1 has three items, ord_2 has two payments, and cust_1/cust_2
# are the same person (uniq_A).
FIXTURE_COUNTS = {
    "customers": 4,
    "geolocation": 3,
    "order_items": 6,
    "order_payments": 5,
    "order_reviews": 5,
    "orders": 4,
    "products": 3,
    "sellers": 2,
    "product_category_translation": 2,
}


def test_ingest_creates_every_table_with_expected_row_counts(tmp_path):
    counts = ingest(FIXTURE_DIR, tmp_path / "t.duckdb")
    assert counts == FIXTURE_COUNTS
    assert set(counts) == set(TABLES)


def test_ingest_is_idempotent(tmp_path):
    db = tmp_path / "t.duckdb"
    assert ingest(FIXTURE_DIR, db) == ingest(FIXTURE_DIR, db)


def test_ingest_raises_when_a_csv_is_missing(tmp_path):
    with pytest.raises(FileNotFoundError, match="missing CSV files"):
        ingest(tmp_path, tmp_path / "t.duckdb")


def test_ingest_creates_the_parent_directory(tmp_path):
    db = tmp_path / "nested" / "dir" / "t.duckdb"
    ingest(FIXTURE_DIR, db)
    assert db.is_file()


def test_timestamps_are_typed_not_text(fixture_db):
    with duckdb.connect(fixture_db, read_only=True) as c:
        types = dict(
            c.sql(
                "SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_name = 'orders'"
            ).fetchall()
        )
    assert types["order_purchase_timestamp"] == "TIMESTAMP"
    assert types["order_delivered_customer_date"] == "TIMESTAMP"
