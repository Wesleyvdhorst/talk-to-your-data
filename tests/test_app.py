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


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("![](http://evil/x)", r"\!\[\](http://evil/x)"),
        ("see [link](http://evil) now", r"see \[link\](http://evil) now"),
        ("There are 96,096 customers (distinct people).", "There are 96,096 customers (distinct people)."),
    ],
)
def test_markdown_that_would_render_a_link_or_image_is_escaped(raw, expected):
    """Answers can quote review_comment_message, which is public free text and
    in this dataset really does contain raw URLs. st.success renders Markdown.
    """
    from twyd.app import _plain

    assert _plain(raw) == expected
