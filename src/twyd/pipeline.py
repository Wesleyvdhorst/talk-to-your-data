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
from twyd.llm import clarify, generate_answer, generate_sql
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

    @property
    def trace(self) -> list[tuple[str, str]]:
        """Every stage the question passed through, as (stage, what happened).

        Derived rather than recorded: each step's outcome is already implied by
        the fields above, and a second parallel record of it would be one more
        thing that can disagree with what actually ran.
        """
        steps: list[tuple[str, str]] = []
        if self.classification:
            c = self.classification
            detail = c.label
            if c.reason:
                detail += f" - {c.reason} (no model call)"
            elif c.confidence is not None:
                detail += f" - confidence {c.confidence:.2f}, {c.model}"
            steps.append(("classify", detail))
        if self.stage == "clarify":
            steps.append(("clarify", "asked which reading was meant"))
            return steps
        if self.stage == "classify":
            return steps
        if self.sql:
            steps.append(("generate_sql", f"{len(self.sql)} characters"))
            steps.append(
                ("validate", "rejected" if self.stage == "validate" else "passed")
            )
        if self.stage == "validate":
            return steps
        if self.result is not None:
            rows = f"{self.result.row_count} rows"
            steps.append(
                ("run_query", f"{rows}, truncated" if self.result.truncated else rows)
            )
        elif self.stage == "execute":
            steps.append(("run_query", "failed"))
            return steps
        if self.answer:
            steps.append(("generate_answer", f"{len(self.answer)} characters"))
        return steps


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
    if classification.label == UNSUPPORTED:
        # Fixed text: the scope of the data is a fact about the data, so
        # nothing here needs a model.
        return Answer(
            question=question,
            classification=classification,
            message=_UNSUPPORTED_MESSAGE,
            stage="classify",
        )

    context = schema_context(conn)

    if classification.label == AMBIGUOUS:
        # The classifier decided *that* the question is underspecified; this
        # decides *what to ask about it*, which needs the schema and a
        # sentence of prose. Still no SQL: nothing is guessed at.
        return Answer(
            question=question,
            classification=classification,
            message=clarify(question, context, client=client),
            stage="clarify",
        )
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
