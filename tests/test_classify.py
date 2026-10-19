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
