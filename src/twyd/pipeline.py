"""question -> SQL -> validation -> execution -> answer.

Linear and deterministic. No classification, no repair attempt, no routing:
those are later iterations, and adding them here is a change to this file
rather than to llm.py.

Contains no UI code and opens no database: the caller owns the connection, so
a Streamlit layer can keep one open across questions.
"""

from dataclasses import dataclass
from typing import Any

import duckdb

from twyd.db import QueryResult, run_query
from twyd.llm import generate_answer, generate_sql
from twyd.schema import schema_context
from twyd.sql_guard import UnsafeSQL, validate


@dataclass(frozen=True)
class Answer:
    """Everything the pipeline learned, including how it failed.

    `error` and `answer` are mutually exclusive: exactly one is set. `sql` is
    present whenever the model produced any, even when it was rejected - that
    is the field worth reading when something went wrong.
    """

    question: str
    sql: str | None = None
    result: QueryResult | None = None
    answer: str | None = None
    error: str | None = None
    stage: str | None = None  # which step failed: generate | validate | execute

    @property
    def ok(self) -> bool:
        return self.error is None


def answer_question(
    question: str,
    conn: duckdb.DuckDBPyConnection,
    client: Any | None = None,
) -> Answer:
    """Answer one question. Returns failures as fields, never as empty results.

    Only the three failures that are part of normal operation are caught -
    unsafe SQL, a database error, a query that ran too long. Anything else
    (a missing API key, a network failure, a malformed model response) still
    raises, because it is a problem with the app rather than with the question.
    """
    if not question or not question.strip():
        return Answer(question=question, error="the question is empty", stage="generate")

    context = schema_context(conn)
    sql = generate_sql(question, context, client=client)

    try:
        validate(sql)
    except UnsafeSQL as exc:
        return Answer(question=question, sql=sql, error=str(exc), stage="validate")

    try:
        result = run_query(conn, sql)
    except (duckdb.Error, TimeoutError) as exc:
        return Answer(question=question, sql=sql, error=str(exc), stage="execute")

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
    return Answer(question=question, sql=sql, result=result, answer=answer)
