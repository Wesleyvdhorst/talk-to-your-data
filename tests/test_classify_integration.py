"""Real Jev calls. Run on purpose: uv run --env-file .env pytest -m integration

About $0.0001 each - input is $0.042/MTok and output is free - so this file is
cheap enough to cover every label. It checks the labels the pipeline's
behaviour depends on, not the confidence values, which are the model's to
decide and are only recorded.
"""

import os
from pathlib import Path

import pytest

from twyd.classify import AMBIGUOUS, ANSWERABLE, UNSUPPORTED, classify
from twyd.db import connect
from twyd.schema import column_overview

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.environ.get("TYPESAFE_API_KEY"),
        reason="needs TYPESAFE_API_KEY (uv run --env-file .env pytest -m integration)",
    ),
]


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
def test_the_questions_the_baseline_answers_stay_answerable(question, columns):
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
def test_what_this_data_cannot_answer_is_declined(question, columns):
    assert classify(question, columns).label == UNSUPPORTED


@pytest.mark.parametrize(
    "question",
    [
        "What are the best products?",  # best by revenue, units or reviews?
        "Which sellers perform best?",
    ],
)
def test_an_undefined_metric_is_ambiguous(question, columns):
    assert classify(question, columns).label == AMBIGUOUS


def test_the_concrete_model_version_is_recorded(columns):
    """`jev-latest` is an alias - the models endpoint publishes no pinned id -
    so the version that answered is recorded rather than requested."""
    result = classify("How many orders are there?", columns)
    assert result.model and result.model != "jev-latest"
    assert 0.0 <= result.confidence <= 1.0
