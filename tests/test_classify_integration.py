"""Real classifier calls. Run on purpose: uv run --env-file .env pytest -m integration

Every boundary runs on both classifiers: Jev (about $0.0001 a call - input is
$0.042/MTok and output is free) and the Claude fallback the app uses without a
TypeSafe key (about $0.003 cold). The same questions pin both, because the
criteria are shared and a boundary one classifier holds is not evidence that
the other does. It checks the labels the pipeline's behaviour depends on, not
Jev's confidence values, which are the model's to decide and are only recorded.
"""

import os
from pathlib import Path

import pytest

from twyd.classify import AMBIGUOUS, ANSWERABLE, UNSUPPORTED, classify
from twyd.db import connect
from twyd.schema import column_overview

pytestmark = pytest.mark.integration


@pytest.fixture(params=["jev", "claude"])
def classifier(request, monkeypatch):
    """Which classifier answers. Claude is chosen the way the app chooses it:
    by there being no TypeSafe key."""
    key = {"jev": "TYPESAFE_API_KEY", "claude": "ANTHROPIC_API_KEY"}[request.param]
    if not os.environ.get(key):
        pytest.skip(f"needs {key} (uv run --env-file .env pytest -m integration)")
    if request.param == "claude":
        monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    return request.param


@pytest.fixture(scope="module")
def columns():
    """The real column overview, because that is what the pipeline sends. The
    classifier labelled these questions correctly on table names alone, but at
    0.54-0.76 confidence where it now sits at 0.82-1.00."""
    path = Path("db/olist.duckdb")
    if not path.is_file():
        pytest.skip("db/olist.duckdb not built; run ingest() first")
    with connect(path) as conn:
        yield column_overview(conn)


@pytest.mark.parametrize(
    "question",
    [
        "How many orders are there?",
        "How many customers does the company have?",
        "What is the average review score for orders delivered later than estimated?",
        "Which customer state has the most orders?",
        # A date filter nothing matches is still answerable: the answer is 0.
        # The first version of the criteria called this UNSUPPORTED, because
        # "outside the covered period" beat "an answer of zero is an answer".
        "How many orders were placed in 2030?",
        # Needs a column, not a table: freight_value exists, and a delivery
        # time has to be computed from two timestamps.
        "What is the average freight value per order?",
        "What is the average delivery time in days?",
    ],
)
def test_the_questions_the_baseline_answers_stay_answerable(question, columns, classifier):
    assert classify(question, columns).label == ANSWERABLE


@pytest.mark.parametrize(
    "question",
    [
        "What will sales be next month?",  # the future
        "What is our profit margin on electronics?",  # no costs in this data
        "How old are our customers?",  # no demographics
        "asdfgh",  # the baseline answered this one with SQL
        "hello there",  # not a question about data
    ],
)
def test_what_this_data_cannot_answer_is_declined(question, columns, classifier):
    assert classify(question, columns).label == UNSUPPORTED


@pytest.mark.parametrize(
    "question",
    [
        "What are the best products?",  # best by revenue, units or reviews?
        "Which sellers perform best?",
        # Milestone 10. This came back UNSUPPORTED at 0.67 - the lowest
        # confidence in the iteration 2 run - because nothing in it names a
        # metric or a period. Both are choices the data CAN make, so the right
        # move is to ask, and the criteria now say so.
        "How are we doing recently?",
        "How is business lately?",
    ],
)
def test_an_undefined_metric_or_period_is_ambiguous(question, columns, classifier):
    assert classify(question, columns).label == AMBIGUOUS


@pytest.mark.parametrize(
    "question",
    [
        # The controls for the milestone 10 criteria change. Widening AMBIGUOUS
        # to cover vagueness must not pull a genuinely unanswerable question
        # with it: these lack a column or ask about the future, which is what
        # UNSUPPORTED is for, however vaguely they are worded.
        "How are our profit margins doing recently?",
        "How will we be doing next quarter?",
    ],
)
def test_vague_wording_does_not_make_a_missing_column_ambiguous(question, columns, classifier):
    assert classify(question, columns).label == UNSUPPORTED


@pytest.mark.skipif(not os.environ.get("TYPESAFE_API_KEY"), reason="needs TYPESAFE_API_KEY")
def test_the_concrete_model_version_is_recorded(columns):
    """`jev-latest` is an alias - the models endpoint publishes no pinned id -
    so the version that answered is recorded rather than requested."""
    result = classify("How many orders are there?", columns)
    assert result.model and result.model != "jev-latest"
    assert 0.0 <= result.confidence <= 1.0
