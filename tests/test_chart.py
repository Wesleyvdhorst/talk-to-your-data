"""The chart is chosen from the result's shape alone. Offline, no Streamlit."""

import datetime as dt
from decimal import Decimal

from twyd.chart import MAX_BARS, Chart, chart_for, series


def test_a_month_bucket_is_a_line():
    rows = [("2017-01", 800, 0.05), ("2017-02", 1780, 0.07)]
    assert chart_for(["month", "orders", "late_share"], rows) == Chart(
        "line", "month", ["orders", "late_share"]
    )


def test_a_real_date_is_a_line_too():
    rows = [(dt.datetime(2018, 1, 1), 7269), (dt.datetime(2018, 2, 1), 6728)]
    assert chart_for(["m", "n"], rows).kind == "line"


def test_categories_and_a_number_are_bars():
    rows = [("SP", 41746), ("RJ", 12852), ("MG", 11635)]
    assert chart_for(["state", "orders"], rows) == Chart("bar", "state", ["orders"])


def test_small_integers_on_the_x_axis_are_categories():
    """"Do orders with more items get worse reviews?" comes back keyed by the
    item count."""
    rows = [(1, 4.15), (2, 3.79), (3, 3.52)]
    assert chart_for(["items", "avg_score"], rows).kind == "bar"


def test_one_row_is_a_sentence_not_a_chart():
    assert chart_for(["customers"], [(96096,)]) is None
    assert chart_for(["state", "orders"], [("SP", 41746)]) is None


def test_no_numeric_column_means_no_chart():
    assert chart_for(["state", "city"], [("SP", "sao paulo"), ("RJ", "rio")]) is None


def test_text_columns_are_never_offered_as_y():
    rows = [("SP", "sao paulo", 10), ("RJ", "rio", 5)]
    assert chart_for(["state", "city", "n"], rows).ys == ["n"]


def test_too_many_categories_is_no_chart():
    rows = [(f"seller{i}", i) for i in range(MAX_BARS + 1)]
    assert chart_for(["seller", "n"], rows) is None


def test_true_and_false_are_not_a_measure():
    assert chart_for(["late", "flag"], [("a", True), ("b", False)]) is None


def test_a_null_inside_a_numeric_column_is_tolerated_but_all_nulls_is_not():
    assert chart_for(["s", "avg"], [("a", 1.0), ("b", None)]).ys == ["avg"]
    assert chart_for(["s", "avg"], [("a", None), ("b", None)]) is None


def test_a_month_as_text_is_drawn_on_a_time_axis_so_gaps_show():
    """2016-11 has no orders in Olist. On a category axis it would vanish."""
    data = series(["m", "n"], [("2016-10", 324), ("2016-12", 1)], "m", "n")
    assert data["m"] == [dt.date(2016, 10, 1), dt.date(2016, 12, 1)]
    assert series(["d", "n"], [("2017-01-31", 1)], "d", "n")["d"] == [dt.date(2017, 1, 31)]


def test_decimal_is_drawn_as_float_and_row_order_is_kept():
    columns = ["category", "revenue", "orders"]
    rows = [("toys", Decimal("10.50"), 3), ("art", Decimal("2.25"), 1)]
    assert series(columns, rows, "category", "revenue") == {
        "category": ["toys", "art"],
        "revenue": [10.5, 2.25],
    }
