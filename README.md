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

## Evaluation

Twelve questions, written before the first run: `docs/eval_questions.md`.
Latest results, including every generated query: `docs/eval_results.md`.

```bash
uv run --env-file .env python -m twyd.eval     # 21 questions, ~$0.20
```

**21/21 on the recorded run** (classification 21/21, values 11/11), including
the traps that make this dataset interesting: customers counted as people
rather than order rows (96,096, not 99,441), reviews aggregated per order
before averaging, orders with no reviews found by anti-join (768), and
lateness defined by timestamp rather than status.

**Read question 8 before believing the 21.** It passed this run and failed the
last one, with nothing changed between them — see the first limitation below.
The score is a snapshot of one run, which is why every run is recorded in
full.

## Limitations

Known and measured, not hypothetical. The current eval run is in
`docs/eval_results.md` (21/21), the iteration 2 baseline in
`docs/eval_results_iteration2.md` and iteration 1 in
`docs/eval_results_iteration1.md`.

- **Semantic ambiguity is surfaced, not detected.** The classifier reliably
  spots an undefined *word* — "best", "perform best" — but not a question that
  reads precisely and still depends on a modelling choice. "What is the
  average number of items per order?" is labelled ANSWERABLE at 0.98 and
  returns 1.1328 (all 99,441 orders, zero-item orders counted as zero) where
  the reference says 1.1417 (only the 98,666 orders that have items). Both are
  defensible readings of one sentence, so the app does not ask — asking about
  every denominator would make it useless. What it does instead is **state
  the choice**: the SQL call returns an `assumption` alongside the query, and
  the answer carries it — *"that average covers all orders, including the 775
  orders with no items counted as zero items."* The number is still one of two;
  it is no longer one of two silently.

  **The run-to-run flip is the evidence, and it is still there.** Question 8
  returned 1.1328 on one recorded run and 1.1417 on the next, with no change
  to the app in between — so it failed its reference once and passed it once.
  The reference was not relaxed and the prompt was not tuned at it. What
  changed is that the record now says which reading each run took: *"averaged
  only over orders that have at least one item line (the 775 orders with no
  items are excluded)"* on the run that passed, *"including the 775 orders
  with no items counted as zero items"* on the run that failed. A single score
  cannot represent this question; the assumption line can.
- **The assumption is prose, and it fires on nearly every question.** Nothing
  branches on it, and no test asserts its wording — only that it exists, stays
  a clause, and does not restate the SQL. In the recorded run all 11 answered
  questions produced one, several of them genuinely load-bearing ("orders with
  multiple reviews count once per review"), and some closer to boilerplate
  ("every order regardless of status"). Tightening it to *consequential*
  choices only would need a measure of consequence, which is the harder
  problem it is standing in for.
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
- **History is dropped, never summarised, and the app is single-session.**
  Past the 3-turn or 2,000-character cap the oldest turn is gone, so a
  reference back to something five questions ago fails — it becomes an
  AMBIGUOUS question and the app asks. The transcript lives in Streamlit's
  session state, so a browser reload starts a new conversation. Both are
  deliberate: a summary is one more model call that can be wrong about what
  was asked, and persistence is a database this app does not need.
- **No review-text understanding, and it is not coming in this app.**
  `review_comment_message` is Portuguese and present on ~41% of reviews;
  nothing reads it. Dropped from iteration 3 on cost, and the measurement
  behind that is worth knowing: DuckDB's `fts` and `vss` extensions **cannot
  be loaded here at all** — `LOAD fts` on a connection with
  `enable_external_access=false` returns `Permission Error`, and neither is
  installed. Search over the reviews would have meant embeddings precomputed
  into a plain column and ranked with core `list_cosine_similarity` (no vector
  database), or relaxing the engine configuration that is half the safety
  story. The first is real work; the second is not on the table.
- **No SQL repair.** Deliberately absent: across 21 questions this run
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
uv run pytest                                   # 199 offline, no key needed
uv run --env-file .env pytest -m integration    # 40 real-model tests, ~$0.35
```

Offline tests mock the model and run on tiny fixture CSVs. Integration tests
make real calls and assert analytical meaning rather than SQL strings, since
two correct queries for one question rarely look alike.

Working notes, including what broke and why, are in `docs/dev_notes.md`. The
measured grain, keys and traps of the dataset are in `docs/data_model.md`.
