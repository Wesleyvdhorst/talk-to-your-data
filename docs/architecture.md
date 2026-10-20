# Architecture

How the app is put together, and why it is safe to point at a database. For
running it, see the README; for what it gets wrong, see `limitations.md`.

## How it works

`answer_question(question, conn)` is the whole pipeline. One pass, no loops:

```
question (+ up to 3 earlier turns, capped at 2,000 characters)
  -> column overview     one line per table: its columns, ~266 tokens
  -> classify            Jev, three labels, over those columns and the history
  -> clarify             if AMBIGUOUS: LLM call asking which reading, turn ends
                         if UNSUPPORTED: fixed scope message, turn ends
  -> schema context      built from the live database + verified semantic notes
  -> generate_sql        LLM call #1, structured output, {sql, assumption}
                         history goes in the USER message, never the system prompt
  -> validate            parse tree: one SELECT, nine known tables, no table functions
  -> run_query           read-only DuckDB, 1,000-row cap, 30s timeout
  -> generate_answer     LLM call #2, grounded in the rows, their shape and the assumption
  -> Answer(classification, sql, assumption, result, answer, message, error, stage)
```

Three outcomes, kept apart: an **answer** (grounded in a result), a
**message** (the pipeline declined or asked back - unsupported or
underspecified, and no SQL was generated), or an **error** (something broke).
A decline is an outcome, not a failure.

`Answer.trace` lists the stages the question actually passed through and is
shown in the UI. `Answer.turn` is the same question as the *next* one may see
it. Both are derived from the fields above rather than recorded alongside
them, so neither can claim a stage that did not run or SQL that was never
generated.

A follow-up is the same mechanism as a clarification: "and in 2018?" or "by
revenue" is answered by giving the next turn the earlier questions and the SQL
that answered them — **never the rows**. Scope is re-derived by querying
again, because every grounded claim in this app comes from a result its own
query just produced. History is capped at 3 turns **and** 2,000 characters,
oldest dropped, never summarised.

| Module | Responsibility |
|---|---|
| `ingest.py` | CSVs → DuckDB, deterministic, no LLM |
| `db.py` | read-only connections, execution, row cap, timeout |
| `schema.py` | the prompt's schema context: columns introspected, semantics hand-written |
| `sql_guard.py` | treats generated SQL as untrusted; allowlist over the parse tree |
| `classify.py` | ANSWERABLE / AMBIGUOUS / UNSUPPORTED from the column list, before any SQL exists |
| `llm.py` | both Claude calls; the only file that knows that provider |
| `pipeline.py` | wires the above; returns failures as fields, never as empty results |
| `app.py` | thin Streamlit layer; contains no logic |
| `eval.py` | the fixed evaluation set and its runner |

Safety is enforced twice, independently. The **engine** opens DuckDB with
`read_only=True` and `enable_external_access=false` — the second matters,
because on a plain read-only connection `COPY ... TO` writes a real file and
`read_csv('/etc/passwd')` reads one. The **validator** then requires exactly
one statement that parses as `SELECT`, references only the nine ingested
tables, and calls no table functions. It is an allowlist over DuckDB's own
parse tree, not a keyword blocklist, because `SELECT * FROM 'secrets.csv'`
contains no suspicious keyword at all.

## Model settings

`claude-sonnet-5-5`, effort `medium` for SQL and `low` for prose, adaptive
thinking, structured output for the SQL. About **$0.01 per question**.

This model rejects `temperature`, forced `tool_choice` and assistant prefill
with a 400 — all three being the usual ways to pin down text-to-SQL output —
so the output schema does that work instead.
