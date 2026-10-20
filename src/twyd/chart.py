"""Pick a chart for a result from its shape. No model: the types decide.

Pure and Streamlit-free, so it is tested without running the app. app.py
asks `chart_for` whether a result has a chart, and draws it if so.

The rule is deliberately narrow. The first column is the x axis; every column
after it that holds only numbers is a candidate y. One y is drawn at a time,
because "orders per month" and "share late" on one axis flattens the share
into a line along the bottom - a chart that is technically correct and
says nothing.
"""

import datetime as dt
import re
from decimal import Decimal
from typing import Any, NamedTuple, Sequence

# Past this many categories a bar chart is a texture, not a comparison.
MAX_BARS = 50

# How a time bucket comes back when the SQL formats it: strftime gives
# '2017-01' or '2017-01-31', not a date.
_DATE_TEXT = re.compile(r"\d{4}-\d{2}(-\d{2})?")


class Chart(NamedTuple):
    kind: str  # "line" or "bar"
    x: str
    # The numeric columns, in result order. The LAST is drawn by default: in
    # every multi-measure example the app ships, the count that qualifies a
    # row comes first and the measure the question asked about comes last
    # ("review_count, avg_review_score"). A habit of the SQL, not a rule.
    ys: list[str]


def _is_number(value: Any) -> bool:
    # bool is an int in Python, and a column of True/False is not a measure.
    return isinstance(value, (int, float, Decimal)) and not isinstance(value, bool)


def _is_time(value: Any) -> bool:
    # datetime is a subclass of date, so this covers both.
    return isinstance(value, dt.date) or (
        isinstance(value, str) and _DATE_TEXT.fullmatch(value) is not None
    )


def chart_for(columns: Sequence[str], rows: Sequence[Sequence[Any]]) -> Chart | None:
    """A chart for this result, or None when its shape does not suggest one.

    None for a single row (one number is a sentence, not a chart), for a
    result with no numeric column, and for an x axis that is neither time nor
    a short list of categories.
    """
    if len(rows) < 2 or len(columns) < 2:
        return None
    ys = [
        name
        for i, name in enumerate(columns[1:], start=1)
        # NULL is allowed (an average over nothing); a column of only NULLs is not.
        if all(row[i] is None or _is_number(row[i]) for row in rows)
        and any(row[i] is not None for row in rows)
    ]
    if not ys:
        return None
    xs = [row[0] for row in rows]
    if all(_is_time(x) for x in xs):
        return Chart("line", columns[0], ys)
    if len(rows) <= MAX_BARS and all(
        isinstance(x, (str, int)) and not isinstance(x, bool) for x in xs
    ):
        return Chart("bar", columns[0], ys)
    return None


def _as_date(value: Any) -> Any:
    """'2017-01' -> 2017-01-01. A month as text sits on a category axis, where
    a month with no orders silently vanishes and the line looks unbroken; as a
    date it sits on a time axis, where the gap shows."""
    if isinstance(value, str) and _DATE_TEXT.fullmatch(value):
        return dt.date.fromisoformat(value if len(value) == 10 else value + "-01")
    return value


def series(
    columns: Sequence[str], rows: Sequence[Sequence[Any]], x: str, y: str
) -> dict[str, list[Any]]:
    """The two columns to draw, as column -> values. Decimal becomes float:
    the chart library does not plot Decimal, and DuckDB returns it for every
    DECIMAL column. Date-shaped text becomes a date, for the axis."""
    xi, yi = columns.index(x), columns.index(y)
    return {
        x: [_as_date(row[xi]) for row in rows],
        y: [float(row[yi]) if isinstance(row[yi], Decimal) else row[yi] for row in rows],
    }
