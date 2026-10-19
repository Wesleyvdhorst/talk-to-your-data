"""Build the schema description that goes into the SQL-generation prompt.

Two parts, kept separate on purpose:

- The physical schema is introspected from the live database, so it can never
  drift from what the tables actually are.
- The semantics are hand-written below. Every number in them was measured
  against the real data and is asserted in tests/test_real_data.py; the
  derivation is in docs/data_model.md.

No LLM is involved, so this is fully testable offline.
"""

import duckdb

# Measured facts and the join hazards they cause. Not business policy: these
# are properties of the data, which is why they belong in generic context.
TABLE_NOTES = {
    "orders": (
        "One row per order; order_id is unique. Delivery timestamps are NULL "
        "for the 2,965 orders that were never delivered. An order is late when "
        "order_delivered_customer_date > order_estimated_delivery_date (7,827 "
        "orders). 8 orders have status 'delivered' with no delivery date: trust "
        "the timestamp, not the status."
    ),
    "customers": (
        "One row per ORDER, not per person. customer_id is unique per order "
        "(99,441); customer_unique_id identifies the real person (96,096). "
        "'How many customers' almost always means "
        "COUNT(DISTINCT customer_unique_id)."
    ),
    "order_reviews": (
        "One row per review. review_id is NOT unique and NOT a primary key: "
        "789 values recur against different orders, and 547 orders have more "
        "than one review. 768 orders have no review at all. "
        "review_comment_message is Portuguese and present for only ~41% of "
        "reviews."
    ),
    "order_items": (
        "One row per item line within an order; up to 21 lines per order. "
        "price and freight_value are per line, so an order total is "
        "SUM(price), not the price column itself. 775 orders have no items."
    ),
    "order_payments": (
        "One row per payment instalment line; up to 29 lines per order. "
        "payment_value is per line, not the order total. 1 order has no payment."
    ),
    "products": "One row per product; product_id is unique.",
    "sellers": "One row per seller; seller_id is unique.",
    "geolocation": (
        "A sample of lat/lng points, NOT a zip-code lookup table: 1,000,163 "
        "rows for 19,015 zip prefixes. Joining it on zip multiplies rows ~53x. "
        "Aggregate it first (e.g. one average point per zip prefix)."
    ),
    "product_category_translation": (
        "Portuguese to English category names; 71 rows. Two categories used by "
        "products have no translation, so an inner join drops them."
    ),
}

RELATIONSHIPS = """\
orders.customer_id -> customers.customer_id (one-to-one, per order)
order_items.order_id -> orders.order_id (many-to-one)
order_payments.order_id -> orders.order_id (many-to-one)
order_reviews.order_id -> orders.order_id (many-to-one)
order_items.product_id -> products.product_id
order_items.seller_id -> sellers.seller_id
products.product_category_name -> product_category_translation.product_category_name
customers.customer_zip_code_prefix / sellers.seller_zip_code_prefix \
-> geolocation.geolocation_zip_code_prefix (one-to-MANY)"""

JOIN_RULES = """\
- Joining orders to order_items, order_payments or order_reviews duplicates
  order rows. Aggregate the child table first, or use COUNT(DISTINCT ...) on
  purpose. Never AVG() across an inflated join.
- Use LEFT JOIN when orders without items, payments or reviews must still be
  counted; an inner join drops them silently.
- Never join geolocation without aggregating it to one row per zip prefix."""


def schema_context(conn: duckdb.DuckDBPyConnection) -> str:
    """Return the full schema description: columns, notes, relationships, rules.

    All nine tables, every time. Selecting tables per question is an
    optimisation with no observed problem to justify it yet.
    """
    columns = conn.sql(
        "SELECT table_name, column_name, data_type FROM duckdb_columns() "
        # internal = false drops the system catalogs (information_schema etc.)
        # that duckdb_columns() also reports.
        "WHERE internal = false AND schema_name = 'main' "
        "ORDER BY table_name, column_index"
    ).fetchall()

    by_table: dict[str, list[str]] = {}
    for table, column, data_type in columns:
        by_table.setdefault(table, []).append(f"{column} {data_type}")

    blocks = []
    for table, cols in by_table.items():
        n = conn.sql(f'SELECT count(*) FROM "{table}"').fetchone()[0]  # noqa: S608
        block = f"TABLE {table} ({n:,} rows)\n  " + "\n  ".join(cols)
        note = TABLE_NOTES.get(table)
        if note:
            block += f"\n  NOTE: {note}"
        blocks.append(block)

    return (
        "\n\n".join(blocks)
        + "\n\nRELATIONSHIPS\n"
        + RELATIONSHIPS
        + "\n\nJOIN RULES\n"
        + JOIN_RULES
    )
