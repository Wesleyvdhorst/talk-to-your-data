# Project

"Talk with your data" app for the Olist Brazilian e-commerce dataset. A user asks a question in natural language. The app generates SQL, runs it read-only against DuckDB, and explains the result.

The point of this project is **process, learning and conceptual ownership** as much as the final product. The code must stay small enough that I can explain every component, the control flow and each design choice from memory to a colleague.

- Requirements: `docs/requirements.txt`
- Full implementation plan: `docs/implementation_plan.md`. Read the relevant section before starting any substep.
- Development notes: `docs/dev_notes.md` (see the last section of this file)

# Current status

**Iteration 1, milestone 2 (Database Intelligence & SQL Safety): complete.** Next is milestone 3 (LLM Integration & End-to-End Pipeline). Update this line whenever a milestone is finished.

Do not implement anything from a later step or iteration unless I ask for it explicitly.

# Iterations

1. **Core product.** Question → schema context → SQL generation → validation → DuckDB execution → answer generation. Split into five implementation milestones in the plan.
2. **Robustness.** Classify questions as ANSWERABLE, AMBIGUOUS or UNSUPPORTED. Ask for clarification when needed. Allow at most one SQL repair attempt. Capture inspectable pipeline artifacts. Build a fixed eval set.
3. **Richer data understanding.** Route questions to SQL_ONLY, REVIEW_TEXT or HYBRID. Add semantic search over `review_comment_message`. Add a bounded agent loop (3–4 tool steps at most) only if a fixed pipeline proves insufficient.

Each iteration adds complexity only to fix a limitation observed in the previous one.

# Architecture

Each component is separate, has an explicit input/output contract and can be tested without the rest of the app:

| Component | Responsibility | Must not |
|---|---|---|
| Ingestion | Load the Olist CSVs into DuckDB deterministically | Use an LLM, or modify files in `data/` |
| Database layer | Open read-only connections, execute SQL, return structured results and metadata | Know anything about the LLM |
| Schema/context | Build a concise physical and semantic schema description for prompts | Call an LLM (it must be testable offline) |
| SQL generation | Turn a question plus context into SQL (LLM call #1) | Execute SQL or write the answer |
| SQL validation | Treat generated SQL as untrusted. Allow only a single read-only SELECT/WITH | Fail silently |
| Answer generation | Turn question + SQL + result into a grounded explanation (LLM call #2) | Claim anything the result doesn't support |
| Pipeline | `answer_question(question)` connects the steps above and returns answer, data and SQL | Contain UI code |
| Frontend (later) | A thin Streamlit (or similar) layer on top of `answer_question` | Contain core logic |

# Data

- The raw CSVs are assumed to be already present in `data/`. They are **read-only**, are never committed (~120 MB) and obtaining them is a manual step outside the application.
- All nine tables are ingested. `docs/data_model.md` records the measured grain, keys, null rates and cardinalities for each.

Known semantic traps, every one verified against the real data — the numbers and the queries behind them are in `docs/data_model.md` and asserted in `tests/test_real_data.py`. Encode them in the schema context (milestone 2):

- `customers.customer_id` is unique **per order**. `customer_unique_id` identifies the real person (99,441 vs 96,096). "How many customers" almost always means `COUNT(DISTINCT customer_unique_id)`.
- `order_reviews.review_id` is **not unique** and is not a primary key: 789 values recur against different orders, and 547 orders carry more than one review.
- An order can have up to 21 items and 29 payment lines. Joins duplicate rows and skew averages — measurably, not theoretically. Aggregate before joining, or use DISTINCT on purpose.
- 768 orders have no review, 775 no items, 1 no payment. An inner join silently drops them.
- Delivery timestamps are null for undelivered orders (2,965). "Late" means `order_delivered_customer_date > order_estimated_delivery_date` (7,827 orders). 8 orders are marked `delivered` with no delivery date — trust the timestamp, not the status.
- Review text is in Portuguese and only ~41% of reviews have a message at all.
- `geolocation` has 1,000,163 rows for 19,015 zip prefixes. Joining it on zip multiplies rows ~53x; aggregate it first.

# Rules

- **Simple and explicit.** Plain Python and small functions. No LangChain, LangGraph, agent frameworks, vector DBs or speculative abstractions unless a concrete, observed problem requires one.
- **Dependencies.** Explain why before adding one, and prefer what is already installed or the stdlib.
- **Deterministic before LLM.** Never use an LLM for a check that code can do (empty input, keyword blocklists, row counts and so on).
- **No hidden errors.** Never swallow exceptions or turn a failure into an empty result. Errors must stay visible to higher layers.
- **Read-only SQL, enforced twice.** Two independent layers:
  1. *Engine* — `db.connect()` opens DuckDB with `read_only=True` **and**
     `enable_external_access=false`. The first blocks database writes; the
     second blocks host access, which `read_only` alone does **not**: on a
     plain read-only connection `COPY ... TO` writes a real file and
     `read_csv('/etc/passwd')` reads one. Verified in `tests/test_db.py`.
  2. *Validator* (milestone 2) — rejects anything that isn't a single
     read-only SELECT/WITH. It must reject **multi-statement** SQL, which the
     engine cannot: `SELECT 1; SELECT 2` runs and returns only the last
     result. That gap is pinned by a test so it stays visible.
- **Bounded loops.** Every retry, repair or agent loop has a hard maximum.
- **Small context.** Send prompts concise, relevant schema context, not the whole database description.
- **Secrets.** API keys come from environment variables and are never committed or printed.
- **Git: fetch first.** Run `git fetch --all` before any branching, committing,
  rebasing or cherry-picking, and before reasoning about where a branch sits.
  Local refs go stale silently: a branch merged on the remote still looks
  unmerged locally, and a plan built on that is wrong. Never state what a
  branch contains without fetching first.
- **No unrelated refactoring** while implementing a feature, and no rewriting components that already work.

# Workflow (substantial tasks)

1. Read the relevant files and the matching section of `docs/implementation_plan.md`.
2. Explain the proposed change and its main tradeoffs **before writing code**. When there are real architectural alternatives, present them and let me choose.
3. Implement the simplest version that meets the current step.
4. Add or run the tests.
5. Review the result critically. State the assumptions made, the remaining failure modes and anything still uncertain.

Claude is a coding partner, not the decision maker. Challenge my choices when they look wrong. I stay the conceptual owner of the codebase.

The workflow above is the whole process. Do not layer a plugin's own planning
or TDD ceremony on top of it. Ponytail (scope and simplicity) is the only
process plugin enabled on purpose; `feature-dev` is available on request only,
and its subagents are covered by the no-Agent-tool rule.

# Testing

- Every component has direct tests that don't need the frontend.
- **Unit tests** mock LLM responses and must run offline. **Integration tests** make real model calls, are marked separately and are run on purpose.
- Test SQL execution with hand-written SQL before involving LLM-generated SQL.
- Evaluate behaviour (did it execute, is the result right, was the routing right), not exact SQL strings.
- Write eval questions before tuning against them, to avoid leakage.
- Never weaken a test just to make the implementation pass.

# Commands

Python 3.14 is managed with `uv`.

```bash
uv sync                                  # install dependencies
uv run pytest                            # unit tests: offline, fixture data, fast
uv run pytest -m integration             # integration tests: needs the real CSVs in data/
uv run python -c "from pathlib import Path; from twyd.ingest import ingest; print(ingest(Path('data'), Path('db/olist.duckdb')))"
```

The DuckDB file is built at `db/olist.duckdb` and is gitignored. `data/` is
never written to. Re-running ingestion replaces every table, so it is safe to
delete `db/` and start over.

# Development notes

`docs/dev_notes.md` collects material for explaining the project to others. After each substantial task, propose a short entry (a few bullets) covering:

- what was attempted
- where AI helped
- decisions I made, and Claude's suggestions I accepted or rejected
- what failed, why, and what changed as a result
- new tools or techniques learned

Keep the entries short. They are working notes, not formal documentation.
