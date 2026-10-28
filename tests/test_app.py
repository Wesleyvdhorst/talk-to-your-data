"""The UI renders without touching the model.

Uses Streamlit's own AppTest harness: it runs app.py headlessly so a broken
widget call or a bad st.dataframe shape fails here instead of in the browser.
No question is entered, so answer_question is never called and no tokens are
spent. Anything beyond this is checked by running the app for real.
"""

from pathlib import Path

import pytest

APP = Path(__file__).parent.parent / "src" / "twyd" / "app.py"


@pytest.fixture
def app(monkeypatch, fixture_db):
    """Point the app at the fixture database via the env var it reads.

    The connection the app opens is released afterwards. st.cache_resource is
    process-wide and keyed on nothing, so without this the first app test to
    submit a question leaves a connection open for the rest of the session -
    and DuckDB refuses a second connection to the same file with a different
    configuration, which is exactly what a test opening it with plain
    duckdb.connect() does. It failed only in the full suite, never alone.
    """
    import gc

    import streamlit as st
    from streamlit.testing.v1 import AppTest

    monkeypatch.setenv("TWYD_DB", str(fixture_db))
    yield AppTest.from_file(str(APP), default_timeout=30)
    st.cache_resource.clear()
    gc.collect()  # drops the last reference, which closes the file


def test_the_app_renders_with_no_question_and_no_model_call(app):
    app.run()
    assert not app.exception
    assert "Talk with your data" in app.title[0].value
    # The example buttons are the only affordance before a question is typed.
    # A literal, so adding an example means looking at this test: every one of
    # them is a claim that the question works, which only a real run can say.
    assert len(app.button) == 24
    assert app.chat_input[0].value is None
    # One section per path through the pipeline, so each can be tried.
    assert [e.label for e in app.expander] == [
        "One number", "Bigger questions", "Asks back", "Declines",
        "Follow-ups", "Edge cases",
    ]
    # Nothing to replay yet, so no turn is rendered.
    assert not app.chat_message


def test_a_missing_database_is_reported_not_crashed(monkeypatch, tmp_path):
    from streamlit.testing.v1 import AppTest

    monkeypatch.setenv("TWYD_DB", str(tmp_path / "nope.duckdb"))
    at = AppTest.from_file(str(APP), default_timeout=30)
    at.run()
    assert not at.exception
    assert "No database" in at.error[0].value


def test_untrusted_text_is_never_rendered_as_markdown():
    """The answer and the error can quote review_comment_message, which is
    public free text containing real URLs. st.success/st.markdown would render
    it; st.text does not. Escaping was tried twice and bypassed twice (GFM
    autolinks need no syntax, and a leading backslash defeats the escaper), so
    the property worth pinning is that no Markdown widget sees that text.
    """
    source = (Path(__file__).parent.parent / "src" / "twyd" / "app.py").read_text()
    for widget in ("st.success(", "st.markdown(", "st.write("):
        assert widget not in source, f"{widget} renders Markdown; use st.text"
    assert "st.text(result.answer)" in source
    assert "st.text(result.error)" in source


def test_a_declined_question_is_rendered_as_neither_an_answer_nor_an_error():
    """Three outcomes, three renderings. A decline shown via st.error would
    read as a bug in the app; shown as an answer it would read as a finding."""
    source = (Path(__file__).parent.parent / "src" / "twyd" / "app.py").read_text()
    assert "if result.declined:" in source
    assert "st.text(result.message)" in source
    # ...and it must come first, because a decline is also `ok`.
    assert source.index("if result.declined:") < source.index("elif result.ok:")


def test_the_trace_is_rendered_from_the_pipeline_not_rebuilt_in_the_ui():
    """The app must not assemble its own account of what happened; that is how
    a UI ends up claiming a stage ran when it did not."""
    source = (Path(__file__).parent.parent / "src" / "twyd" / "app.py").read_text()
    assert "result.trace" in source
    assert "st.expander(\"Pipeline\")" in source


def test_the_assumption_is_shown_with_the_answer_and_not_as_markdown():
    """The choice behind a number belongs next to the number, not buried in
    the trace - and it is model output, so st.text renders it like the rest."""
    source = (Path(__file__).parent.parent / "src" / "twyd" / "app.py").read_text()
    assert "if result.assumption:" in source
    assert "st.text(result.assumption)" in source
    # Inside the answer branch, after the answer itself.
    assert source.index("st.text(result.answer)") < source.index("st.text(result.assumption)")
    assert source.index("st.text(result.assumption)") < source.index("else:\n        # The stage")


# Follow-up questions (milestone 12). The UI owns the transcript; the pipeline
# owns what the model is allowed to see of it.


@pytest.fixture
def stub_pipeline(monkeypatch):
    """Replace answer_question before the app imports it.

    AppTest executes app.py inside run(), and app.py does `from
    twyd.pipeline import answer_question` at that point, so patching the
    pipeline module beforehand is what the from-import picks up. No model
    call, no tokens, and the calls are recorded.
    """
    from twyd.pipeline import Answer

    calls = []

    def fake(question, conn, client=None, jev=None, history=()):
        calls.append((question, tuple(history)))
        return Answer(
            question=question,
            sql=f"SELECT '{len(calls)}' AS n",
            answer=f"Answer {len(calls)}.",
        )

    monkeypatch.setattr("twyd.pipeline.answer_question", fake)
    return calls


def test_a_second_question_carries_the_first_turn_as_history(app, stub_pipeline):
    app.run()
    app.chat_input[0].set_value("how many orders are there?").run()
    app.chat_input[0].set_value("and in 2018?").run()

    first, second = stub_pipeline
    assert first == ("how many orders are there?", ())
    question, history = second
    assert question == "and in 2018?"
    # One earlier turn, carrying its question and the SQL that answered it -
    # and no rows: the next query has to stand on its own result.
    assert len(history) == 1
    assert history[0].question == "how many orders are there?"
    assert history[0].sql == "SELECT '1' AS n"
    assert not hasattr(history[0], "result")


def test_both_turns_stay_on_screen(app, stub_pipeline):
    app.run()
    app.chat_input[0].set_value("first question").run()
    app.chat_input[0].set_value("second question").run()

    shown = [element.value for element in app.text]
    assert "first question" in shown and "Answer 1." in shown
    assert "second question" in shown and "Answer 2." in shown


def test_starting_a_new_conversation_drops_the_history(app, stub_pipeline):
    app.run()
    app.chat_input[0].set_value("first question").run()
    [reset] = [b for b in app.button if "new conversation" in b.label.lower()]
    reset.click().run()
    app.chat_input[0].set_value("unrelated question").run()

    assert stub_pipeline[-1] == ("unrelated question", ())


def test_the_ui_keeps_no_second_account_of_the_conversation():
    """The history sent to the model is derived from the answers themselves,
    so it cannot drift from what actually ran - the same reason Answer.trace
    is derived rather than recorded."""
    source = (Path(__file__).parent.parent / "src" / "twyd" / "app.py").read_text()
    assert "history=tuple(a.turn for a in st.session_state.answers)" in source


# The chart (milestone 14): drawn from the result's shape, never asked for.


def _answer_with(monkeypatch, columns, rows):
    from twyd.db import QueryResult
    from twyd.pipeline import Answer

    def fake(question, conn, client=None, jev=None, history=()):
        return Answer(question=question, sql="SELECT 1", answer="An answer.",
                      result=QueryResult(columns=columns, rows=rows))

    monkeypatch.setattr("twyd.pipeline.answer_question", fake)


def _charts(app):
    return app.get("vega_lite_chart")


def test_a_monthly_result_gets_a_chart_and_a_choice_of_column(app, monkeypatch):
    _answer_with(monkeypatch, ["month", "orders", "late_share"],
                 [("2017-01", 800, 0.05), ("2017-02", 1780, 0.07)])
    app.run()
    app.chat_input[0].set_value("orders and share late per month").run()
    assert not app.exception
    assert len(_charts(app)) == 1
    assert app.selectbox[0].options == ["orders", "late_share"]
    # The measure, not the count: the last numeric column is the default.
    assert app.selectbox[0].value == "late_share"
    # Switching column redraws on the rerun, without asking anything again.
    app.selectbox[0].select("orders").run()
    assert not app.exception
    assert len(_charts(app)) == 1


def test_a_single_number_gets_no_chart(app, monkeypatch):
    _answer_with(monkeypatch, ["customers"], [(96096,)])
    app.run()
    app.chat_input[0].set_value("how many customers?").run()
    assert not app.exception
    assert len(_charts(app)) == 0


def test_an_example_in_a_collapsed_section_is_still_asked_when_clicked(app, stub_pipeline):
    """Sections other than the first start collapsed; a collapsed expander
    still holds working buttons, and clicking one is the same as typing it."""
    app.run()
    [button] = [b for b in app.button if b.label == "What are the best products?"]
    button.click().run()
    assert stub_pipeline == [("What are the best products?", ())]


def test_the_chat_input_stays_after_an_example_is_clicked(app, stub_pipeline):
    """Clicking an example that gets a clarifying question must leave a box to
    answer it in. The input used to be skipped on the run that asked the
    example, so the question back had nowhere to be answered."""
    app.run()
    [button] = [b for b in app.button if b.label == "What are the best products?"]
    button.click().run()
    assert len(app.chat_input) == 1
    app.chat_input[0].set_value("by revenue").run()
    assert [q for q, _ in stub_pipeline] == ["What are the best products?", "by revenue"]
