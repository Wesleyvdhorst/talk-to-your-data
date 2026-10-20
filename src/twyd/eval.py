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

from twyd.classify import AMBIGUOUS, ANSWERABLE, UNSUPPORTED
from twyd.db import connect, run_query
from twyd.pipeline import Answer, answer_question

DB_PATH = Path("db/olist.duckdb")


@dataclass(frozen=True)
class Question:
    """One eval item.

    `expected_label` is required: every question has a right answer to "what
    kind of question is this", including the ones that also have a value.

    `reference` is SQL returning the value the model's query must match, and
    `reference_rows` SQL returning the number of rows it must return. Both are
    None for a question that should be declined - there is nothing to execute.
    Neither is ever a hard-coded number: both are run against the live database
    at eval time, so rebuilding it moves the references with it.

    `prior` is the turns asked before the graded one, through the same
    pipeline, so the history the graded question sees is the real thing rather
    than a hand-written transcript. Each prior turn costs a real call.
    """

    id: int
    question: str
    covers: str
    expected_label: str
    reference: str | None = None
    reference_rows: str | None = None
    prior: tuple[str, ...] = ()


QUESTIONS = [
    Question(
        1,
        "How many orders are there?",
        "count",
        ANSWERABLE,
        "SELECT count(*) FROM orders",
    ),
    Question(
        2,
        "How many customers does the company have?",
        "identity trap",
        ANSWERABLE,
        "SELECT count(DISTINCT customer_unique_id) FROM customers",
    ),
    Question(
        3,
        "How many orders were placed in 2017?",
        "time filter",
        ANSWERABLE,
        "SELECT count(*) FROM orders WHERE year(order_purchase_timestamp) = 2017",
    ),
    Question(
        4,
        "What is the average review score?",
        "aggregation",
        ANSWERABLE,
        "SELECT avg(review_score) FROM order_reviews",
    ),
    Question(
        5,
        "What is the average review score for orders delivered later than estimated?",
        "join inflation trap",
        ANSWERABLE,
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
        ANSWERABLE,
        """SELECT count(*) FROM orders o
           WHERE NOT EXISTS (SELECT 1 FROM order_reviews r WHERE r.order_id = o.order_id)""",
    ),
    Question(
        7,
        "Which customer state has the most orders?",
        "join + group by + ordering",
        ANSWERABLE,
        """SELECT c.customer_state FROM orders o JOIN customers c USING (customer_id)
           GROUP BY 1 ORDER BY count(*) DESC LIMIT 1""",
    ),
    Question(
        8,
        "What is the average number of items per order?",
        "child-table grain",
        ANSWERABLE,
        """WITH per_order AS (SELECT order_id, count(*) AS n FROM order_items GROUP BY order_id)
           SELECT avg(n) FROM per_order""",
    ),
    # 9-12 were record-only in iteration 1, which had nothing to grade them
    # against. Iteration 2 classifies, so each one now has an expected label.
    Question(9, "What are the best products?", "ambiguous", AMBIGUOUS),
    Question(10, "What will sales be next month?", "unsupported: the future", UNSUPPORTED),
    Question(11, "asdfgh", "malformed", UNSUPPORTED),
    Question(
        12,
        "How many orders were placed in 2030?",
        "empty result is correct, NOT unsupported",
        ANSWERABLE,
        "SELECT count(*) FROM orders WHERE year(order_purchase_timestamp) = 2030",
    ),
    # New in iteration 2, written before the first run of this set.
    Question(
        13,
        "What is our profit margin on electronics?",
        "unsupported: no cost or margin data",
        UNSUPPORTED,
    ),
    Question(
        14,
        "How old are our customers?",
        "unsupported: no demographics",
        UNSUPPORTED,
    ),
    Question(
        15,
        "Which sellers perform best?",
        "ambiguous: undefined metric",
        AMBIGUOUS,
    ),
    Question(
        16,
        "How are we doing recently?",
        "ambiguous: undefined metric and undefined period",
        AMBIGUOUS,
    ),
    Question(
        17,
        "What is the average review score for each customer state?",
        "grounding: many rows, and the answer must not miscount them",
        ANSWERABLE,
        reference_rows="SELECT count(DISTINCT customer_state) FROM customers",
    ),
    Question(
        18,
        "What is the average freight value per order?",
        "answerable only if the classifier knows the columns",
        ANSWERABLE,
        """WITH per_order AS (
               SELECT order_id, sum(freight_value) AS f FROM order_items GROUP BY order_id
           )
           SELECT avg(f) FROM per_order""",
    ),
    # New in iteration 3, written before the first run of this version. Each
    # one needs history to be answerable at all, which is the point.
    Question(
        19,
        "and in 2018?",
        "follow-up: the previous question with one thing changed",
        ANSWERABLE,
        "SELECT count(*) FROM orders WHERE year(order_purchase_timestamp) = 2018",
        prior=("How many orders were placed in 2017?",),
    ),
    Question(
        20,
        "by revenue",
        "the clarification loop closing: an answered clarification is a turn",
        ANSWERABLE,
        # Graded on the label alone. A reference value here would grade which
        # column the model chose to return (product_id, category, a top-10
        # list), not whether the follow-up worked.
        prior=("What are the best products?",),
    ),
    Question(
        21,
        "and for late deliveries?",
        "a follow-up with nothing to follow: must ask, not guess",
        AMBIGUOUS,
    ),
]


@dataclass(frozen=True)
class Result:
    question: Question
    answer: Answer
    expected: Any | None
    actual: Any | None
    expected_rows: int | None
    seconds: float

    @property
    def label(self) -> str | None:
        return self.answer.classification.label if self.answer.classification else None

    @property
    def label_ok(self) -> bool:
        return self.label == self.question.expected_label

    @property
    def asked_back(self) -> bool:
        """An AMBIGUOUS question must ASK something, not state that it cannot
        answer. The cheapest honest check for that is a question mark."""
        return bool(self.answer.message and "?" in self.answer.message)

    @property
    def actual_rows(self) -> int | None:
        return self.answer.result.row_count if self.answer.result else None

    @property
    def why(self) -> str:
        """Why the verdict is not PASS. Empty when it is."""
        if not self.answer.ok:
            return f"{self.answer.stage}: {(self.answer.error or '')[:60]}"
        if not self.label_ok:
            return f"labelled {self.label}, expected {self.question.expected_label}"
        if self.question.expected_label == AMBIGUOUS and not self.asked_back:
            return "declined without asking anything"
        if self.expected_rows is not None and self.actual_rows != self.expected_rows:
            return f"returned {self.actual_rows} rows, expected {self.expected_rows}"
        if self.question.reference and not _close(self.actual, self.expected):
            return f"got {_fmt(self.actual)}, expected {_fmt(self.expected)}"
        return ""

    @property
    def verdict(self) -> str:
        """PASS / FAIL / ERROR. Every question is graded now: the four that
        were record-only in iteration 1 are graded on their label, which is
        the feature iteration 2 added."""
        if not self.answer.ok:
            return "ERROR"
        return "PASS" if not self.why else "FAIL"


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
    """Run every question. A declined one costs a fraction of a cent, an
    answered one about $0.01."""
    results = []
    for question in QUESTIONS:
        expected = (
            _first_value(run_query(conn, question.reference))
            if question.reference
            else None
        )
        expected_rows = (
            _first_value(run_query(conn, question.reference_rows))
            if question.reference_rows
            else None
        )
        # The prior turns run first, through the same pipeline, and their
        # outcomes become the history the graded question sees. Not timed and
        # not graded: they are setup, and the turn under test is the last one.
        history = []
        for earlier in question.prior:
            history.append(
                answer_question(
                    earlier, conn, client=client, jev=jev, history=tuple(history)
                ).turn
            )
        started = time.monotonic()
        answer = answer_question(
            question.question, conn, client=client, jev=jev, history=tuple(history)
        )
        seconds = time.monotonic() - started
        results.append(
            Result(
                question=question,
                answer=answer,
                expected=expected,
                actual=_first_value(answer.result),
                expected_rows=expected_rows,
                seconds=seconds,
            )
        )
    return results


def as_markdown(results: list[Result]) -> str:
    """The results table for docs/eval_results.md."""
    passed = sum(r.verdict == "PASS" for r in results)
    labels_ok = sum(r.label_ok for r in results)
    values = [r for r in results if r.question.reference]
    values_ok = sum(_close(r.actual, r.expected) for r in values if r.answer.ok)
    # Jev or Claude, depending on whether a TypeSafe key was set: two runs
    # with different classifiers are different measurements.
    classifiers = sorted({
        r.answer.classification.model
        for r in results
        if r.answer.classification and r.answer.classification.model
    })
    lines = [
        f"**{passed}/{len(results)} questions correct.** "
        f"Classification {labels_ok}/{len(results)}; "
        f"values {values_ok}/{len(values)}. "
        f"Total time {sum(r.seconds for r in results):.1f}s. "
        # Graded turns only, like the time: prior turns are setup. Cache
        # reads are the number that says whether prompt caching is working.
        f"Claude cost ${sum(r.answer.cost_usd for r in results):.4f}, "
        f"{sum(u.cache_read for r in results for u in r.answer.usage):,} "
        f"input tokens read from cache. "
        f"Classified by {', '.join(classifiers) or 'no model'}.",
        "",
        "| # | Question | Covers | Verdict | Label | Expected | Got | Ended at | Cost | Why not |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        label = r.label or "—"
        if not r.label_ok:
            label = f"**{label}**"
        lines.append(
            f"| {r.question.id} | {r.question.question} | {r.question.covers} "
            f"| **{r.verdict}** | {label} | {_fmt(r.expected)} | {_fmt(r.actual)} "
            f"| {r.answer.stage or 'answer'} | ${r.answer.cost_usd:.4f} "
            f"| {r.why.replace('|', '/')} |"
        )
    lines += ["", "## What each question produced", ""]
    for r in results:
        lines += [
            f"### {r.question.id}. {r.question.question}",
            "",
            f"*{r.question.covers} — {r.verdict}, {r.seconds:.1f}s*",
            "",
            "```",
            "\n".join(f"{stage:16} {detail}" for stage, detail in r.answer.trace),
            "```",
            "",
        ]
        if r.answer.sql:
            lines += ["```sql", r.answer.sql.strip(), "```", ""]
        if r.answer.message:
            lines += [f"**Did not answer:** {r.answer.message}", ""]
        if r.answer.answer:
            lines += [f"**Answer:** {r.answer.answer}", ""]
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
        print(f"{r.verdict:7} {r.question.id:2}. {r.question.question[:48]:<50}"
              f" {r.label or '-':12} {r.why}")
    Path("docs/eval_results.md").write_text(
        "# Evaluation results — latest run\n\n"
        "Generated by `uv run --env-file .env python -m twyd.eval`; everything "
        "in this file is overwritten on each run. The question set and the "
        "grading rules are in `eval_questions.md`. The recorded per-iteration "
        "runs this is measured against are archived in "
        "`eval_results_iteration3.md`, `eval_results_iteration2.md` and "
        "`eval_results_iteration1.md`; the cost before and after prompt "
        "caching in `eval_results_m13_uncached.md` and "
        "`eval_results_m13_cached.md`; the run with Claude as the classifier "
        "(no TypeSafe key) in `eval_results_claude_classifier.md`.\n\n"
        + as_markdown(results)
        + "\n"
    )
    print("\nWrote docs/eval_results.md")


if __name__ == "__main__":
    main()
