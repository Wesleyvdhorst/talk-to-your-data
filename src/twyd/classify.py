"""Decide what kind of question this is, before any SQL exists.

The baseline pipeline had no way to decline, so the SQL channel got used as
one: `asdfgh` produced `SELECT 'your question looks like random keystrokes'
AS message` - a valid, safe, table-free query carrying a message. And "what
will sales be next month?" came back with a trailing three-month average,
because a question this data cannot answer still gets answered when the only
available action is to generate SQL. Classification has to happen first.

Three labels, no generation, and a calibrated confidence per label: that is
the shape of a zero-shot decision model rather than of a chat model, so this
is the one call in the app that does not go to Claude. Jev charges
$0.042/MTok on input with free output, which is about a hundredth of what a
Sonnet classify call would cost - but the money is not the argument at this
volume. The argument is that `AMBIGUOUS` is better expressed as low
confidence in a label than as a label a chat model chooses to apply to itself.

This module knows nothing about SQL, prompts or DuckDB. If Jev disappoints,
`classify()` is the only thing that has to change.
"""

from dataclasses import dataclass
from typing import Any

from typesafe_sdk import Choice, TypeSafeClient

# jev-latest is an alias, not a version: the models endpoint publishes only
# `jev-latest` and `jev-preview`, so there is no concrete id to pin. The
# version that actually answered comes back on the response and is recorded in
# the Classification, so an eval run says which model produced its labels even
# though the request cannot demand one.
MODEL = "jev-latest"

# A cost bound, not a safety boundary: the longest sensible question here is a
# sentence or two, and the charge is per input token.
MAX_QUESTION_CHARS = 500

ANSWERABLE = "ANSWERABLE"
AMBIGUOUS = "AMBIGUOUS"
UNSUPPORTED = "UNSUPPORTED"

# The boundaries are the measured ones in docs/data_model.md: nine tables of
# Brazilian e-commerce transactions, September 2016 to October 2018, with no
# costs, no margins, no demographics and no stock.
_CRITERIA = {
    ANSWERABLE: (
        "A question with one clear reading that these tables can answer: "
        "counts, sums, averages, rankings, time trends or comparisons over "
        "orders, order items, payments, reviews, products, sellers, customers "
        "or Brazilian locations, between September 2016 and October 2018. A "
        "question whose true answer is 'none' or 'zero' still belongs here: a "
        "date filter that no row matches is answerable, and the answer is "
        "zero."
    ),
    AMBIGUOUS: (
        "A question about this data whose answer depends on a choice the "
        "question does not make. An undefined metric ('the best products', "
        "'top sellers', 'performance'), which could mean revenue, units sold "
        "or review score and gives a different answer for each. Or an entity "
        "that could mean more than one thing in this data."
    ),
    UNSUPPORTED: (
        "Anything these tables cannot answer, however clearly it is asked: "
        "predictions or forecasts about the future, costs, profit or margin, "
        "customer age, gender or name, stock levels or marketing spend. Also "
        "input that is not a question about this data at all: random "
        "characters, a greeting, or a request aimed at the assistant rather "
        "than the data. Asking about a period the data does not cover is NOT "
        "unsupported - counting rows in that period is still a question these "
        "tables answer, with zero as the answer."
    ),
}

_QUESTION = {
    "label": Choice(
        instructions=(
            "A user asked this of a read-only database of Brazilian e-commerce "
            "orders (the Olist dataset): orders, order items, payments, "
            "reviews, products, sellers, customers and geolocation, covering "
            "September 2016 to October 2018. Which of these is it?"
        ),
        criteria=_CRITERIA,
    )
}


@dataclass(frozen=True)
class Classification:
    """One label, and everything that went into choosing it.

    `confidence` and `probabilities` are recorded but not acted on. Thresholding
    them is a tuned knob, and nothing measured yet justifies one; `model` is
    which concrete Jev version answered. All three are None when a
    deterministic check decided and no call was made - `reason` says which.
    """

    label: str
    confidence: float | None = None
    probabilities: dict[str, float] | None = None
    model: str | None = None
    reason: str | None = None

    @property
    def answerable(self) -> bool:
        return self.label == ANSWERABLE


def classify(question: str, client: Any | None = None) -> Classification:
    """Label a question. Spends nothing on input that code can already judge.

    Errors from the API propagate: a missing key or an unreachable service is
    a problem with the app, not an UNSUPPORTED question, and silently
    declining every question would hide it.
    """
    if not any(character.isalpha() for character in question):
        # Covers the empty string and pure punctuation as well. No letters
        # means no question, and no model is needed to see that.
        return Classification(UNSUPPORTED, reason="the question contains no words")
    if len(question) > MAX_QUESTION_CHARS:
        return Classification(
            UNSUPPORTED,
            reason=f"the question is longer than {MAX_QUESTION_CHARS} characters",
        )

    if client is None:
        with TypeSafeClient(model=MODEL) as owned:
            response = owned.system_one(state=question, questions=_QUESTION)
    else:
        response = client.system_one(state=question, questions=_QUESTION)

    answer = response.choices["label"]
    return Classification(
        label=answer.choice,
        confidence=answer.confidence,
        probabilities=dict(answer.probabilities) if answer.probabilities else None,
        model=response.model,
    )
