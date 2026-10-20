from pathlib import Path

import duckdb
import pytest

from twyd.ingest import TABLES, find_data, ingest

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


def test_ingest_raises_when_no_csv_is_present(tmp_path):
    with pytest.raises(FileNotFoundError, match="no Olist CSV files"):
        ingest(tmp_path, tmp_path / "t.duckdb")


def _copy(tables, to):
    to.mkdir(exist_ok=True)
    for table in tables:
        (to / TABLES[table]).write_bytes((FIXTURE_DIR / TABLES[table]).read_bytes())
    return to


def test_ingest_loads_the_tables_whose_csv_is_present_and_no_others(tmp_path):
    data = _copy(["orders", "customers"], tmp_path / "data")
    counts = ingest(data, tmp_path / "t.duckdb")
    assert counts == {"orders": 4, "customers": 4}


def test_a_table_whose_csv_disappeared_is_dropped_not_left_stale(tmp_path):
    """The database depends only on what is in the data directory now."""
    db = tmp_path / "t.duckdb"
    ingest(FIXTURE_DIR, db)
    ingest(_copy(["orders"], tmp_path / "data"), db)
    with duckdb.connect(db, read_only=True) as c:
        assert [r[0] for r in c.sql("SELECT table_name FROM duckdb_tables()").fetchall()] == ["orders"]


def test_find_data_uses_a_data_dir_with_any_csv_and_downloads_nothing(tmp_path, monkeypatch):
    import sys

    monkeypatch.setitem(sys.modules, "kagglehub", None)  # importing it would fail
    data = _copy(["orders"], tmp_path / "data")
    assert find_data(data) == data


def test_find_data_downloads_the_pinned_version_when_the_dir_is_empty(tmp_path, monkeypatch):
    import sys
    import types

    asked = []
    stub = types.SimpleNamespace(
        dataset_download=lambda handle: asked.append(handle) or str(tmp_path / "cache")
    )
    monkeypatch.setitem(sys.modules, "kagglehub", stub)
    assert find_data(tmp_path / "empty") == tmp_path / "cache"
    assert asked == ["olistbr/brazilian-ecommerce/versions/2"]


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
