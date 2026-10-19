"""A thin Streamlit layer over answer_question.

Deliberately contains no logic: it collects a question, calls the pipeline,
and renders what comes back. Every decision about SQL, safety or grounding
lives in the modules underneath. If something here looks like it is deciding
anything, it belongs in pipeline.py instead.

Run with:  uv run --env-file .env streamlit run src/twyd/app.py
"""

import os
from pathlib import Path

import streamlit as st

from twyd.db import connect
from twyd.pipeline import answer_question

# Env var so the tests can point the app at the fixture database. AppTest
# re-executes this file, so a monkeypatched module attribute would not survive.
DB_PATH = Path(os.environ.get("TWYD_DB", "db/olist.duckdb"))

# Every one of these was run through the pipeline once before being put here:
# an example that errors or gets a clarification is worse than no example. The
# quick ones are single numbers that each hit a trap in docs/data_model.md; the
# bigger ones span three or four tables, return 10-27 rows, or compute a value
# no column holds.
QUICK_EXAMPLES = [
    "How many customers does the company have?",
    "How many orders were delivered later than estimated?",
    "What is the average review score for late deliveries?",
    "What share of orders are paid in more than one instalment?",
]

BIGGER_EXAMPLES = [
    "For each month, how many orders were placed and what share of them arrived late?",
    "What is the average delivery time in days per customer state?",
    "Which sellers have the most late deliveries, and what is their average review score?",
    "Which product categories have the worst average review scores, counting only categories with at least 500 reviews?",
    "Do orders with more items get worse review scores?",
    "How much of total revenue comes from the top 10% of customers?",
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


st.set_page_config(page_title="Talk with your data", page_icon="📊")
st.title("Talk with your data")
st.caption("Ask about the Olist Brazilian e-commerce dataset. Answers come from SQL run read-only against DuckDB.")

if not DB_PATH.is_file():
    st.error(f"No database at {DB_PATH}. Run ingestion first (see README).")
    st.stop()

with st.sidebar:
    for heading, examples in (
        ("One number", QUICK_EXAMPLES),
        ("Bigger questions", BIGGER_EXAMPLES),
    ):
        st.subheader(heading)
        for example in examples:
            if st.button(example, width="stretch"):
                st.session_state.question = example

question = st.text_input(
    "Your question", key="question", placeholder="How many orders were late?"
)

if question:
    with st.spinner("Generating SQL and running it…"):
        result = answer_question(question, _database().cursor())

    # st.text renders plain text. Everything below is either model output or a
    # database message, and both can quote review_comment_message - public free
    # text that in this dataset really does contain raw URLs. Rendered as
    # Markdown, that text can fetch an image or link anywhere its author chose,
    # and escaping it is a game of enumerating constructs: I escaped the
    # bracket forms, missed GFM's syntax-free autolinks, fixed those, and still
    # had a backslash bypass (\[x](url) survives any escaper that does not
    # escape the escape first). Not rendering Markdown ends the whole class.
    if result.declined:
        # Not an answer and not a failure: the pipeline understood the question
        # well enough to say it will not answer it. Its own caption, so the
        # distinction is visible rather than inferred from the wording.
        with st.container(border=True):
            st.caption(
                "A question back to you"
                if result.stage == "clarify"
                else f"No answer — classified {result.classification.label}"
            )
            st.text(result.message)
    elif result.ok:
        with st.container(border=True):
            st.caption("Answer")
            st.text(result.answer)
            if result.assumption:
                # The choice the query made that the question left open. Shown
                # next to the answer rather than buried in the trace, because
                # it is the one thing the number cannot be judged without.
                st.caption("The query assumed")
                st.text(result.assumption)
    else:
        # The stage is our own string and says what went wrong; the detail
        # below it is untrusted, so it stays plain.
        st.error(f"Failed at the {result.stage} stage.")
        st.text(result.error)

    if result.trace:
        # The pipeline's own account of where the question went, including
        # where it stopped. Derived in pipeline.py, so it cannot claim a stage
        # that did not run.
        with st.expander("Pipeline"):
            st.text("\n".join(f"{stage:16} {detail}" for stage, detail in result.trace))

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
