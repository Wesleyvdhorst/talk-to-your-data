"""Offline tests for the two LLM calls. No network, no API key needed.

The stub client records the request and returns a canned response, so these
tests check the request we send and our handling of what comes back - the
parts we own. Whether the model writes good SQL is an integration concern.
"""

import json

import pytest

from twyd.llm import (
    ANSWER_EFFORT,
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


def sql_client(sql: str) -> StubClient:
    return StubClient([StubBlock(json.dumps({"sql": sql}))])


def test_generate_sql_returns_the_sql_from_the_structured_response():
    client = sql_client("SELECT count(*) FROM orders")
    assert (
        generate_sql("how many orders", "SCHEMA", client=client)
        == "SELECT count(*) FROM orders"
    )


def test_generate_sql_sends_the_schema_and_the_question():
    client = sql_client("SELECT 1")
    generate_sql("how many orders?", "TABLE orders (99,441 rows)", client=client)
    (call,) = client.calls
    assert "TABLE orders (99,441 rows)" in call["system"]
    assert call["messages"] == [{"role": "user", "content": "how many orders?"}]


def test_generate_sql_asks_for_a_single_sql_field_and_nothing_else():
    """The output schema is what replaces temperature=0 and prefill here."""
    client = sql_client("SELECT 1")
    generate_sql("q", "SCHEMA", client=client)
    schema = client.calls[0]["output_config"]["format"]["schema"]
    assert schema["required"] == ["sql"]
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
    assert "must repeat the RESULT line" in system
    assert "Do not count the rows" in system  # wrapped in the prompt source


def test_the_prompt_does_not_invite_reciting_the_shape_of_a_single_value():
    """The first version of the RESULT rule made every answer end with
    "The result is 1 row, 1 column (n), complete." - grounded, and noise."""
    client = StubClient([StubBlock("ok")])
    generate_answer("q", "SELECT 1", ["n"], [(1,)], client=client)
    assert "do not" in client.calls[0]["system"]
    assert "recite the RESULT line back" in client.calls[0]["system"]


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
    system = client.calls[0]["system"]
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
