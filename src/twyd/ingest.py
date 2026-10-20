"""Load the Olist CSVs into DuckDB.

Deterministic and idempotent: every run replaces each table from its CSV, so
the resulting database depends only on the contents of the data directory.
Nothing here ever writes to that directory, and no LLM is involved.
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


def ingest(data_dir: Path, db_path: Path) -> dict[str, int]:
    """Replace every table in TABLES from its CSV. Returns {table: row_count}.

    Raises FileNotFoundError if a CSV is missing, so a partial database is
    never silently presented as a complete one.
    """
    paths = {table: data_dir / name for table, name in TABLES.items()}
    missing = [str(p) for p in paths.values() if not p.is_file()]
    if missing:
        raise FileNotFoundError(f"missing CSV files: {', '.join(sorted(missing))}")

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
    return counts


if __name__ == "__main__":
    import sys

    counts = ingest(Path("data"), Path("db/olist.duckdb"))
    for table, n in counts.items():
        print(f"{table:30} {n:>9,}", file=sys.stderr)
    print(f"\nWrote db/olist.duckdb ({sum(counts.values()):,} rows)", file=sys.stderr)
