"""Offline tests for answer_question. The SQL is real, the model is a stub."""

import json

import pytest

from twyd.pipeline import answer_question


class _Blocks:
    def __init__(self, text):
        self.type = "text"
        self.text = text


class _Response:
    def __init__(self, text):
        self.content = [_Blocks(text)]
        self.stop_reason = "end_turn"


class _Messages:
    def __init__(self, replies):
        self._replies = list(replies)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return _Response(self._replies.pop(0))


class ScriptedClient:
    """Replies in order: the first call is SQL generation, the second prose."""

    def __init__(self, sql: str, answer: str = "An answer."):
        self.messages = _Messages([json.dumps({"sql": sql}), answer])

    @property
    def call_count(self):
        return len(self.messages.calls)


def test_a_good_question_runs_end_to_end(conn):
    client = ScriptedClient("SELECT count(*) AS n FROM orders", "There are 4 orders.")
    result = answer_question("how many orders are there?", conn, client=client)

    assert result.ok
    assert result.sql == "SELECT count(*) AS n FROM orders"
    assert result.result.rows == [(4,)]
    assert result.answer == "There are 4 orders."
    assert result.error is None
    assert client.call_count == 2


def test_unsafe_sql_is_rejected_before_it_reaches_the_database(conn):
    client = ScriptedClient("DROP TABLE orders")
    result = answer_question("delete everything", conn, client=client)

    assert not result.ok
    assert result.stage == "validate"
    assert "DROP" in result.error
    assert result.sql == "DROP TABLE orders"  # kept, so the failure is inspectable
    assert result.result is None
    assert client.call_count == 1  # no answer call on a failure


def test_a_file_reference_is_rejected_too(conn):
    client = ScriptedClient("SELECT * FROM 'secrets.csv'")
    result = answer_question("read the secrets file", conn, client=client)
    assert result.stage == "validate"
    assert "unknown table" in result.error


def test_a_broken_query_reports_the_database_error(conn):
    client = ScriptedClient("SELECT nonexistent_column FROM orders")
    result = answer_question("something impossible", conn, client=client)

    assert not result.ok
    assert result.stage == "execute"
    assert "nonexistent_column" in result.error
    assert client.call_count == 1


def test_an_empty_result_is_a_success_with_an_answer(conn):
    client = ScriptedClient(
        "SELECT * FROM orders WHERE order_status = 'nope'", "Nothing matched."
    )
    result = answer_question("orders with a status that does not exist", conn, client=client)

    assert result.ok
    assert result.result.row_count == 0
    assert result.answer == "Nothing matched."


@pytest.mark.parametrize("question", ["", "   "])
def test_an_empty_question_never_reaches_the_model(conn, question):
    client = ScriptedClient("SELECT 1")
    result = answer_question(question, conn, client=client)
    assert not result.ok
    assert result.stage == "generate"
    assert client.call_count == 0  # deterministic check, no tokens spent
