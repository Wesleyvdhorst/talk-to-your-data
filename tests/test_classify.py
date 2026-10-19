"""Offline tests for the classifier. No network, no TypeSafe key needed.

What is ours to test: the deterministic pre-checks, the label set we expose,
and that we read the SDK's response correctly. Whether Jev labels a given
question well is an integration concern, and the eval set measures it.
"""

import pytest
from conftest import StubJev  # pytest puts tests/ on sys.path; it is not a package

from twyd.classify import (
    AMBIGUOUS,
    ANSWERABLE,
    MAX_QUESTION_CHARS,
    UNSUPPORTED,
    classify,
)


@pytest.mark.parametrize("label", [ANSWERABLE, AMBIGUOUS, UNSUPPORTED])
def test_each_label_is_read_back_from_the_response(label):
    result = classify("how many orders?", client=StubJev(label))
    assert result.label == label
    assert result.answerable is (label == ANSWERABLE)


def test_the_confidence_and_the_model_version_are_recorded(label=ANSWERABLE):
    """Both exist so a run can say how sure the classifier was and which
    version said it. Neither changes the routing yet."""
    result = classify("how many orders?", client=StubJev(ANSWERABLE, confidence=0.72))
    assert result.confidence == 0.72
    assert result.probabilities[ANSWERABLE] == 0.72
    assert result.model == "jev-stub"


@pytest.mark.parametrize("question", ["", "   ", "???", "...!!", "12345"])
def test_input_with_no_words_is_unsupported_without_a_call(question):
    jev = StubJev()
    result = classify(question, client=jev)
    assert result.label == UNSUPPORTED
    assert result.reason == "the question contains no words"
    assert jev.call_count == 0  # deterministic: no tokens spent


def test_an_overlong_question_is_rejected_without_a_call():
    jev = StubJev()
    result = classify("how many orders " * 100, client=jev)
    assert result.label == UNSUPPORTED
    assert str(MAX_QUESTION_CHARS) in result.reason
    assert jev.call_count == 0


def test_a_normal_question_reaches_the_classifier():
    jev = StubJev()
    classify("how many orders are there?", client=jev)
    assert jev.questions == ["how many orders are there?"]


def test_a_deterministic_rejection_carries_no_confidence():
    """Nothing judged it, so there is nothing to be confident about."""
    result = classify("!!!", client=StubJev())
    assert result.confidence is None
    assert result.probabilities is None
    assert result.model is None


# The classifier's job is to decide whether this data can answer a question,
# and that is a question about columns: "how old are our customers" fails on a
# missing column, not a missing table. Without them it guesses from table
# names - measurably: freight value, product weight and description length all
# got the right label at 0.54-0.76 confidence, where questions decidable from
# a table name alone sat at 0.91-1.00.


def test_the_columns_are_sent_to_the_classifier():
    jev = StubJev()
    classify("how old are our customers?", "customers: customer_id, customer_city", client=jev)
    instructions = jev.instructions[0]
    assert "TABLES AND COLUMNS" in instructions
    assert "customers: customer_id, customer_city" in instructions
    assert "COLUMNS" in instructions  # and the instruction to reason from them


def test_without_columns_the_block_is_absent_rather_than_empty():
    """classify() stays testable with no database, but says nothing false."""
    jev = StubJev()
    classify("how many orders?", client=jev)
    assert "TABLES AND COLUMNS" not in jev.instructions[0]


def test_a_deterministic_rejection_sends_no_columns_because_it_sends_nothing():
    jev = StubJev()
    classify("!!!", "orders: order_id", client=jev)
    assert jev.instructions == []
