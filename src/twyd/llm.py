"""The two LLM calls, and the only file that knows the model provider.

Call #1 turns a question plus schema context into SQL. Call #2 turns a
question, its SQL and the result into prose. Neither executes SQL, validates
it, or opens a database: the pipeline does that between them.

Settings are module constants rather than parameters, because every one of
them is a decision worth defending rather than a knob to twiddle:

- claude-sonnet-5-5: the model the assignment offers a key for. $2/$10 per
  MTok, so a question costs about $0.01 end to end.
- No temperature. Sonnet 5.5 rejects non-default sampling values with a 400,
  so the usual `temperature=0` for text-to-SQL is not available. Determinism
  comes from the output schema and a narrow prompt instead.
- Thinking is left adaptive (the parameter is omitted). Effort is the control;
  its default of `high` is more than either of these calls needs. It CAN be
  turned off on this model - `thinking={"type": "between_tools"}`, accepted
  only at effort `high` or below and with no other field alongside it - which
  an earlier version of this comment denied. Not used yet: whether it changes
  either call's output is a measurement for the next eval run, not a guess.
- Structured output rather than a forced tool call: this model rejects
  tool_choice "any"/"tool" with a 400, and assistant prefill too, so the two
  usual ways to coerce bare SQL out of a model are both gone.
"""

import json
from typing import Any, NamedTuple, Protocol

import anthropic

MODEL = "claude-sonnet-5-5"
SQL_EFFORT = "medium"  # guesses until milestone 5 measures them
ANSWER_EFFORT = "low"
CLARIFY_EFFORT = "low"
MAX_TOKENS = 1024
# A clarification is one question back to the user. Two sentences at the most,
# so the cap is here to make a runaway visible rather than to pay for it.
CLARIFY_MAX_TOKENS = 256
# Thinking off for this one call: there is nothing to reason about beyond
# reading the schema and naming the readings it supports, and a clarification
# that varies run to run is itself a kind of ambiguity. `between_tools` is the
# only way to turn thinking off on this model, and it is accepted only at
# effort `high` or below, with no other field alongside it.
_NO_THINKING = {"type": "between_tools"}

# Two required fields, no extras. additionalProperties=False is what makes the
# response shape a guarantee rather than a hope, and `required` is what stops
# `assumption` from being quietly omitted on the questions that most need it.
_SQL_FORMAT = {
    "type": "json_schema",
    "schema": {
        "type": "object",
        "properties": {
            "sql": {"type": "string", "description": "a single DuckDB SELECT query"},
            "assumption": {
                "type": "string",
                "description": (
                    "the modelling choice this query makes that the question "
                    "did not specify, in one clause; empty string if the "
                    "question fully determines the query"
                ),
            },
        },
        "required": ["sql", "assumption"],
        "additionalProperties": False,
    },
}

_CLARIFY_FORMAT = {
    "type": "json_schema",
    "schema": {
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "one short question back to the user",
            }
        },
        "required": ["question"],
        "additionalProperties": False,
    },
}

_CLARIFY_SYSTEM = """\
A user asked a question of an Olist e-commerce database. It has more than one
reasonable reading, and the readings give different answers. Ask them which
one they mean.

- Name the readings, two or three of them, and nothing else. "Best" is
  revenue, units sold, or review score; "recently" is a month, a quarter, or
  the whole period.
- Offer only readings the schema below supports. If a reading would need a
  column that does not exist, leave it out.
- Do not pick one, do not answer the question, and do not apologise.
- One or two sentences, no preamble, no markdown.

Schema:
{schema_context}"""

_SQL_SYSTEM = """\
You translate questions about an Olist e-commerce database into DuckDB SQL.

Rules:
- Return exactly one SELECT (a leading WITH is fine). No semicolons, no second
  statement, no DDL, no writes.
- Read only the tables in the schema below. No table functions, no file paths.
- Obey the NOTE and JOIN RULES lines in the schema. They record measured
  properties of this data, not style preferences; ignoring one gives a
  confidently wrong number.
- Prefer explicit column lists over SELECT *.
- If the question needs a column that does not exist, write the closest query
  the schema does support rather than inventing a column.

- If EARLIER TURNS are given, the current question may be a follow-up that
  only makes sense with them: "and in 2018?" means the previous question with
  that year, "break that down by state" means the previous query grouped by
  state. Rewrite the previous query rather than starting over, and use the
  earlier SQL for its shape only - never quote a number from an earlier turn,
  because this query has to stand on its own result.

Also report the `assumption`: the one choice this query makes that the question
left open and that would change the number if made differently. A denominator
(which rows the average divides by), an inclusion (whether rows with no child
rows count), a tie-break, or which of two timestamps defines a date. One
clause, in plain language, naming what was counted rather than the SQL that
counts it. If the question and the schema together fully determine the query,
return an empty string - do not manufacture a choice you did not make, and
never restate the query as an assumption.

Schema:
{schema_context}"""

_ANSWER_SYSTEM = """\
You explain the result of a database query to the person who asked the
question. Two or three sentences, no preamble, no markdown headings.

- Use only numbers that appear in the result. Never estimate, extrapolate or
  add context the result does not contain.
- Every claim ABOUT the result - how many rows it has, which columns it has,
  whether it is complete - must repeat the RESULT line. Do not count the rows
  yourself and do not reason about what is missing from them.
- Mention the shape only when it matters to the answer: many rows, no rows, or
  a truncated result. For a single value, give the value and stop; do not
  recite the RESULT line back.
- Use the schema only to say what a column means. It carries no numbers about
  this result. If it does not define a column, describe the column by the name
  the query gave it rather than guessing what it identifies.
- If the result is empty, say that nothing matched, and do not guess why.
- If the result was truncated, say the answer covers the rows shown.
- Mention a caveat only when the SQL makes one real: a filter that excludes
  rows, or a join that could duplicate them.
- If an ASSUMPTION line is present, state that choice in a clause of its own.
  It is the one thing the reader cannot judge the number without, and it came
  from the query rather than from the question. If there is no ASSUMPTION
  line, do not invent one."""


class _Client(Protocol):
    """What this module needs from a client. Lets tests pass a stub."""

    messages: Any


def _text(response: Any) -> str:
    """The first text block. Raises if the response has none, rather than
    returning an empty string that would look like a model with nothing to say.
    """
    for block in response.content:
        if block.type == "text":
            return block.text
    raise ValueError(f"no text block in response: stop_reason={response.stop_reason!r}")


class Generated(NamedTuple):
    """What the SQL call produced: the query, and the choice it had to make.

    `assumption` is one clause naming the modelling choice the question left
    open - a denominator, an inclusion, a tie-break - and is an empty string
    when the question fully determined the query. It is prose, not a contract:
    nothing branches on it. It exists because "the average number of items per
    order" is 1.1328 or 1.1417 depending on whether orders with no items
    count, both readings are defensible, and a number whose denominator is
    invisible cannot be judged by the person reading it.

    A NamedTuple rather than a dataclass: the call site unpacks it like the
    tuple it is, and the pipeline reads it by name.
    """

    sql: str
    assumption: str


def generate_sql(
    question: str,
    schema_context: str,
    client: _Client | None = None,
    history: str = "",
) -> Generated:
    """Return the SQL the model proposes. It is untrusted until validated.

    `history` is the earlier turns, rendered by pipeline.history_block, and
    goes in the USER message rather than the system prompt. Two reasons, both
    deliberate. The system prompt is the stable prefix - prompt caching is a
    prefix match, so putting a per-turn string in it would invalidate the
    schema context behind it on every question. And earlier turns are the
    user's own text: in the system prompt they would carry operator authority,
    which is how a question talks the rules out of a model. The rules stay
    above; the conversation stays below.
    """
    client = client or anthropic.Anthropic()
    content = (
        f"{history}\n\nCURRENT QUESTION (write SQL for this one): {question}"
        if history
        else question
    )
    response = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        output_config={"effort": SQL_EFFORT, "format": _SQL_FORMAT},
        system=_SQL_SYSTEM.format(schema_context=schema_context),
        messages=[{"role": "user", "content": content}],
    )
    # The schema guarantees the shape, so a KeyError or JSONDecodeError here is
    # a real anomaly and is left to propagate. That includes a missing
    # `assumption`: defaulting it to "" would turn a broken response into a
    # question that silently looks fully determined.
    payload = json.loads(_text(response))
    return Generated(sql=payload["sql"], assumption=payload["assumption"])


def generate_answer(
    question: str,
    sql: str,
    columns: list[str],
    rows: list[tuple[Any, ...]],
    truncated: bool = False,
    schema_context: str = "",
    assumption: str = "",
    client: _Client | None = None,
) -> str:
    """Turn a result into prose. Takes the result's parts, not a QueryResult,
    so this module stays independent of the database layer.

    The RESULT line states the shape of the result as a fact - row count,
    column names, completeness - because the baseline showed the model will
    otherwise describe a 20-row result as 19 rows. Counting rows is Python's
    job; the model is only allowed to repeat the count.

    schema_context is the same string the SQL call received. It is here so the
    model can say what a column means instead of inferring it from its name -
    the other half of the same defect, where customer_unique_id was explained
    as "distinct customer IDs".

    assumption comes from the SQL call and is stated as a fact for the same
    reason the row count is: it is something the query knows and the prose
    would otherwise have to guess at. An empty string omits the line entirely,
    so the model is never asked to explain a choice that was not made.
    """
    client = client or anthropic.Anthropic()
    result = {"columns": columns, "rows": [list(row) for row in rows]}
    content = (
        f"Question: {question}\n\nSQL:\n{sql}\n\n"
        f"RESULT: {len(rows)} row{'' if len(rows) == 1 else 's'}, "
        f"{len(columns)} column{'' if len(columns) == 1 else 's'} "
        f"({', '.join(columns)}), "
        f"{'truncated at the row cap - more rows matched' if truncated else 'complete'}"
        f"\n{json.dumps(result, default=str)}"
    )
    if assumption:
        content += f"\n\nASSUMPTION (the choice the query made, from the SQL call): {assumption}"
    if schema_context:
        content += f"\n\nSchema (what the columns mean; no numbers about this result):\n{schema_context}"
    response = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        output_config={"effort": ANSWER_EFFORT},
        system=_ANSWER_SYSTEM,
        messages=[{"role": "user", "content": content}],
    )
    return _text(response)


def clarify(question: str, schema_context: str, client: _Client | None = None) -> str:
    """Return one question to ask back about an underspecified question.

    Reached only when the classifier said AMBIGUOUS. The classifier decides
    *that* a question is underspecified; this decides *what to ask about it*,
    which needs the schema and a sentence of prose - neither of which a
    decision model produces.
    """
    client = client or anthropic.Anthropic()
    response = client.messages.create(
        model=MODEL,
        max_tokens=CLARIFY_MAX_TOKENS,
        thinking=_NO_THINKING,
        output_config={"effort": CLARIFY_EFFORT, "format": _CLARIFY_FORMAT},
        system=_CLARIFY_SYSTEM.format(schema_context=schema_context),
        messages=[{"role": "user", "content": question}],
    )
    return json.loads(_text(response))["question"]
