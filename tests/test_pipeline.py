"""Offline tests for answer_question. The SQL is real, both models are stubs.

Two stubs now: ScriptedClient for Claude and StubJev (conftest) for the
classifier, passed separately so a test can hold one fixed while it varies
the other.
"""

import json

import pytest

from conftest import StubJev  # pytest puts tests/ on sys.path; it is not a package
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


class ClarifyingClient:
    """Replies with a clarification question: the only Claude call an
    AMBIGUOUS question makes."""

    def __init__(self, question: str):
        self.messages = _Messages([json.dumps({"question": question})])

    @property
    def call_count(self):
        return len(self.messages.calls)


class ScriptedClient:
    """Replies in order: the first call is SQL generation, the second prose."""

    def __init__(self, sql: str, answer: str = "An answer.", assumption: str = ""):
        self.messages = _Messages(
            [json.dumps({"sql": sql, "assumption": assumption}), answer]
        )

    @property
    def call_count(self):
        return len(self.messages.calls)


def test_a_good_question_runs_end_to_end(conn, jev):
    client = ScriptedClient("SELECT count(*) AS n FROM orders", "There are 4 orders.")
    result = answer_question("how many orders are there?", conn, client=client, jev=jev)

    assert result.ok
    assert result.sql == "SELECT count(*) AS n FROM orders"
    assert result.result.rows == [(4,)]
    assert result.answer == "There are 4 orders."
    assert result.error is None
    assert client.call_count == 2


def test_unsafe_sql_is_rejected_before_it_reaches_the_database(conn, jev):
    client = ScriptedClient("DROP TABLE orders")
    result = answer_question("delete everything", conn, client=client, jev=jev)

    assert not result.ok
    assert result.stage == "validate"
    assert "DROP" in result.error
    assert result.sql == "DROP TABLE orders"  # kept, so the failure is inspectable
    assert result.result is None
    assert client.call_count == 1  # no answer call on a failure


def test_a_file_reference_is_rejected_too(conn, jev):
    client = ScriptedClient("SELECT * FROM 'secrets.csv'")
    result = answer_question("read the secrets file", conn, client=client, jev=jev)
    assert result.stage == "validate"
    assert "unknown table" in result.error


def test_a_broken_query_reports_the_database_error(conn, jev):
    client = ScriptedClient("SELECT nonexistent_column FROM orders")
    result = answer_question("something impossible", conn, client=client, jev=jev)

    assert not result.ok
    assert result.stage == "execute"
    assert "nonexistent_column" in result.error
    assert client.call_count == 1


def test_an_empty_result_is_a_success_with_an_answer(conn, jev):
    client = ScriptedClient(
        "SELECT * FROM orders WHERE order_status = 'nope'", "Nothing matched."
    )
    result = answer_question("orders with a status that does not exist", conn, client=client, jev=jev)

    assert result.ok
    assert result.result.row_count == 0
    assert result.answer == "Nothing matched."


@pytest.mark.parametrize("question", ["", "   "])
def test_an_empty_question_never_reaches_the_model(conn, jev, question):
    client = ScriptedClient("SELECT 1")
    result = answer_question(question, conn, client=client, jev=jev)
    assert not result.ok
    assert result.stage == "generate"
    assert client.call_count == 0  # deterministic check, no tokens spent
    assert jev.call_count == 0  # and nothing spent on classifying it either


def test_the_answer_call_receives_the_same_schema_as_the_sql_call(conn, jev):
    """One context is built per question and reaches both calls."""
    client = ScriptedClient("SELECT count(*) AS n FROM orders", "There are 4 orders.")
    answer_question("how many orders?", conn, client=client, jev=jev)

    sql_call, answer_call = client.messages.calls
    assert "TABLE customers" in sql_call["system"]
    assert "TABLE customers" in answer_call["messages"][0]["content"]
    assert "RESULT: 1 row, 1 column (n), complete" in answer_call["messages"][0]["content"]


# Defects 3 and 4 of the baseline: `asdfgh` was answered through the SQL
# channel, and "what will sales be next month?" got a number. Both are now
# decided before any SQL exists, so the test that matters is that the SQL call
# never happens - not what the SQL would have said.


def test_an_unsupported_question_generates_no_sql_at_all(conn):
    client = ScriptedClient("SELECT 'should never run' AS message")
    result = answer_question("what will sales be next month?", conn, client=client,
                             jev=StubJev("UNSUPPORTED"))

    assert result.declined
    assert result.stage == "classify"
    assert result.sql is None  # nothing was generated...
    assert client.call_count == 0  # ...because the model was never asked
    assert result.result is None
    assert result.answer is None
    assert not result.error  # declining is an outcome, not a failure
    assert result.ok


def test_an_ambiguous_question_asks_instead_of_guessing(conn):
    """The one Claude call an ambiguous question makes is the clarification.
    No SQL is generated, so nothing is guessed at and nothing is executed."""
    client = ClarifyingClient("Do you mean by revenue or by review score?")
    result = answer_question("what are the best products?", conn, client=client,
                             jev=StubJev("AMBIGUOUS"))

    assert result.declined
    assert result.stage == "clarify"
    assert result.message == "Do you mean by revenue or by review score?"
    assert result.sql is None
    assert result.result is None
    assert client.call_count == 1  # the clarification, and nothing else


def test_the_clarification_call_gets_the_schema_so_it_offers_real_readings(conn):
    client = ClarifyingClient("by revenue or by units?")
    answer_question("best products?", conn, client=client, jev=StubJev("AMBIGUOUS"))
    call = client.messages.calls[0]
    assert "TABLE order_items" in call["system"]
    # Thinking off: a clarification that varies run to run is itself ambiguous.
    assert call["thinking"] == {"type": "between_tools"}
    assert call["output_config"]["format"]["schema"]["required"] == ["question"]


def test_the_decline_message_explains_the_scope_rather_than_apologising(conn):
    client = ScriptedClient("SELECT 1")
    result = answer_question("how old are our customers?", conn, client=client,
                             jev=StubJev("UNSUPPORTED"))
    assert "September 2016 to October 2018" in result.message
    assert "demographics" in result.message


def test_the_classification_is_kept_on_every_answer(conn, jev):
    """It is part of the trace: a wrong label has to be visible afterwards."""
    client = ScriptedClient("SELECT count(*) AS n FROM orders", "There are 4 orders.")
    result = answer_question("how many orders?", conn, client=client, jev=jev)

    assert result.classification.label == "ANSWERABLE"
    assert result.classification.confidence == 0.99
    assert result.classification.model == "jev-stub"


def test_classification_happens_before_sql_generation(conn, jev):
    """Order matters: the baseline's failure was asking for SQL first."""
    client = ScriptedClient("SELECT count(*) AS n FROM orders")
    answer_question("how many orders?", conn, client=client, jev=jev)
    assert jev.questions == ["how many orders?"]


# The trace: every stage the question passed through, derived from the fields
# the pipeline already set. What matters is that it ends where the question
# actually ended - a trace that claims a stage ran is worse than none.


def test_the_trace_of_an_answered_question_lists_every_stage(conn, jev):
    client = ScriptedClient("SELECT count(*) AS n FROM orders", "There are 4 orders.")
    result = answer_question("how many orders?", conn, client=client, jev=jev)
    assert [stage for stage, _ in result.trace] == [
        "classify",
        "generate_sql",
        "validate",
        "run_query",
        "generate_answer",
    ]
    assert "ANSWERABLE" in dict(result.trace)["classify"]
    assert dict(result.trace)["validate"] == "passed"
    assert dict(result.trace)["run_query"] == "1 rows"


def test_the_trace_stops_where_an_unsupported_question_stopped(conn):
    client = ScriptedClient("SELECT 1")
    result = answer_question("what will sales be next month?", conn, client=client,
                             jev=StubJev("UNSUPPORTED"))
    assert [stage for stage, _ in result.trace] == ["classify"]


def test_the_trace_shows_a_clarification_as_its_own_stage(conn):
    client = ClarifyingClient("by revenue or by units?")
    result = answer_question("best products?", conn, client=client,
                             jev=StubJev("AMBIGUOUS"))
    assert [stage for stage, _ in result.trace] == ["classify", "clarify"]


def test_the_trace_records_a_rejection_rather_than_claiming_the_query_ran(conn, jev):
    client = ScriptedClient("DROP TABLE orders")
    result = answer_question("delete everything", conn, client=client, jev=jev)
    trace = dict(result.trace)
    assert trace["validate"] == "rejected"
    assert "run_query" not in trace


def test_the_trace_says_a_deterministic_rejection_cost_nothing(conn):
    """The classifier's pre-checks are the one place a stage has no model
    behind it, and the trace has to say so."""
    client = ScriptedClient("SELECT 1")
    result = answer_question("!!!", conn, client=client, jev=StubJev())
    assert "no model call" in dict(result.trace)["classify"]


def test_the_classifier_is_given_the_columns_not_only_the_table_names(conn, jev):
    """It decides whether the data can answer the question, and that is a
    question about which columns exist."""
    client = ScriptedClient("SELECT count(*) AS n FROM orders", "4 orders.")
    answer_question("how many orders?", conn, client=client, jev=jev)
    assert "customer_unique_id" in jev.instructions[0]
    assert "orders: order_id, customer_id, order_status" in jev.instructions[0]


# The assumption travels with the SQL (milestone 11).


def test_the_assumption_reaches_the_answer_and_the_answer_call(conn, jev):
    client = ScriptedClient(
        "SELECT count(*) AS n FROM orders",
        assumption="orders with no items count as zero",
    )
    result = answer_question("average items per order", conn, client=client, jev=jev)

    assert result.assumption == "orders with no items count as zero"
    # Stated to the prose call rather than left for it to infer.
    answer_call = client.messages.calls[1]
    assert "orders with no items count as zero" in answer_call["messages"][0]["content"]


def test_the_trace_names_the_choice_on_the_generate_sql_step(conn, jev):
    """Not its own stage: no extra step ran, the step that ran learned a
    detail. A new stage string would claim otherwise."""
    client = ScriptedClient("SELECT count(*) AS n FROM orders", assumption="zeros count")
    result = answer_question("q", conn, client=client, jev=jev)

    stages = dict(result.trace)
    assert "zeros count" in stages["generate_sql"]
    assert "assumption" not in stages  # no invented stage


def test_a_fully_determined_question_adds_nothing_to_the_trace(conn, jev):
    client = ScriptedClient("SELECT count(*) AS n FROM orders", assumption="")
    result = answer_question("how many orders", conn, client=client, jev=jev)

    assert result.assumption == ""
    sql = "SELECT count(*) AS n FROM orders"
    assert dict(result.trace)["generate_sql"] == f"{len(sql)} characters"


def test_a_declined_question_has_no_assumption_at_all(conn):
    """None, not "": no SQL was generated, so no choice was made. The empty
    string means the opposite - the model considered it and found none."""
    client = ClarifyingClient("By best do you mean revenue or reviews?")
    result = answer_question(
        "what are the best products?", conn, client=client, jev=StubJev("AMBIGUOUS")
    )

    assert result.assumption is None


def test_rejected_sql_keeps_the_assumption_that_came_with_it(conn, jev):
    client = ScriptedClient("DROP TABLE orders", assumption="whatever it claimed")
    result = answer_question("drop it", conn, client=client, jev=jev)

    assert not result.ok
    assert result.assumption == "whatever it claimed"
