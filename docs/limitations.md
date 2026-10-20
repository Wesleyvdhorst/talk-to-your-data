# Limitations

Known and measured, not hypothetical. Every claim below points at a recorded
run: the iteration 3 run in `eval_results_iteration3.md` (21/21), iteration 2
in `eval_results_iteration2.md`, iteration 1 in `eval_results_iteration1.md`,
and whatever ran last in `eval_results.md`.

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
