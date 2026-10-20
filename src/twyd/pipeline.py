"""question -> classification -> SQL -> validation -> execution -> answer.

Linear and deterministic. No repair attempt and no routing: those are later
work, and adding them here is a change to this file rather than to llm.py.

Classification comes first and can end the turn. The alternative - generate
SQL and hope the model declines inside it - is what the baseline did, and it
produced `SELECT 'your question looks like random keystrokes' AS message`.

Contains no UI code and opens no database: the caller owns the connection, so
a Streamlit layer can keep one open across questions.

History lives here too, and only here. `Turn` and `history_block()` are this
module's because a follow-up is a property of the conversation, not of a
prompt or of a classifier: the layers underneath receive the rendered string
and stay ignorant of what produced it.
"""

from dataclasses import dataclass
from typing import Any, NamedTuple, Sequence

import duckdb

from twyd.classify import AMBIGUOUS, UNSUPPORTED, Classification, classify
from twyd.db import QueryResult, run_query
from twyd.llm import clarify, generate_answer, generate_sql
from twyd.schema import column_overview, schema_context
from twyd.sql_guard import UnsafeSQL, validate


# Shown when the pipeline declines. Deterministic text: nothing here needs a
# model, and a generated apology would be one more thing that can hallucinate.
_UNSUPPORTED_MESSAGE = (
    "This dataset cannot answer that. It holds Olist's Brazilian e-commerce "
    "transactions from September 2016 to October 2018 - orders, items, "
    "payments, reviews, products, sellers and locations - with no costs, "
    "margins, customer demographics or stock, and nothing about the future."
)

# Hard caps on how much of the past any question can see. Both of them,
# because turns alone do not bound cost: three turns of 300-character SQL is a
# different prompt from three turns of 2,000. Old turns are DROPPED, never
# summarised - a summary is one more model call that can be wrong about what
# was asked, and the point of the cap is to bound cost, not to preserve
# meaning. The newest turn always survives: it is the one a follow-up refers to.
MAX_HISTORY_TURNS = 3
MAX_HISTORY_CHARS = 2000


class Turn(NamedTuple):
    """One past turn, as the next question is allowed to see it.

    Carries the question, the SQL that answered it, and the clarification it
    was asked - and deliberately NOT the rows. A follow-up re-derives its
    scope by querying again; pasting an earlier result into a prompt would
    make the second answer evidence for nothing, and every grounded claim in
    this app comes from a result the query just produced.
    """

    question: str
    sql: str | None = None
    asked: str | None = None  # the clarification we sent back, if any


def history_block(turns: Sequence[Turn]) -> str:
    """Render recent turns for a prompt, within both caps. "" when empty.

    Pure and offline: the caps are tested without a model or a database.
    """
    recent = list(turns)[-MAX_HISTORY_TURNS:]
    while recent:
        lines = []
        for number, turn in enumerate(recent, start=1):
            lines.append(f"{number}. Question: {turn.question}")
            if turn.sql:
                lines.append(f"   Answered with SQL: {' '.join(turn.sql.split())}")
            if turn.asked:
                lines.append(f"   We asked back: {turn.asked}")
        block = "EARLIER TURNS IN THIS CONVERSATION\n" + "\n".join(lines)
        if len(block) <= MAX_HISTORY_CHARS:
            return block
        # Over budget: drop the oldest and try again. Dropping the newest
        # would strip exactly the turn a follow-up is about.
        recent.pop(0)
    return ""


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
    # The modelling choice the SQL call reported, as prose. None when no SQL
    # was generated and "" when the model said the question determined the
    # query outright - two different facts, so they stay apart.
    assumption: str | None = None
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
    def turn(self) -> Turn:
        """This question as the next one may see it.

        Derived, like `trace`, from the fields already set, so the caller
        appends what happened rather than reconstructing it - and so it cannot
        record SQL that was never generated or a question never asked.
        """
        return Turn(
            question=self.question,
            sql=self.sql,
            asked=self.message if self.stage == "clarify" else None,
        )

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
            detail = f"{len(self.sql)} characters"
            if self.assumption:
                detail += f" - {self.assumption}"
            steps.append(("generate_sql", detail))
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
    history: Sequence[Turn] = (),
) -> Answer:
    """Answer one question, or decline it. Returns failures as fields.

    `client` is the Claude client and `jev` the classifier's; both are optional
    so tests can stub either independently.

    `history` is the earlier turns of this conversation, newest last, and is
    bounded by history_block() rather than by the caller. It informs
    classification and SQL generation and nothing else: this turn still
    produces its own validated SQL and its own answer grounded in its own
    result. An answered clarification is just a follow-up turn, which is why
    there is one mechanism here and not two.

    Only the three failures that are part of normal operation are caught -
    unsafe SQL, a database error, a query that ran too long. Anything else
    (a missing API key, a network failure, a malformed model response) still
    raises, because it is a problem with the app rather than with the question.
    """
    if not question or not question.strip():
        return Answer(question=question, error="the question is empty", stage="generate")

    past = history_block(history)
    classification = classify(question, column_overview(conn), client=jev, history=past)
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
    sql, assumption = generate_sql(question, context, client=client, history=past)

    try:
        validate(sql)
    except UnsafeSQL as exc:
        return Answer(
            question=question,
            classification=classification,
            sql=sql,
            assumption=assumption,
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
            assumption=assumption,
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
        # Stated to the answer call rather than re-derived by it: the choice
        # was made by the query, and only the query knows which one it was.
        assumption=assumption,
        client=client,
    )
    return Answer(
        question=question,
        classification=classification,
        sql=sql,
        assumption=assumption,
        result=result,
        answer=answer,
    )
