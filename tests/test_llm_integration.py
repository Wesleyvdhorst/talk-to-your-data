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
    sql = generate_sql("How many orders are there?", schema_context(real_conn))
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
    sql = generate_sql("How many customers does the company have?", schema_context(real_conn))
    result = run_query(real_conn, sql)
    assert result.rows[0][0] == 96096, f"got the per-order count from: {sql}"


def test_the_late_delivery_definition(real_conn):
    sql = generate_sql(
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
    app.text_input[0].set_value("How many orders are there?").run()

    assert not app.exception
    rendered = " ".join(t.value for t in app.text)
    assert rendered, f"no answer rendered; errors={[e.value for e in app.error]}"
    assert "99,441" in rendered or "99441" in rendered
    assert "FROM orders" in app.code[0].value.lower() or "from orders" in app.code[0].value.lower()
    assert app.dataframe  # the result table rendered
