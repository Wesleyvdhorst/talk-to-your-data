"""The eval harness must be trustworthy before its numbers mean anything.

Offline: a stub client plays the part of the model, so these tests check the
grading logic rather than the model's SQL.
"""

import json

import pytest

from twyd.eval import QUESTIONS, Result, _close, as_markdown, run_eval


class _Block:
    def __init__(self, text):
        self.type, self.text = "text", text


class _Response:
    def __init__(self, text):
        self.content, self.stop_reason = [_Block(text)], "end_turn"


class _Messages:
    def __init__(self, sql):
        self._sql = sql
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        # Key off the request, not the call count: a failed stage skips the
        # prose call, which would knock any odd/even scheme out of step.
        if "format" in kwargs.get("output_config", {}):
            return _Response(json.dumps({"sql": self._sql}))
        return _Response("An answer.")


class AlwaysClient:
    """Answers every question with the same SQL."""

    def __init__(self, sql):
        self.messages = _Messages(sql)


def test_the_question_set_is_what_the_docs_promise():
    graded = [q for q in QUESTIONS if q.reference]
    assert len(QUESTIONS) == 12
    assert len(graded) == 8
    assert {q.id for q in QUESTIONS} == set(range(1, 13))


def test_every_reference_query_runs_and_returns_one_value(conn):
    """A broken reference would grade the model against nothing."""
    from twyd.db import run_query

    for q in (q for q in QUESTIONS if q.reference):
        result = run_query(conn, q.reference)
        assert result.row_count == 1, f"question {q.id} reference returns {result.row_count} rows"
        assert len(result.columns) == 1, f"question {q.id} reference returns >1 column"


def test_a_correct_answer_passes(conn):
    """The fixture db has 4 orders, so counting orders is the right answer."""
    results = run_eval(conn, client=AlwaysClient("SELECT count(*) FROM orders"))
    by_id = {r.question.id: r for r in results}
    assert by_id[1].verdict == "PASS"  # question 1 asks exactly this


def test_a_wrong_number_fails_rather_than_passing(conn):
    results = run_eval(conn, client=AlwaysClient("SELECT 123456 AS wrong"))
    graded = [r for r in results if r.question.reference]
    assert all(r.verdict == "FAIL" for r in graded), [r.verdict for r in graded]


def test_unsafe_sql_counts_as_an_error_not_a_pass(conn):
    results = run_eval(conn, client=AlwaysClient("DROP TABLE orders"))
    graded = [r for r in results if r.question.reference]
    assert all(r.verdict == "ERROR" for r in graded)
    assert all(r.answer.stage == "validate" for r in graded)


def test_record_only_questions_are_never_scored(conn):
    results = run_eval(conn, client=AlwaysClient("SELECT 1"))
    record = [r for r in results if r.question.reference is None]
    assert len(record) == 4
    assert all(r.verdict == "RECORD" for r in record)


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


def test_the_report_names_every_question(conn):
    results = run_eval(conn, client=AlwaysClient("SELECT count(*) FROM orders"))
    report = as_markdown(results)
    for q in QUESTIONS:
        assert q.question in report
