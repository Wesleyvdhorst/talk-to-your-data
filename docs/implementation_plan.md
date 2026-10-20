
# Talk with Your Data — Implementation Plan

## Purpose and how to use this plan

This document tracks **what to build, in what order, and why**. Stable engineering principles and collaboration rules live in the repository-root `CLAUDE.md`.

The assignment at `docs/requirements.txt` is authoritative. Work on **one milestone at a time**. Each milestone must result in a verifiable deliverable before moving on. Product Iterations 2 and 3 are proposed enhancements subject to evidence from the previous version.

## Current progress

- **Active product iteration:** 3 — the final touches, complete
- **Implementation milestones 1–12 done.** Iteration 1 shipped the linear
  pipeline and a recorded baseline; iteration 2 added grounded claims about
  results (6), classification before SQL (7), clarification and an
  inspectable trace (8), and a graded eval covering all of it (9). Iteration 3
  closed with the borderline classification (10), the modelling assumption
  behind a number (11) and follow-up questions (12).
- **Latest run: 21/21** (archived in `docs/eval_results_iteration3.md`; the
  live file is `docs/eval_results.md`). Classification 21/21,
  values 11/11 — but question 8 passed this run and failed the previous one
  with nothing changed between them, which is the limitation itself rather
  than a fix. Read it before quoting the score. The iteration 2 baseline is archived in
  `docs/eval_results_iteration2.md` and iteration 1 in
  `docs/eval_results_iteration1.md`.
- **The bounded SQL repair attempt was not built**, on
  purpose: the run produced zero validation and zero execution failures, so
  there is no observed limitation for it to fix. The condition for building it
  is a recorded run that fails at `validate` or `execute`.
- **Extras, after iteration 3:** milestone 13 (cost and cache meter) and
  milestone 14 (auto chart) done. See the section at the end of the
  iterations.

Update this section upon completion of a milestone. Record implementation decisions in `docs/dev_notes.md`.

---

# Product Iteration 1 — Core Product

**Goal:** Deliver the assignment's complete baseline: question → schema/semantic context → LLM-generated SQL → validation → DuckDB execution → result-grounded natural-language answer.

These five **implementation milestones** are development stages within the *same* first product iteration, not five separate product versions.

## Milestone 1 — Data Foundation & Database

**Corresponds to former steps 1A–1D.**

### Work

- Create/verify a clean Git repository, Python environment, `uv` project configuration, pytest setup, `.gitignore`, and `.env.example`.
- Put the assignment and project documentation under `docs/`.
- Assume the Olist CSVs are already present in `data/`. Acquiring them is a manual step outside the application; ingestion reads them from that directory and never writes to it.
- Explore at least `orders`, `order_reviews`, and `customers` (`olist_customers_dataset.csv`). Add other tables only when justified by useful questions.
- Document table grain, key identifiers, join relationships/cardinality, missing values, timestamps, and categorical fields.
- Pay special attention to `customer_id` versus `customer_unique_id`, multiple reviews per order, and row multiplication from items/payments.
- Create a deterministic CSV-to-DuckDB ingestion process that preserves raw inputs.
- Build an independent connection/query layer returning structured results and useful errors.

### Verification

- Dependencies install and imports/tests work.
- Secrets/raw data/derived DB files are appropriately excluded from Git.
- Re-running ingestion yields consistent tables and row counts.
- Verify table structure and simple SQL queries, including counts, groupings, joins, and invalid SQL.
- Confirm a valid empty query result is distinguishable from failure.

### Deliverable / done when

A reproducible DuckDB database, a documented core data model, and a tested Python query interface. **No LLM integration yet.**

## Milestone 2 — Database Intelligence & SQL Safety

**Corresponds to former steps 1E–1F.**

### Work

- Expose physical schema context (tables, columns, types).
- Add concise, verified semantics: table grain, identifiers, relationships, and join hazards. Keep business choices out of generic schema metadata.
- Create a SQL validator allowing only the intended single analytical read-only query form.
- Use a read-only DuckDB connection for LLM-generated SQL. Check query constructs that can access files, extensions, or side-effecting functions; apply restrictions proportionate to the case.
- Reject **multi-statement** SQL. The engine cannot: `SELECT 1; SELECT 2`
  executes and returns only the last result. Pinned by
  `test_multiple_statements_are_NOT_blocked_here`.
- Bound query cost. `SELECT count(*) FROM geolocation a, geolocation b` counts
  1.0 trillion rows with no timeout or row cap. Decide between a statement
  timeout, a row limit, or both.
- Preserve actionable validation and execution errors.

### Verification

- Test generated context without an LLM.
- Test permitted SELECTs, appropriate WITH queries, joins, aggregation, empty results, malformed SQL, multiple statements, writes/DDL, and potentially unsafe read constructs.
- Demonstrate defense in depth rather than keyword-only checks.

### Deliverable / done when

An independently testable schema/context builder and restricted read-only SQL query path suitable for connecting to an LLM.

## Milestone 3 — LLM Integration & End-to-End Pipeline

**Corresponds to former steps 1G–1I.**

### Work

- Choose and configure an LLM API and model, keeping provider-specific code isolated where practical.
- Implement `generate_sql(question, schema_context)` with a narrow prompt/output contract.
- Validate generated SQL through Milestone 2's safety layer, execute it through Milestone 1's database layer, and return structured results.
- Implement `generate_answer(question, sql, result)` with explicit grounding requirements.
- Create a simple `answer_question(question)` function returning answer, SQL, data, and interpretable failure information.
- Keep the pipeline deterministic and linear. Advanced classification, repair loops, and autonomous routing belong to later product iterations.

### Verification

- Mock model responses in offline unit tests.
- Deliberately run a small number of real LLM integration tests.
- Check counts, grouping, dates, joins, and semantic traps (especially customer identity and row multiplication).
- Inspect generated SQL and final claims; correct analytical meaning matters more than SQL-string equality.

### Deliverable / done when

A runnable Python natural-language → SQL → DuckDB → explanation pipeline, separate from any UI.

## Milestone 4 — Minimal Application Interface

**Corresponds to former step 1J.**

### Work

- Create a small Streamlit application that calls the existing pipeline.
- Accept a natural-language question and show an answer.
- Make generated SQL and tabular results inspectable.
- Clearly surface failures while protecting secrets and unnecessary internals.

### Verification

- Run locally and test several representative questions.
- Confirm business logic stays outside Streamlit.
- Verify empty results and errors are distinguishable in the UI.

### Deliverable / done when

A locally runnable, demonstrable UI. No elaborate dashboard, authentication, or deployment work unless explicitly required.

## Milestone 5 — Evaluation & Baseline Stabilization

**Corresponds to former step 1K plus documentation.**

### Work

- Define approximately **8–12** representative evaluation questions *before* tuning against them.
- Cover counts, aggregation, joins, time filters, ambiguous wording, unsupported questions, and malformed inputs.
- Establish reference SQL/answers independently where possible.
- Record question, generated SQL, execution success, result correctness, answer correctness, failure category, and notes.
- Diagnose recurring errors; fix baseline defects without preemptively implementing later iterations.
- Update README, development notes, limitations, and demonstration examples.

### Verification

- Rerun the fixed set and record actual behavior.
- Demonstrate at least the assignment's baseline questions successfully.
- Clearly describe remaining unsupported behaviors and analytical risks.

### Deliverable / done when

A tested and explainable baseline product with documented limitations and evidence informing Product Iteration 2.

---

# Product Iteration 2 — Robustness (complete)

**Goal:** address the four defects the milestone 5 baseline recorded, without
changing the core purpose.

**What was built,** one milestone and one PR each:

- **Milestone 6 — grounded claims about the result.** The row count, column
  names and truncation flag are stated as facts in the answer prompt
  (`len(rows)` is Python's job), and the answer call now receives the same
  schema context as the SQL call so a column can be explained from measured
  semantics rather than its name.
- **Milestone 7 — classification before SQL generation.** `ANSWERABLE` /
  `AMBIGUOUS` / `UNSUPPORTED` from TypeSafe's Jev, reasoning over a
  column-level schema overview (`schema.column_overview`). `UNSUPPORTED` ends
  the turn with a fixed scope message; no SQL is generated. `Answer` gained a
  third outcome: a decline is neither an answer nor an error.
- **Milestone 8 — clarification and the trace.** `AMBIGUOUS` asks which
  reading was meant, naming only readings the schema supports. `Answer.trace`
  is derived from the fields the pipeline already sets, so it cannot claim a
  stage that did not run.
- **Milestone 9 — the graded eval.** Eighteen questions, every one graded on
  its label, ten also on a value, one on its row count. The four that were
  record-only in iteration 1 are now gradeable, which is most of what this
  iteration adds to the set.

**Deliberately not built:**

- **One bounded SQL repair attempt.** Conditional on evidence, and the
  evidence did not appear: zero validation and zero execution failures across
  18 questions. Build it when a recorded run fails at `validate` or `execute`.
- **Follow-up questions.** Moved to iteration 3 (below). A clarification ends
  the turn and the user re-asks.
- **A confidence threshold on the classifier.** Measured and rejected: the
  question whose correct answer is zero sits at 0.40–0.55, below questions
  that are genuinely harder, so any threshold would misroute it.
- **Thinking off on the answer call.** `thinking={"type": "between_tools"}` is
  used for the clarification, where a varying question is itself a kind of
  ambiguity. It is *not* used for the answer call: judging which caveats are
  real is the one place in the pipeline where reasoning earns its cost, and
  nothing in the recorded run suggests those answers need to be cheaper.

**Outcome:** 16/18, against a baseline of 8/8 on a set that could not grade
the four questions this iteration is about. All four recorded defects are
fixed, with the evidence in `README.md`. The two remaining failures are the
input to iteration 3.

# Product Iteration 3 — the final touches

**Goal:** the two failures the iteration 2 run recorded, and the one
structural gap it left: a clarification that ends the turn.

**Scope decided before starting.** Items 1-3 below are in; review text (4) and
the agent loop (5) are not. Review text was dropped on its cost rather than
its value, and the technical half of that is worth keeping: **DuckDB's `fts`
and `vss` extensions cannot be used in this app at all.** `LOAD fts` on a
connection opened with `enable_external_access=false` returns `Permission
Error: Loading external extensions is disabled through configuration`, and
neither extension is installed (duckdb 1.5.6, verified directly). Full-text or
vector search would have meant either embeddings precomputed into a plain
column and ranked with core `list_cosine_similarity` — no extension, no vector
database — or relaxing the engine configuration that is half of the app's
safety story. The first is real work; the second is not on the table.

**Driven by evidence, in this order:**

1. **Semantic ambiguity — addressed by surfacing, milestone 11.** Detection
   was considered and not built. Generating the query two ways and comparing
   is evidence rather than a prompt rule, but it doubles the cost of every
   answerable question and turns question 8 into a clarification, which is a
   change to the eval's expectations rather than to the app's correctness.
   What was built instead states the choice: the SQL call returns an
   `assumption` beside the query, `Answer` carries it, the trace names it on
   the `generate_sql` step and the answer call is given it as a fact. Question
   8 still fails against its reference on purpose — the failure is the
   evidence — but it now records *why*: "including the 775 orders with no
   items counted as zero items". Open: the field fires on nearly every
   question, so a measure of *consequence* is still missing.
2. **Overlapping criteria at the margin — done, milestone 10.** "How are we
   doing recently?" was `UNSUPPORTED` at 0.67; it is `AMBIGUOUS` at 0.95 now.
   The criteria learned one distinction: ambiguity is a choice the *data*
   could make, unsupported is what the data *lacks*. Widening them that far
   also moved "how will we be doing next quarter?" to `AMBIGUOUS`, caught by
   an integration test written as a control, so the criteria now carve out
   forecasts explicitly. Recorded as its own before/after run: 16/18 to 17/18,
   classification 17/18 to 18/18, nothing else moved.
3. **Follow-up questions — done, milestone 12.** Built as specified below,
   and the clarification dead end is closed: "What are the best products?"
   asks which reading, "by revenue" answers it, and the second turn produces
   its own validated SQL. Three new eval questions cover it (19-21), all
   passing. What was built:
   - A **hard cap** on history length: 3 turns **and** 2,000 characters, both
     enforced in `pipeline.history_block()`. Old turns are dropped, oldest
     first, never summarised. Turns alone would not bound cost - three turns
     of 2,000-character SQL is a different prompt from three of 300.
   - Each turn produces its own validated SQL and its own grounded answer.
     History informs generation; it never licenses reusing an earlier result
     as this question's evidence.
   - An unresolvable follow-up is `AMBIGUOUS` and asks.
   - The clarification flow and the follow-up flow are the same mechanism: an
     answered clarification *is* a follow-up turn.
   - One decision the plan did not anticipate: history goes in the **user**
     message, never the system prompt. The system prompt is the cacheable
     prefix, and earlier turns are the user's own text - in the system prompt
     they would carry operator authority, which is how a question talks the
     rules out of a model.
4. **Review text.** Route among `SQL_ONLY`, `REVIEW_TEXT` and `HYBRID`, and
   add semantic search over the Portuguese `review_comment_message` (~41% of
   reviews have one). Routing is decided per turn, never inherited. A
   follow-up may narrow to the previous turn's rows, so history carries the
   prior SQL rather than its numbers — scope is re-derived by query, never by
   pasting a result into a prompt. Retrieval is bounded by excerpt count.
5. **A bounded agent loop of 3–4 tool steps**, only if fixed routing
   demonstrably fails. Still unjustified.

**Outcome:** 21/21 on the 21-question set, from 16/18 at the end of iteration
2. Every addition traceable to a recorded failure: the borderline label (10)
to question 16, the assumption (11) to question 8, follow-ups (12) to the
clarification dead end iteration 2 shipped with. Items 4 and 5 were not built,
and the reasons are above rather than implied.

---

# Extras — bonus directions from the assignment

Two picked, one milestone each: a cost and cache meter (13), then a chart
chosen from the shape of the result (14). Semantic search over reviews stays
out for the reasons in iteration 3.

## Milestone 13 — cost and cache meter (done)

**Prompted by a finding, not a feature list.** The design talked about prompt
caching - history goes in the user message so it cannot invalidate the cached
schema - but no request ever set `cache_control`, and the API caches nothing
unasked. Measure first, then fix, then measure again:

1. **Meter.** `pipeline._Metered` wraps the Claude client for one question and
   records `response.usage` from every call through it, so `llm.py` did not
   change shape. `Answer.usage` holds one `Usage` per call, `Answer.cost_usd`
   prices it at `PRICE_PER_MTOK`, the trace gets a `cost` line and the eval a
   cost column plus a cache-read total. Jev is not counted ($0.042/MTok).
2. **Before:** 21/21, $0.1810, **0** tokens read from cache.
3. **Fix:** one cache breakpoint closing the SQL and clarify system prompts
   (rules plus schema, the same bytes every question), 5-minute TTL.
4. **After:** 21/21, $0.1154 (-36%), 38,073 tokens read from cache. Only the
   first question writes the cache (2,619 tokens).

**Open, and the next lever:** the answer call is still uncached, ~2,450
tokens per answered question, because its schema comes *after* the result
rows in the user message. Moving it into a cached system block is a prompt
change that could move answers, so it waits for a reason beyond cost.

## Milestone 14 — a chart from the shape of the result (done)

`chart.py`, pure and Streamlit-free: `chart_for(columns, rows)` returns a
`Chart(kind, x, ys)` or None, decided by types alone. The first column is x;
every all-numeric column after it is a candidate y. Time on x (a date, or a
`YYYY-MM` / `YYYY-MM-DD` string) is a line; up to 50 categories is a bar;
anything else, and any single-row result, gets no chart. `app.py` draws one y
at a time with a selector, defaulting to the last numeric column.

**Two things the real run corrected, not the tests:**
- The default column. "First numeric" drew `review_count` - the column that
  qualifies a category - instead of `avg_review_score`, the one asked about.
  Both multi-measure examples put the qualifier first, so the default is now
  the last. A habit of the generated SQL, recorded as such in `Chart`.
- The axis. Month strings sat on a category axis, where a month with no
  orders (2016-11) disappears and the line looks unbroken. `series()` turns
  them into dates, so the axis is temporal and the gap shows.

`st.bar_chart(sort=False)` keeps the query's ORDER BY - verified on
Streamlit 1.66, so a "worst first" result stays worst first.

---

# Milestone working agreement

For each milestone, Claude should:

1. Read the corresponding section above and the assignment.
2. Propose the smallest workable implementation and test strategy **before changing code**.
3. Highlight any choices requiring my approval.
4. Implement only the approved scope.
5. Run tests and manually inspect relevant behavior.
6. Propose a short entry for `docs/dev_notes.md`.
7. Stop at the milestone boundary and summarize its acceptance criteria.

# Commands and operational notes

Environment: Python 3.14 managed with `uv`; verify dependency support. Update commands below as they are *actually implemented and tested*.

Once added, document verified install, ingestion, test, and app-run commands here or in `README.md`; do not invent commands in advance.
