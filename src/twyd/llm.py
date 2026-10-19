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
from typing import Any, Protocol

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

# One required field, no extras. additionalProperties=False is what makes the
# response shape a guarantee rather than a hope.
_SQL_FORMAT = {
    "type": "json_schema",
    "schema": {
        "type": "object",
        "properties": {
            "sql": {"type": "string", "description": "a single DuckDB SELECT query"}
        },
        "required": ["sql"],
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
  rows, or a join that could duplicate them."""


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


def generate_sql(
    question: str, schema_context: str, client: _Client | None = None
) -> str:
    """Return the SQL the model proposes. It is untrusted until validated."""
    client = client or anthropic.Anthropic()
    response = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        output_config={"effort": SQL_EFFORT, "format": _SQL_FORMAT},
        system=_SQL_SYSTEM.format(schema_context=schema_context),
        messages=[{"role": "user", "content": question}],
    )
    # The schema guarantees the shape, so a KeyError or JSONDecodeError here is
    # a real anomaly and is left to propagate.
    return json.loads(_text(response))["sql"]


def generate_answer(
    question: str,
    sql: str,
    columns: list[str],
    rows: list[tuple[Any, ...]],
    truncated: bool = False,
    schema_context: str = "",
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
