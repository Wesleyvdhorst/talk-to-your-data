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
    """Point the app at the fixture database via the env var it reads."""
    from streamlit.testing.v1 import AppTest

    monkeypatch.setenv("TWYD_DB", str(fixture_db))
    return AppTest.from_file(str(APP), default_timeout=30)


def test_the_app_renders_with_no_question_and_no_model_call(app):
    app.run()
    assert not app.exception
    assert "Talk with your data" in app.title[0].value
    # The example buttons are the only affordance before a question is typed.
    assert len(app.button) == 4
    assert app.text_input[0].value == ""


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
