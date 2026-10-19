"""A thin Streamlit layer over answer_question.

Deliberately contains no logic: it collects a question, calls the pipeline,
and renders what comes back. Every decision about SQL, safety or grounding
lives in the modules underneath. If something here looks like it is deciding
anything, it belongs in pipeline.py instead.

Run with:  uv run --env-file .env streamlit run src/twyd/app.py
"""

import os
import re
from pathlib import Path

import streamlit as st

from twyd.db import connect
from twyd.pipeline import answer_question

# Env var so the tests can point the app at the fixture database. AppTest
# re-executes this file, so a monkeypatched module attribute would not survive.
DB_PATH = Path(os.environ.get("TWYD_DB", "db/olist.duckdb"))

EXAMPLES = [
    "How many customers does the company have?",
    "What is the average review score for late deliveries?",
    "How many orders were delivered later than estimated?",
    "Which product category has the highest average payment value per order?",
]


# st.cache_resource is shared across ALL sessions, not one per session.
# That matters because run_query's timeout calls conn.interrupt(): on a shared
# connection, one slow query being cancelled would cancel a concurrent one in
# another browser tab. Each run therefore gets its own cursor, which is a
# separate connection to the same database - it inherits read_only and
# enable_external_access, and interrupting it affects nothing else (verified).
@st.cache_resource
def _database():
    """The one process-wide connection. Shared by every session; never queried
    directly - callers take a cursor from it.
    """
    return connect(DB_PATH)


def _plain(text: str) -> str:
    r"""Escape the Markdown that turns text into links and images.

    st.success and st.error render Markdown, and the answer can legitimately
    quote review_comment_message - public free text that in this dataset
    really does contain raw URLs (5,591 reviews carry `[`, `!` or `http`).
    Rendered, that becomes a clickable link or an auto-fetched image pointing
    wherever the review's author chose. HTML is already inert here, since
    Streamlit escapes it unless unsafe_allow_html is set.
    """
    return re.sub(r"([\[\]!`])", r"\\\1", text)


st.set_page_config(page_title="Talk with your data", page_icon="📊")
st.title("Talk with your data")
st.caption("Ask about the Olist Brazilian e-commerce dataset. Answers come from SQL run read-only against DuckDB.")

if not DB_PATH.is_file():
    st.error(f"No database at {DB_PATH}. Run ingestion first (see README).")
    st.stop()

with st.sidebar:
    st.subheader("Example questions")
    for example in EXAMPLES:
        if st.button(example, width="stretch"):
            st.session_state.question = example

question = st.text_input(
    "Your question", key="question", placeholder="How many orders were late?"
)

if question:
    with st.spinner("Generating SQL and running it…"):
        result = answer_question(question, _database().cursor())

    if result.ok:
        st.success(_plain(result.answer))
    else:
        # The stage is the useful part: it says whether the model wrote
        # something unsafe, something broken, or something too slow.
        st.error(f"Failed at the {result.stage} stage: {_plain(result.error)}")

    if result.sql:
        with st.expander("SQL", expanded=not result.ok):
            st.code(result.sql, language="sql")

    if result.result is not None:
        with st.expander(f"Result ({result.result.row_count} rows)", expanded=True):
            if result.result.row_count:
                # A column -> values dict renders with real headers and keeps
                # pandas out of the dependency list.
                st.dataframe(
                    {
                        name: [row[i] for row in result.result.rows]
                        for i, name in enumerate(result.result.columns)
                    },
                    width="stretch",
                )
            else:
                st.info("The query ran and matched no rows.")
            if result.result.truncated:
                st.warning("Showing the first rows only; the result was truncated.")
