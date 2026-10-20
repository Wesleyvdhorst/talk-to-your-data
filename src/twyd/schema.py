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

# Each line with the tables it names. A line is sent only when every one of
# them was loaded: a join to a table that is not there is an invitation to
# write SQL that cannot bind.
RELATIONSHIPS = [
    (("orders", "customers"), "orders.customer_id -> customers.customer_id (one-to-one, per order)"),
    (("order_items", "orders"), "order_items.order_id -> orders.order_id (many-to-one)"),
    (("order_payments", "orders"), "order_payments.order_id -> orders.order_id (many-to-one)"),
    (("order_reviews", "orders"), "order_reviews.order_id -> orders.order_id (many-to-one)"),
    (("order_items", "products"), "order_items.product_id -> products.product_id"),
    (("order_items", "sellers"), "order_items.seller_id -> sellers.seller_id"),
    (
        ("products", "product_category_translation"),
        "products.product_category_name -> product_category_translation.product_category_name",
    ),
    (
        ("customers", "geolocation"),
        "customers.customer_zip_code_prefix -> geolocation.geolocation_zip_code_prefix (one-to-MANY)",
    ),
    (
        ("sellers", "geolocation"),
        "sellers.seller_zip_code_prefix -> geolocation.geolocation_zip_code_prefix (one-to-MANY)",
    ),
]

# The child tables that inflate an order when joined to it, and how each one
# is named in the rules below.
_ORDER_CHILDREN = {
    "order_items": "items",
    "order_payments": "payments",
    "order_reviews": "reviews",
}


def _or(items: list[str]) -> str:
    """"a", "a or b", "a, b or c"."""
    return " or ".join([", ".join(items[:-1]), items[-1]] if len(items) > 1 else items)


def _join_rules(tables: set[str]) -> list[str]:
    """The join rules that apply to the loaded tables, naming only those."""
    rules = []
    children = [t for t in _ORDER_CHILDREN if t in tables]
    if "orders" in tables and children:
        rules.append(
            f"- Joining orders to {_or(children)} duplicates\n"
            "  order rows. Aggregate the child table first, or use COUNT(DISTINCT ...) on\n"
            "  purpose. Never AVG() across an inflated join."
        )
        rules.append(
            f"- Use LEFT JOIN when orders without "
            f"{_or([_ORDER_CHILDREN[t] for t in children])} must still be\n"
            "  counted; an inner join drops them silently."
        )
    if "geolocation" in tables:
        rules.append(
            "- Never join geolocation without aggregating it to one row per zip prefix."
        )
    return rules


def table_names(conn: duckdb.DuckDBPyConnection) -> list[str]:
    """The tables this database holds, which may be any subset of the nine."""
    return [
        row[0]
        for row in conn.sql(
            "SELECT table_name FROM duckdb_tables() "
            "WHERE internal = false AND schema_name = 'main' ORDER BY table_name"
        ).fetchall()
    ]


def schema_context(conn: duckdb.DuckDBPyConnection) -> str:
    """Return the full schema description: columns, notes, relationships, rules.

    Every loaded table, every time. Selecting tables per question is an
    optimisation with no observed problem to justify it yet. Relationships and
    join rules are filtered to the loaded tables; a section left empty by that
    is left out.
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

    tables = set(by_table)
    relationships = [line for names, line in RELATIONSHIPS if tables.issuperset(names)]
    rules = _join_rules(tables)
    if relationships:
        blocks.append("RELATIONSHIPS\n" + "\n".join(relationships))
    if rules:
        blocks.append("JOIN RULES\n" + "\n".join(rules))
    return "\n\n".join(blocks)


def column_overview(conn: duckdb.DuckDBPyConnection) -> str:
    """One line per table: its name and its columns, nothing else.

    What the classifier needs and all it needs. Deciding whether this data can
    answer a question is a question about which columns exist - "how old are
    our customers" fails on a missing column, not on a missing table - while
    types, row counts and join hazards are about how to write the query, which
    is the SQL call's problem. ~266 tokens against schema_context's ~1,070.
    """
    columns = conn.sql(
        "SELECT table_name, column_name FROM duckdb_columns() "
        "WHERE internal = false AND schema_name = 'main' "
        "ORDER BY table_name, column_index"
    ).fetchall()

    by_table: dict[str, list[str]] = {}
    for table, column in columns:
        by_table.setdefault(table, []).append(column)
    return "\n".join(f"{table}: {', '.join(cols)}" for table, cols in by_table.items())
