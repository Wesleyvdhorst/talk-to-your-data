"""Offline tests for the two LLM calls. No network, no API key needed.

The stub client records the request and returns a canned response, so these
tests check the request we send and our handling of what comes back - the
parts we own. Whether the model writes good SQL is an integration concern.
"""

import json

import pytest

from twyd.llm import (
    ANSWER_EFFORT,
    ANSWER_ROWS,
    CLARIFY_EFFORT,
    CLARIFY_MAX_TOKENS,
    MODEL,
    SQL_EFFORT,
    clarify,
    generate_answer,
    generate_sql,
)


class StubBlock:
    def __init__(self, text: str, type: str = "text"):
        self.type = type
        self.text = text


class StubResponse:
    def __init__(self, blocks, stop_reason="end_turn"):
        self.content = blocks
        self.stop_reason = stop_reason


class StubMessages:
    def __init__(self, response):
        self._response = response
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self._response


class StubClient:
    """Returns whatever blocks it is given, and remembers what it was asked."""

    def __init__(self, blocks, stop_reason="end_turn"):
        self.messages = StubMessages(StubResponse(blocks, stop_reason))

    @property
    def calls(self):
        return self.messages.calls


def sql_client(sql: str, assumption: str = "") -> StubClient:
    return StubClient([StubBlock(json.dumps({"sql": sql, "assumption": assumption}))])


def test_generate_sql_returns_the_sql_from_the_structured_response():
    client = sql_client("SELECT count(*) FROM orders")
    assert generate_sql("how many orders", "SCHEMA", client=client) == (
        "SELECT count(*) FROM orders",
        "",
    )


def test_generate_sql_sends_the_schema_and_the_question():
    client = sql_client("SELECT 1")
    generate_sql("how many orders?", "TABLE orders (99,441 rows)", client=client)
    (call,) = client.calls
    assert "TABLE orders (99,441 rows)" in call["system"][0]["text"]
    assert call["messages"] == [{"role": "user", "content": "how many orders?"}]


def test_generate_sql_asks_for_the_two_fields_and_nothing_else():
    """The output schema is what replaces temperature=0 and prefill here."""
    client = sql_client("SELECT 1")
    generate_sql("q", "SCHEMA", client=client)
    schema = client.calls[0]["output_config"]["format"]["schema"]
    assert schema["required"] == ["sql", "assumption"]
    assert schema["additionalProperties"] is False


def test_the_settings_are_the_ones_we_decided():
    client = sql_client("SELECT 1")
    generate_sql("q", "SCHEMA", client=client)
    call = client.calls[0]
    assert call["model"] == MODEL == "claude-sonnet-5-5"
    assert call["output_config"]["effort"] == SQL_EFFORT
    # Sonnet 5.5 rejects both of these with a 400. Pinned so neither comes back.
    assert "temperature" not in call
    assert "thinking" not in call


def test_generate_answer_passes_the_result_and_asks_for_grounding():
    client = StubClient([StubBlock("There are 4 orders.")])
    answer = generate_answer(
        "how many orders", "SELECT count(*) FROM orders", ["n"], [(4,)], client=client
    )
    assert answer == "There are 4 orders."
    call = client.calls[0]
    assert call["output_config"]["effort"] == ANSWER_EFFORT
    assert "SELECT count(*) FROM orders" in call["messages"][0]["content"]
    assert '"rows": [[4]]' in call["messages"][0]["content"]
    assert "only numbers that appear in the result" in call["system"]


def test_generate_answer_tells_the_model_when_the_result_was_truncated():
    client = StubClient([StubBlock("ok")])
    generate_answer("q", "SELECT 1", ["n"], [(1,)], truncated=True, client=client)
    assert "truncated" in client.calls[0]["messages"][0]["content"]


def test_an_empty_result_is_still_sent_to_the_model():
    """Zero rows is an answerable outcome, not a failure to report."""
    client = StubClient([StubBlock("Nothing matched.")])
    assert generate_answer("q", "SELECT 1", ["n"], [], client=client) == "Nothing matched."
    assert '"rows": []' in client.calls[0]["messages"][0]["content"]


def test_a_response_with_no_text_block_raises_rather_than_returning_empty():
    client = StubClient([StubBlock("", type="thinking")], stop_reason="refusal")
    with pytest.raises(ValueError, match="no text block"):
        generate_sql("q", "SCHEMA", client=client)


# The two grounding defects recorded in docs/eval_results.md. Both were claims
# ABOUT the result rather than values from it: a 20-row result described as 19
# rows, and customer_unique_id explained as "distinct customer IDs". Neither
# can be tested by asserting on prose, so these pin the inputs instead - the
# row count is now a stated fact and the column meaning is now available.


def test_the_row_count_is_stated_as_a_fact_not_left_to_the_model():
    client = StubClient([StubBlock("ok")])
    rows = [(i, f"p{i}") for i in range(20)]
    generate_answer("best products", "SELECT 1", ["product_id", "name"], rows, client=client)
    content = client.calls[0]["messages"][0]["content"]
    assert "RESULT: 20 rows" in content
    assert "2 columns (product_id, name)" in content
    assert "complete" in content


def test_one_row_and_one_column_are_not_pluralised():
    client = StubClient([StubBlock("ok")])
    generate_answer("q", "SELECT 1", ["n"], [(4,)], client=client)
    assert "RESULT: 1 row, 1 column (n), complete" in client.calls[0]["messages"][0]["content"]


def test_truncation_is_stated_as_more_rows_matched():
    client = StubClient([StubBlock("ok")])
    generate_answer("q", "SELECT 1", ["n"], [(1,)], truncated=True, client=client)
    content = client.calls[0]["messages"][0]["content"]
    assert "truncated at the row cap" in content
    assert "complete" not in content


def test_the_answer_call_gets_the_schema_so_it_can_say_what_a_column_means():
    client = StubClient([StubBlock("ok")])
    generate_answer(
        "how many customers",
        "SELECT count(DISTINCT customer_unique_id) AS n FROM customers",
        ["n"],
        [(96096,)],
        schema_context="customer_unique_id identifies the real person",
        client=client,
    )
    assert "customer_unique_id identifies the real person" in (
        client.calls[0]["messages"][0]["content"]
    )


def test_the_schema_section_is_absent_when_no_schema_is_given():
    """generate_answer stays usable without the database layer."""
    client = StubClient([StubBlock("ok")])
    generate_answer("q", "SELECT 1", ["n"], [(1,)], client=client)
    assert "Schema" not in client.calls[0]["messages"][0]["content"]


def test_the_answer_prompt_constrains_claims_about_the_result_not_only_numbers():
    client = StubClient([StubBlock("ok")])
    generate_answer("q", "SELECT 1", ["n"], [(1,)], client=client)
    system = client.calls[0]["system"]
    assert "must agree with the RESULT line" in system
    assert "Do not count the rows" in system  # wrapped in the prompt source


def test_the_prompt_does_not_invite_reciting_the_shape_of_a_single_value():
    """The first version of the RESULT rule made every answer end with
    "The result is 1 row, 1 column (n), complete." - grounded, and noise."""
    client = StubClient([StubBlock("ok")])
    generate_answer("q", "SELECT 1", ["n"], [(1,)], client=client)
    assert "do not" in client.calls[0]["system"]
    assert "recite the RESULT line back" in client.calls[0]["system"]


def test_the_answer_call_sees_at_most_answer_rows_but_the_true_count():
    """"List every order id" sent all 1,000 rows: 8x the cost of a normal
    question, and the prose quoted the RESULT line back verbatim."""
    client = StubClient([StubBlock("ok")])
    rows = [(i,) for i in range(ANSWER_ROWS + 5)]
    generate_answer("q", "SELECT 1", ["n"], rows, client=client)
    content = client.calls[0]["messages"][0]["content"]
    assert f"RESULT: {ANSWER_ROWS + 5} rows" in content
    assert f"only the first {ANSWER_ROWS} are listed" in content
    assert f"[{ANSWER_ROWS - 1}]" in content and f"[{ANSWER_ROWS}]" not in content
    assert "never quote the RESULT line" in client.calls[0]["system"]


def test_a_result_within_answer_rows_is_sent_whole_without_a_listed_note():
    client = StubClient([StubBlock("ok")])
    generate_answer("q", "SELECT 1", ["n"], [(i,) for i in range(ANSWER_ROWS)], client=client)
    assert "only the first" not in client.calls[0]["messages"][0]["content"]


def clarify_client(question: str) -> StubClient:
    return StubClient([StubBlock(json.dumps({"question": question}))])


def test_clarify_returns_the_question_and_never_an_answer():
    client = clarify_client("By best, do you mean revenue or review score?")
    assert (
        clarify("what are the best products?", "SCHEMA", client=client)
        == "By best, do you mean revenue or review score?"
    )


def test_clarify_is_told_to_name_the_readings_and_pick_none():
    client = clarify_client("revenue or reviews?")
    clarify("best products?", "TABLE order_items", client=client)
    system = client.calls[0]["system"][0]["text"]
    assert "TABLE order_items" in system  # only readings the schema supports
    assert "Do not pick one" in system
    assert "do not answer the question" in system


def test_clarify_runs_with_thinking_off_and_a_tight_cap():
    """Settings worth defending: nothing here needs reasoning, and a
    clarification that varies between runs is itself a kind of ambiguity.
    between_tools is the only way to disable thinking on this model, and it
    is accepted only at effort high or below."""
    client = clarify_client("revenue or reviews?")
    clarify("q", "SCHEMA", client=client)
    call = client.calls[0]
    assert call["thinking"] == {"type": "between_tools"}
    assert call["output_config"]["effort"] == CLARIFY_EFFORT == "low"
    assert call["max_tokens"] == CLARIFY_MAX_TOKENS == 256


# The modelling choice behind a number (milestone 11). The eval recorded
# "average items per order" as ANSWERABLE at 0.98 and still returned 1.1328
# where the reference said 1.1417: two defensible denominators, one sentence.
# Nothing here detects that - these pin that the choice travels, and that an
# absent choice is not invented.


def test_the_assumption_comes_back_alongside_the_sql():
    client = sql_client("SELECT avg(n) FROM t", "orders with no items count as zero")
    generated = generate_sql("average items per order", "SCHEMA", client=client)
    assert generated.sql == "SELECT avg(n) FROM t"
    assert generated.assumption == "orders with no items count as zero"


def test_a_response_without_an_assumption_raises_rather_than_defaulting():
    """Defaulting to "" would turn a broken response into a question that
    silently looks fully determined - the one case this field exists for."""
    client = StubClient([StubBlock(json.dumps({"sql": "SELECT 1"}))])
    with pytest.raises(KeyError):
        generate_sql("q", "SCHEMA", client=client)


def test_the_sql_prompt_asks_for_the_choice_and_forbids_inventing_one():
    client = sql_client("SELECT 1")
    generate_sql("q", "SCHEMA", client=client)
    system = client.calls[0]["system"][0]["text"]
    assert "would change the number if made differently" in system
    assert "do not manufacture a choice you did not make" in system


def test_the_assumption_is_stated_to_the_answer_call_as_a_fact():
    client = StubClient([StubBlock("ok")])
    generate_answer(
        "average items per order",
        "SELECT avg(n) FROM t",
        ["avg"],
        [(1.1328,)],
        assumption="orders with no items count as zero",
        client=client,
    )
    content = client.calls[0]["messages"][0]["content"]
    assert "ASSUMPTION" in content
    assert "orders with no items count as zero" in content
    assert "state that choice in a clause of its own" in client.calls[0]["system"]


def test_no_assumption_line_is_sent_when_the_question_determined_the_query():
    """An empty assumption must not become an empty ASSUMPTION heading: the
    model would then explain a choice that was never made."""
    client = StubClient([StubBlock("ok")])
    generate_answer("q", "SELECT 1", ["n"], [(1,)], assumption="", client=client)
    assert "ASSUMPTION" not in client.calls[0]["messages"][0]["content"]


# Where history goes, which is the security-relevant part (milestone 12).


def test_history_goes_in_the_user_message_and_never_the_system_prompt():
    """Two reasons, both deliberate. The system prompt is the stable prefix -
    caching is a prefix match, so a per-turn string in it would invalidate the
    schema context behind it on every question. And earlier turns are the
    user's own text: in the system prompt they would carry operator authority,
    which is how a question talks the rules out of a model.
    """
    client = sql_client("SELECT 1")
    generate_sql("and in 2018?", "SCHEMA", client=client, history="EARLIER: 2017?")
    call = client.calls[0]
    assert "EARLIER: 2017?" in call["messages"][0]["content"]
    assert "EARLIER: 2017?" not in call["system"][0]["text"]
    # The current question is labelled, so there is no doubt which to answer.
    assert "CURRENT QUESTION (write SQL for this one): and in 2018?" in (
        call["messages"][0]["content"]
    )


def test_without_history_the_user_message_is_the_bare_question():
    client = sql_client("SELECT 1")
    generate_sql("how many orders?", "SCHEMA", client=client)
    assert client.calls[0]["messages"] == [
        {"role": "user", "content": "how many orders?"}
    ]


def test_the_sql_prompt_forbids_reusing_an_earlier_number():
    client = sql_client("SELECT 1")
    generate_sql("q", "SCHEMA", client=client)
    assert "never quote a number from an earlier turn" in client.calls[0]["system"][0]["text"]


# Prompt caching (milestone 13). Nothing caches unless asked, and the first
# metered eval run read 0 tokens from cache across 21 questions.


@pytest.mark.parametrize("call", ["sql", "clarify"])
def test_the_stable_system_prompt_ends_in_a_cache_breakpoint(call):
    """The breakpoint closes the system prompt - rules plus schema, the same
    bytes every question - so the question after it never becomes part of
    what is cached, and history (user message) cannot invalidate it."""
    if call == "sql":
        client = sql_client("SELECT 1")
        generate_sql("q", "SCHEMA", client=client, history="EARLIER: 2017?")
    else:
        client = clarify_client("which?")
        clarify("q", "SCHEMA", client=client)
    (block,) = client.calls[0]["system"]
    assert block["cache_control"] == {"type": "ephemeral"}
    assert "SCHEMA" in block["text"]
