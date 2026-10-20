"""The eval harness must be trustworthy before its numbers mean anything.

Offline: a stub client plays the part of the model, so these tests check the
grading logic rather than the model's SQL.
"""

import json

import pytest
from conftest import StubJev

from twyd.eval import QUESTIONS, Result, _close, as_markdown, run_eval


class _Block:
    def __init__(self, text):
        self.type, self.text = "text", text


class _Response:
    def __init__(self, text):
        self.content, self.stop_reason = [_Block(text)], "end_turn"


class _Messages:
    def __init__(self, sql, clarification):
        self._sql = sql
        self._clarification = clarification
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        # Key off what the request asks for, not the call count: a failed stage
        # skips the prose call, which would knock any odd/even scheme out of
        # step, and two different calls now use structured output.
        fmt = kwargs.get("output_config", {}).get("format")
        if fmt:
            field = fmt["schema"]["required"][0]
            payload = (
                {"sql": self._sql, "assumption": ""}
                if field == "sql"
                else {field: self._clarification}
            )
            return _Response(json.dumps(payload))
        return _Response("An answer.")


class AlwaysClient:
    """Answers every question with the same SQL, and every clarification with
    the same question back."""

    def __init__(self, sql, clarification="Do you mean by revenue or by reviews?"):
        self.messages = _Messages(sql, clarification)


def test_the_question_set_is_what_the_docs_promise():
    assert len(QUESTIONS) == 21
    assert {q.id for q in QUESTIONS} == set(range(1, 22))
    assert len([q for q in QUESTIONS if q.reference]) == 11
    # Three follow-ups, two of which need an earlier turn to mean anything.
    assert [q.id for q in QUESTIONS if q.prior] == [19, 20]
    # Every question is graded on its label now, including the four that were
    # record-only when there was no classifier to grade.
    assert all(q.expected_label in ("ANSWERABLE", "AMBIGUOUS", "UNSUPPORTED") for q in QUESTIONS)
    # A question that should be declined has nothing to execute.
    assert all(
        q.reference is None and q.reference_rows is None
        for q in QUESTIONS
        if q.expected_label != "ANSWERABLE"
    )


def test_every_reference_query_runs_and_returns_one_value(conn):
    """A broken reference would grade the model against nothing."""
    from twyd.db import run_query

    for q in (q for q in QUESTIONS if q.reference):
        result = run_query(conn, q.reference)
        assert result.row_count == 1, f"question {q.id} reference returns {result.row_count} rows"
        assert len(result.columns) == 1, f"question {q.id} reference returns >1 column"


def test_a_correct_answer_passes(conn, jev):
    """The fixture db has 4 orders, so counting orders is the right answer."""
    results = run_eval(conn, client=AlwaysClient("SELECT count(*) FROM orders"), jev=jev)
    by_id = {r.question.id: r for r in results}
    assert by_id[1].verdict == "PASS"  # question 1 asks exactly this


def test_a_wrong_number_fails_rather_than_passing(conn, jev):
    results = run_eval(conn, client=AlwaysClient("SELECT 123456 AS wrong"), jev=jev)
    graded = [r for r in results if r.question.reference]
    assert all(r.verdict == "FAIL" for r in graded), [r.verdict for r in graded]


def test_unsafe_sql_counts_as_an_error_not_a_pass(conn, jev):
    results = run_eval(conn, client=AlwaysClient("DROP TABLE orders"), jev=jev)
    graded = [r for r in results if r.question.reference]
    assert all(r.verdict == "ERROR" for r in graded)
    assert all(r.answer.stage == "validate" for r in graded)


def test_a_question_with_no_reference_is_graded_on_its_label(conn):
    """The four record-only questions of iteration 1 are now gradeable: there
    is a classifier to be right or wrong about them."""
    results = run_eval(conn, client=AlwaysClient("SELECT 1"), jev=StubJev("UNSUPPORTED"))
    by_id = {r.question.id: r for r in results}
    for qid in (10, 11, 13, 14):  # the four UNSUPPORTED questions
        assert by_id[qid].verdict == "PASS", by_id[qid].why
        assert by_id[qid].answer.sql is None  # declined, so nothing ran


def test_a_wrong_label_fails_even_when_nothing_else_is_wrong(conn, jev):
    """The `jev` fixture calls everything ANSWERABLE, so every question that
    should have been declined is a failure - and says so."""
    results = run_eval(conn, client=AlwaysClient("SELECT count(*) FROM orders"), jev=jev)
    declined = [r for r in results if r.question.expected_label != "ANSWERABLE"]
    assert len(declined) == 8
    assert all(r.verdict == "FAIL" for r in declined)
    assert all("expected" in r.why for r in declined)


def test_an_ambiguous_question_that_does_not_ask_anything_fails(conn):
    """Labelling a question AMBIGUOUS and then saying nothing useful is not a
    pass: the point of the label is to ask."""
    results = run_eval(conn, client=AlwaysClient("SELECT 1", clarification="No question here."),
                       jev=StubJev("AMBIGUOUS"))
    by_id = {r.question.id: r for r in results}
    assert by_id[9].verdict == "FAIL"
    assert by_id[9].why == "declined without asking anything"


def test_a_row_count_is_graded_when_the_question_asks_for_many_rows(conn):
    """Question 17 exists because an answer must not miscount its own rows.
    The count is graded deterministically; the prose is read by hand."""
    results = run_eval(conn, client=AlwaysClient("SELECT 1 AS one"), jev=StubJev())
    by_id = {r.question.id: r for r in results}
    assert by_id[17].expected_rows is not None
    assert by_id[17].verdict == "FAIL"
    assert "rows, expected" in by_id[17].why


@pytest.mark.parametrize(
    ("actual", "expected", "same"),
    [
        (2.5665, 2.57, True),  # a model rounding to 2dp still matches
        (2.60, 2.57, False),
        # The case the first tolerance got wrong: average items per order over
        # all orders vs over orders that have items. Different questions.
        (1.1328, 1.1417, False),
        (99441, 99441, True),
        ("SP", "sp", True),  # case-insensitive string compare
        (None, 0, False),  # a missing result never matches a real one
    ],
)
def test_the_comparison_is_tolerant_of_floats_but_not_of_wrong(actual, expected, same):
    assert _close(actual, expected) is same


def test_the_report_names_every_question(conn, jev):
    results = run_eval(conn, client=AlwaysClient("SELECT count(*) FROM orders"), jev=jev)
    report = as_markdown(results)
    for q in QUESTIONS:
        assert q.question in report


def test_a_prior_turn_is_asked_first_and_becomes_the_history(conn, jev):
    """Question 19 is "and in 2018?", which means nothing on its own. The
    earlier turn runs through the same pipeline, so the history under test is
    the real thing rather than a hand-written transcript."""
    client = AlwaysClient("SELECT count(*) FROM orders")
    results = run_eval(conn, client=client, jev=jev)
    nineteen = next(r for r in results if r.question.id == 19)

    assert nineteen.question.prior == ("How many orders were placed in 2017?",)
    # The graded call saw the earlier question; the SQL calls carry history in
    # the user message, so it shows up there.
    contents = [c["messages"][0]["content"] for c in client.messages.calls]
    assert any("How many orders were placed in 2017?" in c and "and in 2018?" in c
               for c in contents)
