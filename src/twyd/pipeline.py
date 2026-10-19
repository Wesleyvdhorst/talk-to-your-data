"""question -> classification -> SQL -> validation -> execution -> answer.

Linear and deterministic. No repair attempt and no routing: those are later
work, and adding them here is a change to this file rather than to llm.py.

Classification comes first and can end the turn. The alternative - generate
SQL and hope the model declines inside it - is what the baseline did, and it
produced `SELECT 'your question looks like random keystrokes' AS message`.

Contains no UI code and opens no database: the caller owns the connection, so
a Streamlit layer can keep one open across questions.
"""

from dataclasses import dataclass
from typing import Any

import duckdb

from twyd.classify import AMBIGUOUS, UNSUPPORTED, Classification, classify
from twyd.db import QueryResult, run_query
from twyd.llm import generate_answer, generate_sql
from twyd.schema import schema_context
from twyd.sql_guard import UnsafeSQL, validate


# Shown when the pipeline declines. Deterministic text: nothing here needs a
# model, and a generated apology would be one more thing that can hallucinate.
_UNSUPPORTED_MESSAGE = (
    "This dataset cannot answer that. It holds Olist's Brazilian e-commerce "
    "transactions from September 2016 to October 2018 - orders, items, "
    "payments, reviews, products, sellers and locations - with no costs, "
    "margins, customer demographics or stock, and nothing about the future."
)
# Iteration 2, milestone 8 replaces this with a question naming the readings
# the data supports. Until then the pipeline says that it cannot choose.
_AMBIGUOUS_MESSAGE = (
    "That question has more than one reading in this data, and they give "
    "different answers. Please ask it again with the measure you mean."
)


@dataclass(frozen=True)
class Answer:
    """Everything the pipeline learned, including how it failed.

    Exactly one of `answer`, `message` and `error` is set:

    - `answer` - a question was answered from a result.
    - `message` - the pipeline declined to answer, which is an outcome and not
      a failure: the question is unsupported or underspecified. No SQL exists.
    - `error` - something broke. `sql` is present whenever the model produced
      any, even when it was rejected; that is the field worth reading.
    """

    question: str
    classification: Classification | None = None
    sql: str | None = None
    result: QueryResult | None = None
    answer: str | None = None
    message: str | None = None
    error: str | None = None
    stage: str | None = None  # generate | validate | execute, or classify

    @property
    def ok(self) -> bool:
        return self.error is None

    @property
    def declined(self) -> bool:
        """True when the pipeline chose not to answer. Not an error."""
        return self.message is not None


def answer_question(
    question: str,
    conn: duckdb.DuckDBPyConnection,
    client: Any | None = None,
    jev: Any | None = None,
) -> Answer:
    """Answer one question, or decline it. Returns failures as fields.

    `client` is the Claude client and `jev` the classifier's; both are optional
    so tests can stub either independently.

    Only the three failures that are part of normal operation are caught -
    unsafe SQL, a database error, a query that ran too long. Anything else
    (a missing API key, a network failure, a malformed model response) still
    raises, because it is a problem with the app rather than with the question.
    """
    if not question or not question.strip():
        return Answer(question=question, error="the question is empty", stage="generate")

    classification = classify(question, client=jev)
    if classification.label in (UNSUPPORTED, AMBIGUOUS):
        return Answer(
            question=question,
            classification=classification,
            message=(
                _UNSUPPORTED_MESSAGE
                if classification.label == UNSUPPORTED
                else _AMBIGUOUS_MESSAGE
            ),
            stage="classify",
        )

    context = schema_context(conn)
    sql = generate_sql(question, context, client=client)

    try:
        validate(sql)
    except UnsafeSQL as exc:
        return Answer(
            question=question,
            classification=classification,
            sql=sql,
            error=str(exc),
            stage="validate",
        )

    try:
        result = run_query(conn, sql)
    except (duckdb.Error, TimeoutError) as exc:
        return Answer(
            question=question,
            classification=classification,
            sql=sql,
            error=str(exc),
            stage="execute",
        )

    answer = generate_answer(
        question,
        sql,
        result.columns,
        result.rows,
        truncated=result.truncated,
        # The same context the SQL call got, so the answer can say what a
        # column means rather than inferring it from the name.
        schema_context=context,
        client=client,
    )
    return Answer(
        question=question,
        classification=classification,
        sql=sql,
        result=result,
        answer=answer,
    )
