"""Offline tests for the classifier. No network, no TypeSafe key needed.

What is ours to test: the deterministic pre-checks, the label set we expose,
and that we read the SDK's response correctly. Whether Jev labels a given
question well is an integration concern, and the eval set measures it.
"""

import json

import pytest
from conftest import StubJev  # pytest puts tests/ on sys.path; it is not a package

from twyd.classify import (
    _CRITERIA,
    AMBIGUOUS,
    ANSWERABLE,
    MAX_QUESTION_CHARS,
    UNSUPPORTED,
    classify,
)
from twyd.llm import MODEL


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


def test_the_history_is_classified_with_the_question_labelled_last():
    """"and in 2018?" is not a question on its own: without the previous turn
    the only honest label for it is AMBIGUOUS. The classifier takes the
    rendered string, so this module still knows nothing about conversations."""
    jev = StubJev("ANSWERABLE")
    classify("and in 2018?", "orders: order_id", client=jev, history="EARLIER: 2017?")
    (state,) = jev.questions
    assert "EARLIER: 2017?" in state
    assert state.endswith("CURRENT QUESTION: and in 2018?")


def test_without_history_the_state_is_the_bare_question():
    jev = StubJev("ANSWERABLE")
    classify("how many orders?", "orders: order_id", client=jev)
    assert jev.questions == ["how many orders?"]


def test_the_criteria_say_what_makes_a_follow_up_answerable_or_not():
    """Both sides, because a follow-up can go either way: answerable when the
    earlier turns supply what it leaves out, ambiguous when nothing does."""
    from twyd.classify import _CRITERIA

    assert "only makes sense as a follow-up" in _CRITERIA[ANSWERABLE]
    assert "earlier turns do NOT supply" in _CRITERIA[AMBIGUOUS]


# Without a TypeSafe key the same criteria go to Claude. What is ours to test
# is the routing and the request; how well Claude holds the boundaries is
# measured by test_classify_integration.py on both backends.


def claude_says(label: str):
    from test_llm import StubBlock, StubClient

    return StubClient([StubBlock(json.dumps({"label": label}))])


@pytest.fixture
def no_typesafe_key(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)


@pytest.mark.parametrize("label", [ANSWERABLE, AMBIGUOUS, UNSUPPORTED])
def test_without_a_typesafe_key_claude_classifies(no_typesafe_key, label):
    claude = claude_says(label)
    result = classify("how many orders?", "orders: order_id", claude=claude)
    assert result.label == label
    assert len(claude.calls) == 1
    # No calibrated confidence exists on this path, so none is invented; the
    # model is recorded so a trace or an eval says which classifier answered.
    assert result.confidence is None and result.probabilities is None
    assert result.model == MODEL and result.reason is None


def test_claude_gets_the_same_criteria_and_columns_as_jev(no_typesafe_key):
    claude = claude_says(ANSWERABLE)
    classify("how many orders?", "orders: order_id", claude=claude)
    (call,) = claude.calls
    system = call["system"][0]["text"]
    assert "orders: order_id" in system
    for label, criteria in _CRITERIA.items():
        assert f"{label}: {criteria}" in system
    # The enum makes the label a guarantee rather than a string to parse.
    schema = call["output_config"]["format"]["schema"]
    assert schema["properties"]["label"]["enum"] == [ANSWERABLE, AMBIGUOUS, UNSUPPORTED]
    assert call["system"][0]["cache_control"] == {"type": "ephemeral"}


def test_claude_sees_the_history_in_the_user_message_not_the_system_prompt(no_typesafe_key):
    """Same split as llm.generate_sql: the system prompt is the cached prefix,
    and the conversation is the user's text, so it does not belong in it."""
    claude = claude_says(ANSWERABLE)
    classify("and in 2018?", "orders: order_id", history="EARLIER: 2017?", claude=claude)
    (call,) = claude.calls
    assert "EARLIER" not in call["system"][0]["text"]
    assert call["messages"][0]["content"].endswith("CURRENT QUESTION: and in 2018?")


def test_a_jev_client_wins_over_claude_even_without_a_key(no_typesafe_key):
    claude = claude_says(UNSUPPORTED)
    result = classify("how many orders?", client=StubJev(ANSWERABLE), claude=claude)
    assert result.label == ANSWERABLE
    assert claude.calls == []


def test_with_a_typesafe_key_claude_is_not_asked(monkeypatch):
    """The owned TypeSafe client is replaced so no request leaves the test."""
    import twyd.classify

    monkeypatch.setenv("TYPESAFE_API_KEY", "set")
    jev = StubJev(AMBIGUOUS)

    class Owned:
        def __init__(self, model):
            pass

        def __enter__(self):
            return jev

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(twyd.classify, "TypeSafeClient", Owned)
    claude = claude_says(UNSUPPORTED)
    assert classify("how many orders?", claude=claude).label == AMBIGUOUS
    assert claude.calls == []


def test_the_deterministic_checks_run_before_either_classifier(no_typesafe_key):
    claude = claude_says(ANSWERABLE)
    assert classify("!!!", claude=claude).label == UNSUPPORTED
    assert claude.calls == []
