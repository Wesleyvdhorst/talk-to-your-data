"""The fixed evaluation set and its runner.

The questions were written before the first run (docs/eval_questions.md).
Grading is deterministic: the model's query must return the same value as a
reference query written by hand. No LLM judge - comparing two numbers does not
need one, and a judge would add the failure mode we are trying to measure.

Run with:  uv run --env-file .env python -m twyd.eval
"""

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from twyd.db import connect, run_query
from twyd.pipeline import Answer, answer_question

DB_PATH = Path("db/olist.duckdb")


@dataclass(frozen=True)
class Question:
    """One eval item. `reference` is None for record-only questions."""

    id: int
    question: str
    covers: str
    reference: str | None = None


QUESTIONS = [
    Question(1, "How many orders are there?", "count", "SELECT count(*) FROM orders"),
    Question(
        2,
        "How many customers does the company have?",
        "identity trap",
        "SELECT count(DISTINCT customer_unique_id) FROM customers",
    ),
    Question(
        3,
        "How many orders were placed in 2017?",
        "time filter",
        "SELECT count(*) FROM orders WHERE year(order_purchase_timestamp) = 2017",
    ),
    Question(
        4,
        "What is the average review score?",
        "aggregation",
        "SELECT avg(review_score) FROM order_reviews",
    ),
    Question(
        5,
        "What is the average review score for orders delivered later than estimated?",
        "join inflation trap",
        """WITH per_order AS (
               SELECT order_id, avg(review_score) AS s FROM order_reviews GROUP BY order_id
           )
           SELECT avg(p.s) FROM orders o JOIN per_order p USING (order_id)
           WHERE o.order_delivered_customer_date > o.order_estimated_delivery_date""",
    ),
    Question(
        6,
        "How many orders have no reviews at all?",
        "anti-join trap",
        """SELECT count(*) FROM orders o
           WHERE NOT EXISTS (SELECT 1 FROM order_reviews r WHERE r.order_id = o.order_id)""",
    ),
    Question(
        7,
        "Which customer state has the most orders?",
        "join + group by + ordering",
        """SELECT c.customer_state FROM orders o JOIN customers c USING (customer_id)
           GROUP BY 1 ORDER BY count(*) DESC LIMIT 1""",
    ),
    Question(
        8,
        "What is the average number of items per order?",
        "child-table grain",
        """WITH per_order AS (SELECT order_id, count(*) AS n FROM order_items GROUP BY order_id)
           SELECT avg(n) FROM per_order""",
    ),
    # Record-only: iteration 1 has no classification, clarification or repair.
    Question(9, "What are the best products?", "ambiguous"),
    Question(10, "What will sales be next month?", "unsupported"),
    Question(11, "asdfgh", "malformed"),
    Question(12, "How many orders were placed in 2030?", "empty result is correct"),
]


@dataclass(frozen=True)
class Result:
    question: Question
    answer: Answer
    expected: Any | None
    actual: Any | None
    seconds: float

    @property
    def verdict(self) -> str:
        """PASS / FAIL / ERROR for graded questions, RECORD for the rest."""
        if self.question.reference is None:
            return "RECORD"
        if not self.answer.ok:
            return "ERROR"
        return "PASS" if _close(self.actual, self.expected) else "FAIL"


# Half of the last place a 2-decimal answer can differ in. Wide enough that a
# model rounding 2.5665 to 2.57 still matches; narrow enough that a genuinely
# different query does not. The first version used 0.01, which passed an
# average over 99,441 orders (1.1328) against one over the 98,666 orders that
# have items (1.1417) - two different questions, graded as agreement.
_TOLERANCE = 0.005


def _close(actual: Any, expected: Any) -> bool:
    """Equal, allowing only the difference 2-decimal rounding can introduce."""
    if isinstance(expected, bool) or isinstance(actual, bool):
        return actual is expected
    if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
        return abs(float(actual) - float(expected)) < _TOLERANCE
    return str(actual).strip().lower() == str(expected).strip().lower()


def _first_value(result) -> Any | None:
    """The top-left cell: every graded question asks for a single value."""
    if result is None or not result.rows:
        return None
    return result.rows[0][0]


def run_eval(conn, client: Any | None = None, jev: Any | None = None) -> list[Result]:
    """Run every question. Each one costs about $0.01.

    Grading the classification itself, and the labels each question should get,
    is milestone 9's work; `jev` is plumbed through now so the runner and its
    offline tests can pass a stub classifier.
    """
    results = []
    for question in QUESTIONS:
        expected = (
            _first_value(run_query(conn, question.reference))
            if question.reference
            else None
        )
        started = time.monotonic()
        answer = answer_question(question.question, conn, client=client, jev=jev)
        seconds = time.monotonic() - started
        results.append(
            Result(
                question=question,
                answer=answer,
                expected=expected,
                actual=_first_value(answer.result),
                seconds=seconds,
            )
        )
    return results


def as_markdown(results: list[Result]) -> str:
    """The results table for docs/eval_results.md."""
    graded = [r for r in results if r.question.reference]
    passed = sum(r.verdict == "PASS" for r in graded)
    lines = [
        f"**{passed}/{len(graded)} graded questions correct.** "
        f"Total time {sum(r.seconds for r in results):.1f}s.",
        "",
        "| # | Question | Covers | Verdict | Expected | Got | Stage | Notes |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        note = (r.answer.error or "").replace("\n", " ")[:80]
        lines.append(
            f"| {r.question.id} | {r.question.question} | {r.question.covers} "
            f"| **{r.verdict}** | {_fmt(r.expected)} | {_fmt(r.actual)} "
            f"| {r.answer.stage or 'ok'} | {note} |"
        )
    lines += ["", "## Generated SQL and answers", ""]
    for r in results:
        lines += [
            f"### {r.question.id}. {r.question.question}",
            "",
            f"*{r.question.covers} — {r.verdict}, {r.seconds:.1f}s*",
            "",
            "```sql",
            (r.answer.sql or "(none generated)").strip(),
            "```",
            "",
            f"**Answer:** {r.answer.answer or '(none)'}",
            "",
        ]
        if r.answer.error:
            lines += [f"**Failed at {r.answer.stage}:** {r.answer.error}", ""]
    return "\n".join(lines)


def _fmt(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.4f}".rstrip("0").rstrip(".")
    return f"{value:,}" if isinstance(value, int) else str(value)


def main() -> None:
    with connect(DB_PATH) as conn:
        results = run_eval(conn)
    for r in results:
        print(f"{r.verdict:7} {r.question.id:2}. {r.question.question[:55]:<57}"
              f" got={_fmt(r.actual)} want={_fmt(r.expected)}")
    Path("docs/eval_results.md").write_text(
        "# Evaluation results\n\n"
        "Produced by `uv run --env-file .env python -m twyd.eval`. "
        "The question set and the grading rules are in `eval_questions.md`.\n\n"
        + as_markdown(results)
        + "\n"
    )
    print("\nWrote docs/eval_results.md")


if __name__ == "__main__":
    main()
