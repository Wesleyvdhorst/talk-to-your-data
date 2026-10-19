
# Talk with Your Data — Implementation Plan

## Purpose and how to use this plan

This document tracks **what to build, in what order, and why**. Stable engineering principles and collaboration rules live in the repository-root `CLAUDE.md`.

The assignment at `docs/requirements.txt` is authoritative. Work on **one milestone at a time**. Each milestone must result in a verifiable deliverable before moving on. Product Iterations 2 and 3 are proposed enhancements subject to evidence from the previous version.

## Current progress

- **Active product iteration:** 1 — Core Product
- **Active implementation milestone:** 2 — Database Intelligence & SQL Safety
- **Status:** Milestone 1 complete. `uv` project, ingestion of all nine Olist
  tables into DuckDB, a read-only query layer, 30 passing tests, and a measured
  data model in `docs/data_model.md`.
- **Next gate:** A schema/semantic context builder testable without an LLM, and
  a SQL validator that rejects everything but a single read-only query.

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

# Product Iteration 2 — Robustness

**Goal:** Address observed baseline problems without changing the core purpose.

Possible scope, subject to the Milestone 5 results:

- Classify questions as `ANSWERABLE`, `AMBIGUOUS`, or `UNSUPPORTED`.
- Request clarification for genuinely ambiguous questions rather than guessing.
- Introduce **at most one** SQL repair attempt when useful and safe.
- Capture inspectable pipeline artifacts (e.g., selected schema, generated SQL, validation outcome, execution error, result, final answer).
- Strengthen the fixed evaluation suite, including classification and failure handling.

**Outcome:** Better reliability, clearer uncertainty handling, and a more inspectable pipeline. Measure improvements against the initial baseline.

# Product Iteration 3 — Richer Data Understanding

**Goal:** Support questions requiring review text and, only if justified, more flexible orchestration.

Possible scope:

- Route questions among `SQL_ONLY`, `REVIEW_TEXT`, and `HYBRID`.
- Add semantic search over the Portuguese `review_comment_message` field.
- Combine structured SQL metrics with relevant review excerpts where needed.
- Consider a bounded agent loop of **3–4 tool steps maximum**, but only if fixed routing/pipelines demonstrably fail to cover important use cases.
- Evaluate grounding, retrieval relevance, and integration between structured and unstructured evidence.

**Outcome:** More expressive data interaction without unjustified agent complexity.

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
