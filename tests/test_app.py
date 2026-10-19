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


ZWSP = "\u200b"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # Explicit image and link syntax.
        ("![](http://evil/x)", rf"\!\[\](http:{ZWSP}//evil/x)"),
        ("see [link](http://evil) now", rf"see \[link\](http:{ZWSP}//evil) now"),
        # GFM autolinks all three of these with no syntax at all.
        ("see http://evil/x now", f"see http:{ZWSP}//evil/x now"),
        ("visit www.evil.com", f"visit w{ZWSP}ww.evil.com"),
        ("mail me at a@b.com", f"mail me at a@{ZWSP}b.com"),
        # Ordinary prose must survive untouched, or the fix is worse than the bug.
        (
            "There are 96,096 customers (distinct people). Note: averaged per order.",
            "There are 96,096 customers (distinct people). Note: averaged per order.",
        ),
        ("*emphasis* has no destination", "*emphasis* has no destination"),
    ],
)
def test_text_that_would_reach_out_to_a_url_is_defused(raw, expected):
    """Answers can quote review_comment_message, which is public free text and
    in this dataset really does contain raw URLs. st.success renders Markdown
    with GFM autolinking, so a bare URL needs no syntax to become a link.
    """
    from twyd.app import _plain

    assert _plain(raw) == expected
