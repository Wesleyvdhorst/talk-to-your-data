"""Real model calls. Run on purpose: uv run --env-file .env pytest -m integration

These cost about $0.01 each. They check analytical meaning, not SQL strings:
two correct queries for the same question rarely look alike.
"""

import os

import pytest

from twyd.db import connect, run_query
from twyd.llm import generate_answer, generate_sql
from twyd.pipeline import answer_question
from twyd.schema import schema_context

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.environ.get("ANTHROPIC_API_KEY"),
        reason="needs ANTHROPIC_API_KEY (uv run --env-file .env pytest -m integration)",
    ),
]

REAL_DB = "db/olist.duckdb"


@pytest.fixture(scope="module")
def real_conn():
    from pathlib import Path

    path = Path(REAL_DB)
    if not path.is_file():
        pytest.skip(f"{REAL_DB} not built; run ingest() first")
    with connect(path) as c:
        yield c


def test_a_count_question_produces_valid_runnable_sql(real_conn):
    sql, _ = generate_sql("How many orders are there?", schema_context(real_conn))
    from twyd.sql_guard import validate

    validate(sql)
    result = run_query(real_conn, sql)
    assert result.rows == [(99441,)]


def test_the_customer_identity_trap(real_conn):
    """'How many customers' must mean distinct people, not rows.

    96,096 is correct (customer_unique_id); 99,441 is the trap
    (customer_id, which is per order). This is the single most important
    thing the schema notes have to teach the model.
    """
    sql, _ = generate_sql(
        "How many customers does the company have?", schema_context(real_conn)
    )
    result = run_query(real_conn, sql)
    assert result.rows[0][0] == 96096, f"got the per-order count from: {sql}"


def test_the_late_delivery_definition(real_conn):
    sql, _ = generate_sql(
        "How many orders were delivered later than estimated?",
        schema_context(real_conn),
    )
    result = run_query(real_conn, sql)
    assert result.rows[0][0] == 7827, f"wrong late definition in: {sql}"


def test_the_answer_is_grounded_in_the_result(real_conn):
    answer = generate_answer(
        "How many orders are there?",
        "SELECT count(*) AS n FROM orders",
        ["n"],
        [(99441,)],
    )
    assert "99,441" in answer or "99441" in answer


def test_an_empty_result_is_not_dressed_up_as_an_answer(real_conn):
    answer = generate_answer(
        "How many orders have the status 'teleported'?",
        "SELECT count(*) AS n FROM orders WHERE order_status = 'teleported'",
        ["n"],
        [(0,)],
    )
    assert "0" in answer or "no" in answer.lower()


def test_the_whole_pipeline_answers_a_real_question(real_conn):
    result = answer_question("What are the five most common order statuses?", real_conn)
    assert result.ok, result.error
    assert result.result.row_count == 5
    assert result.answer
    assert "delivered" in result.answer.lower()


def test_the_app_answers_a_question_end_to_end(monkeypatch):
    """Drives the real UI: question in, answer + SQL + table out.

    This is the only test that exercises app.py's render path with data, so a
    broken st.dataframe call or expander shows up here rather than in a demo.
    """
    from pathlib import Path

    from streamlit.testing.v1 import AppTest

    if not Path(REAL_DB).is_file():
        pytest.skip(f"{REAL_DB} not built; run ingest() first")

    monkeypatch.setenv("TWYD_DB", REAL_DB)
    app_path = Path(__file__).parent.parent / "src" / "twyd" / "app.py"
    app = AppTest.from_file(str(app_path), default_timeout=120)
    app.run()
    app.chat_input[0].set_value("How many orders are there?").run()

    assert not app.exception
    rendered = " ".join(t.value for t in app.text)
    assert rendered, f"no answer rendered; errors={[e.value for e in app.error]}"
    assert "99,441" in rendered or "99441" in rendered
    assert "from orders" in app.code[0].value.lower()
    assert app.dataframe  # the result table rendered

    # And the follow-up, in the real UI with a real model: two words that mean
    # nothing without the turn above them.
    app.chat_input[0].set_value("and in 2018?").run()
    assert not app.exception
    rendered = " ".join(t.value for t in app.text)
    assert "54,011" in rendered or "54011" in rendered, rendered


def test_clarify_names_readings_and_answers_nothing(real_conn):
    """A real clarification must ask, not answer. The check is that it offers
    alternatives and contains no number that would be an answer."""
    from twyd.llm import clarify

    question = clarify("What are the best products?", schema_context(real_conn))
    assert "?" in question
    lowered = question.lower()
    assert any(word in lowered for word in ("revenue", "sales", "units", "review"))
    assert " or " in lowered  # more than one reading offered


def test_the_question_with_two_defensible_answers_reports_its_choice(real_conn):
    """Question 8 of the eval set, and the iteration 2 headline failure.

    "The average number of items per order" is 1.1328 over all 99,441 orders
    and 1.1417 over the 98,666 that have items. Both are defensible, the
    sentence reads precisely, and the classifier is right to call it
    ANSWERABLE - so the pipeline cannot ask. What it can do is say which
    denominator it used. Asserted on there being an assumption at all, and on
    the answer carrying it; the wording is the model's to choose.
    """
    answer = answer_question("What is the average number of items per order?", real_conn)
    assert answer.ok, answer.error
    assert answer.assumption, "the question's denominator was left unstated"
    # The trace names it too, so a recorded eval run shows it without the UI.
    assert any(
        stage == "generate_sql" and answer.assumption in detail
        for stage, detail in answer.trace
    )


def test_the_assumption_is_prose_about_the_data_not_a_restatement_of_the_sql(real_conn):
    """The failure mode this field invites: paraphrasing the query back.

    "Grouped by customer_state and ordered descending" tells the reader
    nothing they could not see in the SQL, which is already on screen. The
    assumption has to be about which rows were counted.

    Note what this test does NOT assert any more. It was written expecting
    "how many orders are there?" to leave nothing open, and the model answered
    "counted every order regardless of status (including canceled and
    undelivered ones)" - which is a real choice with a real alternative, since
    2,965 orders were never delivered. The premise was wrong, not the output,
    so the assertion changed rather than the prompt.
    """
    _, assumption = generate_sql("How many orders are there?", schema_context(real_conn))
    assert len(assumption) < 200, f"a clause, not an essay: {assumption!r}"
    lowered = assumption.lower()
    assert not any(
        keyword in lowered
        for keyword in ("select ", "group by", "left join", "inner join", "count(")
    ), f"restates the query instead of the choice: {assumption!r}"


def test_a_clarification_can_be_answered_and_becomes_the_next_turn(real_conn):
    """Iteration 2's dead end, closed. The app asked which reading was meant
    and the user had no way to answer; now the answer is just the next turn.

    Two real turns: an ambiguous question that must ask back, then a two-word
    reply that only means anything with the first turn behind it.
    """
    first = answer_question("What are the best products?", real_conn)
    assert first.declined and first.stage == "clarify", first.trace
    assert "?" in first.message

    second = answer_question("by revenue", real_conn, history=(first.turn,))
    assert second.ok and second.sql, second.error or second.message
    assert second.classification.label == "ANSWERABLE", second.classification
    # The follow-up re-derives its own scope: revenue lives in order_items.
    assert "order_items" in second.sql.lower()


def test_a_follow_up_with_nothing_behind_it_asks_rather_than_guesses(real_conn):
    answer = answer_question("and for late deliveries?", real_conn)
    assert answer.declined, answer.answer
    assert answer.classification.label == "AMBIGUOUS", answer.classification
