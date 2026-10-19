# Talk with your data — Olist

Ask a question in plain language. The app writes DuckDB SQL, validates it,
runs it read-only, and explains the result from the rows it got back.

```bash
uv sync
# the nine Olist CSVs go in data/ (not committed, ~120 MB)
uv run python -c "from pathlib import Path; from twyd.ingest import ingest; print(ingest(Path('data'), Path('db/olist.duckdb')))"
echo 'ANTHROPIC_API_KEY=...' > .env
uv run --env-file .env streamlit run src/twyd/app.py
```

## How it works

`answer_question(question, conn)` is the whole pipeline. One pass, no loops:

```
question
  -> schema context      built from the live database + verified semantic notes
  -> generate_sql        LLM call #1, structured output, one {sql} field
  -> validate            parse tree: one SELECT, nine known tables, no table functions
  -> run_query           read-only DuckDB, 1,000-row cap, 30s timeout
  -> generate_answer     LLM call #2, grounded in the returned rows only
  -> Answer(sql, result, answer, error, stage)
```

| Module | Responsibility |
|---|---|
| `ingest.py` | CSVs → DuckDB, deterministic, no LLM |
| `db.py` | read-only connections, execution, row cap, timeout |
| `schema.py` | the prompt's schema context: columns introspected, semantics hand-written |
| `sql_guard.py` | treats generated SQL as untrusted; allowlist over the parse tree |
| `llm.py` | both model calls; the only file that knows the provider |
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

## Evaluation

Twelve questions, written before the first run: `docs/eval_questions.md`.
Latest results, including every generated query: `docs/eval_results.md`.

```bash
uv run --env-file .env python -m twyd.eval     # ~$0.12
```

**8/8 graded questions correct** on the recorded run, including the traps that
make this dataset interesting: customers counted as people rather than order
rows (96,096, not 99,441), reviews aggregated per order before averaging,
orders with no reviews found by anti-join (768), and lateness defined by
timestamp rather than status.

## Limitations

Known and measured, not hypothetical:

- **Answer prose can misdescribe a correct result.** Twice observed: the
  customer count described as "distinct customer IDs" when the SQL correctly
  used `customer_unique_id`, and a 20-row result described as 19 rows with a
  caveat built on the wrong count. The values are grounded; statements *about*
  the result are not reliably so.
- **Ambiguous questions get answered, not questioned.** "What are the best
  products?" silently picks revenue. The model discloses the choice, but it
  does not ask. Classification and clarification are iteration 2.
- **Unsupported questions get a number.** "What will sales be next month?"
  produced a trailing three-month average presented as an estimate. Honest
  about its method, still an answer to a question this data cannot answer.
- **Nonsense input can route a message through SQL.** For `asdfgh` the model
  emitted `SELECT 'Your question looks like random keystrokes...' AS message`
  — a valid, safe query carrying prose. The pipeline has no channel for "I
  can't use this", so the SQL channel gets used as one.
- **Run-to-run variance on ambiguous wording.** "Average number of items per
  order" returned 1.1328 on one run (all orders, zero-item orders counted as
  zero) and 1.1417 on the next (only orders that have items). Both defensible;
  the question is underspecified. `temperature` is unavailable on this model,
  so this cannot be pinned down by sampling settings.
- **No conversation.** Every question is independent; follow-ups like "and for
  late ones?" are iteration 2.
- **No review-text understanding.** `review_comment_message` is Portuguese and
  present on ~41% of reviews; nothing reads it yet. Iteration 3.
- **Single user, localhost.** The app binds to `127.0.0.1` and has no
  authentication or rate limiting. It holds an API key, so do not serve it.
- **Result size.** Queries are capped at 1,000 rows and 30 seconds; a
  truncated result is flagged, not paginated.

## Tests

```bash
uv run pytest                                   # 123 offline, no key needed
uv run --env-file .env pytest -m integration    # 16 real-model tests, ~$0.20
```

Offline tests mock the model and run on tiny fixture CSVs. Integration tests
make real calls and assert analytical meaning rather than SQL strings, since
two correct queries for one question rarely look alike.

Working notes, including what broke and why, are in `docs/dev_notes.md`. The
measured grain, keys and traps of the dataset are in `docs/data_model.md`.
