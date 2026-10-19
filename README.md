# Talk with your data — Olist

Ask a question in plain language. The app writes DuckDB SQL, validates it,
runs it read-only, and explains the result from the rows it got back.

```bash
uv sync
# the nine Olist CSVs go in data/ (not committed, ~120 MB)
uv run python -c "from pathlib import Path; from twyd.ingest import ingest; print(ingest(Path('data'), Path('db/olist.duckdb')))"
printf 'ANTHROPIC_API_KEY=...\nTYPESAFE_API_KEY=...\n' > .env
uv run --env-file .env streamlit run src/twyd/app.py
```

## How it works

`answer_question(question, conn)` is the whole pipeline. One pass, no loops:

```
question
  -> column overview     one line per table: its columns, ~266 tokens
  -> classify            Jev, three labels, reasoning over those columns
  -> clarify             if AMBIGUOUS: LLM call asking which reading, turn ends
                         if UNSUPPORTED: fixed scope message, turn ends
  -> schema context      built from the live database + verified semantic notes
  -> generate_sql        LLM call #1, structured output, one {sql} field
  -> validate            parse tree: one SELECT, nine known tables, no table functions
  -> run_query           read-only DuckDB, 1,000-row cap, 30s timeout
  -> generate_answer     LLM call #2, grounded in the returned rows and their shape
  -> Answer(classification, sql, result, answer, message, error, stage)
```

Three outcomes, kept apart: an **answer** (grounded in a result), a
**message** (the pipeline declined or asked back - unsupported or
underspecified, and no SQL was generated), or an **error** (something broke).
A decline is an outcome, not a failure.

`Answer.trace` lists the stages the question actually passed through and is
shown in the UI. It is derived from the fields above rather than recorded
alongside them, so it cannot claim a stage that did not run.

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

Known and measured, not hypothetical. The current eval run is in
`docs/eval_results.md` (17/18), the iteration 2 baseline in
`docs/eval_results_iteration2.md` and iteration 1 in
`docs/eval_results_iteration1.md`.

- **Semantic ambiguity is not caught, only lexical ambiguity.** This is the
  headline limitation. The classifier reliably spots an undefined *word* —
  "best", "perform best" — but not a question that reads precisely and still
  depends on a modelling choice. "What is the average number of items per
  order?" is labelled ANSWERABLE at 0.98 and still returns 1.1328 on one run
  (all 99,441 orders, zero-item orders counted as zero) and 1.1417 on another
  (only the 98,666 orders that have items). Both are defensible readings of
  the same sentence. Asking about every denominator choice would make the app
  useless, so this is recorded rather than patched.
- **Vagueness and missing data overlap, and the boundary is prompt-written.**
  "How are we doing recently?" was UNSUPPORTED at 0.67 in iteration 2 and is
  AMBIGUOUS at 0.95 now, after the criteria were told that vagueness is about
  a choice the data *could* make while unsupported is about what the data
  lacks. Widening it that far first pulled "how will we be doing next
  quarter?" across with it, so the criteria carry an explicit carve-out for
  forecasts. The boundary holds because two integration tests pin both sides
  of it, not because the wording is self-evident.
- **The classifier's confidence is recorded, never acted on.** Thresholding it
  is tempting and would be wrong: "how many orders were placed in 2030?" — the
  question whose correct answer is zero — sits at 0.40–0.55, below several
  questions that are genuinely harder.
- **A clarification ends the turn.** The user must re-ask with the measure
  they meant; the pipeline keeps no history, so "and for late deliveries?"
  cannot work. Follow-up questions are iteration 3.
- **No review-text understanding.** `review_comment_message` is Portuguese and
  present on ~41% of reviews; nothing reads it yet. Iteration 3.
- **No SQL repair.** Deliberately absent: across 18 questions this run
  produced zero validation and zero execution failures, so there is nothing
  for a repair loop to fix. It will be built when a run shows it is needed.
- **Answer prose is graded by reading.** The row count behind an answer is
  checked deterministically (question 17), but whether a caveat is well judged
  is not something a test here asserts.
- **Single user, localhost.** The app binds to `127.0.0.1` and has no
  authentication or rate limiting. It holds two API keys, so do not serve it.
- **Result size.** Queries are capped at 1,000 rows and 30 seconds; a
  truncated result is flagged, not paginated.

### Fixed in iteration 2

The baseline's four recorded defects, each with the evidence:

| Defect in iteration 1 | Now |
|---|---|
| A 20-row result described as 19 rows; `customer_unique_id` called "distinct customer IDs" | Row count and column names are stated as facts; the answer call gets the schema. Question 17 grades the count |
| `asdfgh` answered via `SELECT 'looks like random keystrokes' AS message` | Declined before any SQL exists |
| "What will sales be next month?" answered with a trailing average | Declined; no SQL generated |
| "What are the best products?" silently picked revenue | Asks which reading: revenue, units sold, or review score |

## Tests

```bash
uv run pytest                                   # 167 offline, no key needed
uv run --env-file .env pytest -m integration    # 32 real-model tests, ~$0.25
```

Offline tests mock the model and run on tiny fixture CSVs. Integration tests
make real calls and assert analytical meaning rather than SQL strings, since
two correct queries for one question rarely look alike.

Working notes, including what broke and why, are in `docs/dev_notes.md`. The
measured grain, keys and traps of the dataset are in `docs/data_model.md`.
