# Project

"Talk with your data" app for the Olist Brazilian e-commerce dataset. A user asks a question in natural language. The app generates SQL, runs it read-only against DuckDB, and explains the result.

The point of this project is **process, learning and conceptual ownership** as much as the final product. The code must stay small enough that I can explain every component, the control flow and each design choice from memory to a colleague.

- Requirements: `docs/requirements.txt`
- Full implementation plan: `docs/implementation_plan.md`. Read the relevant section before starting any substep.
- Development notes: `docs/dev_notes.md` (see the last section of this file)

# Current status

**Iteration 1, step 1A: repository and environment setup.** Update this line whenever a substep is finished.

Do not implement anything from a later step or iteration unless I ask for it explicitly.

# Iterations

1. **Core product.** Question → schema context → SQL generation → validation → DuckDB execution → answer generation. Steps 1A–1I are in the plan.
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
- The minimum useful set is `orders`, `order_reviews` and customers (`olist_customers_dataset.csv`). Add other tables only when they enable useful questions.

Known semantic traps. Verify these during step 1B and encode them in the schema context:

- `customers.customer_id` is unique **per order**. `customer_unique_id` identifies the real person. "How many customers" almost always means `COUNT(DISTINCT customer_unique_id)`.
- Some orders have more than one review, and an order can have several items and payments. Joins can therefore duplicate rows and inflate counts and averages. Aggregate before joining, or use DISTINCT on purpose.
- Delivery timestamps are null for undelivered orders. "Late" means `order_delivered_customer_date > order_estimated_delivery_date`.
- Review text is in Portuguese.

# Rules

- **Simple and explicit.** Plain Python and small functions. No LangChain, LangGraph, agent frameworks, vector DBs or speculative abstractions unless a concrete, observed problem requires one.
- **Dependencies.** Explain why before adding one, and prefer what is already installed or the stdlib.
- **Deterministic before LLM.** Never use an LLM for a check that code can do (empty input, keyword blocklists, row counts and so on).
- **No hidden errors.** Never swallow exceptions or turn a failure into an empty result. Errors must stay visible to higher layers.
- **Read-only SQL, enforced twice.** The validator rejects anything that isn't a single SELECT/WITH, and execution also uses a read-only DuckDB connection.
- **Bounded loops.** Every retry, repair or agent loop has a hard maximum.
- **Small context.** Send prompts concise, relevant schema context, not the whole database description.
- **Secrets.** API keys come from environment variables and are never committed or printed.
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

Python 3.14 is managed with `uv`. Project dependencies and the test runner have not been set up yet (that is step 1A). Add the real commands here once they exist.

# Development notes

`docs/dev_notes.md` collects material for explaining the project to others. After each substantial task, propose a short entry (a few bullets) covering:

- what was attempted
- where AI helped
- decisions I made, and Claude's suggestions I accepted or rejected
- what failed, why, and what changed as a result
- new tools or techniques learned

Keep the entries short. They are working notes, not formal documentation.
