"""Load the Olist CSVs into DuckDB, downloading them first if there are none.

Deterministic and idempotent: every run replaces each table from its CSV and
drops the tables whose CSV is absent, so the resulting database depends only
on the contents of the data directory. Any subset of the nine files loads -
the assignment's minimum is orders, order_reviews and customers - and the
schema context then describes only what was loaded. Nothing here ever writes
to the data directory, and no LLM is involved.
"""

from pathlib import Path

import duckdb

# Table name in DuckDB -> CSV file name in the data directory.
TABLES = {
    "customers": "olist_customers_dataset.csv",
    "geolocation": "olist_geolocation_dataset.csv",
    "order_items": "olist_order_items_dataset.csv",
    "order_payments": "olist_order_payments_dataset.csv",
    "order_reviews": "olist_order_reviews_dataset.csv",
    "orders": "olist_orders_dataset.csv",
    "products": "olist_products_dataset.csv",
    "sellers": "olist_sellers_dataset.csv",
    "product_category_translation": "product_category_name_translation.csv",
}

# Pinned to a version, so a download is the same nine files every time. Public:
# kagglehub fetches it without Kaggle credentials (verified 2026-10-20, 42.6 MB).
KAGGLE_DATASET = "olistbr/brazilian-ecommerce/versions/2"


def find_data(data_dir: Path) -> Path:
    """Return data_dir if it holds any Olist CSV, else download the dataset.

    The download goes to kagglehub's own cache and is read from there, never
    copied into data_dir: that directory is only ever read. A data_dir with
    some of the files wins over a download, so loading less data on purpose
    stays possible.
    """
    if any((data_dir / name).is_file() for name in TABLES.values()):
        return data_dir
    # Imported here: only a first run without data needs it, and the tests
    # never do.
    import kagglehub

    return Path(kagglehub.dataset_download(KAGGLE_DATASET))


def ingest(data_dir: Path, db_path: Path) -> dict[str, int]:
    """Load every table in TABLES whose CSV is present. Returns {table: row_count}.

    A table whose CSV is absent is dropped rather than left from an earlier
    run, so a partial database is never a stale mix. The caller reports which
    tables are missing; the schema context leaves them out. Raises
    FileNotFoundError when none of the CSVs is present.
    """
    paths = {
        table: data_dir / name
        for table, name in TABLES.items()
        if (data_dir / name).is_file()
    }
    if not paths:
        raise FileNotFoundError(
            f"no Olist CSV files in {data_dir}; expected any of: "
            + ", ".join(TABLES.values())
        )

    db_path.parent.mkdir(parents=True, exist_ok=True)
    # Unlike db.connect(), this connection must reach the filesystem: it reads
    # the CSVs and writes the database. Safe because the SQL here is fixed and
    # the table names come from TABLES, never from user or model input.
    counts = {}
    with duckdb.connect(db_path) as conn:
        for table, path in paths.items():
            # read_csv infers types per column; sample_size=-1 reads the whole
            # file so a column is not mistyped from an unrepresentative head.
            conn.execute(
                f"CREATE OR REPLACE TABLE {table} AS "  # noqa: S608 - names are from TABLES
                "SELECT * FROM read_csv(?, header = true, sample_size = -1)",
                [str(path)],
            )
            counts[table] = conn.sql(f"SELECT count(*) FROM {table}").fetchone()[0]
        for table in TABLES.keys() - paths.keys():
            conn.execute(f"DROP TABLE IF EXISTS {table}")  # noqa: S608 - names are from TABLES
    return counts


if __name__ == "__main__":
    import sys

    source = find_data(Path("data"))
    print(f"Reading CSVs from {source}", file=sys.stderr)
    counts = ingest(source, Path("db/olist.duckdb"))
    for table in TABLES:
        n = f"{counts[table]:>9,}" if table in counts else "  missing - not loaded"
        print(f"{table:30} {n}", file=sys.stderr)
    print(f"\nWrote db/olist.duckdb ({sum(counts.values()):,} rows)", file=sys.stderr)
