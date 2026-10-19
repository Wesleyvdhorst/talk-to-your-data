# Development notes

Working notes for explaining the project. Newest milestone last.

## Milestone 1 — Data Foundation & Database

**Attempted:** `uv` project setup, ingestion of the Olist CSVs into DuckDB, a
read-only query layer, and a verified data model document. No LLM anywhere.

**Where AI helped**
- Drafted `ingest.py` / `db.py` from a contract I specified, so the shape was
  mine and the typing was Claude's.
- Wrote the exploration queries fast enough that I could check 7 separate
  hypotheses about the data instead of 2.
- Built the fixture CSVs with the semantic traps deliberately constructed
  (an order with two reviews, one with three items, two `customer_id`s sharing
  a `customer_unique_id`). Faster than hand-picking real rows that happen to
  contain them.

**Decisions I made**
- **All nine tables, not three.** Claude argued for five (the three required
  plus items and payments, since the documented join hazards involve them) and
  flagged that nine makes the milestone-2 schema prompt bigger. I took all nine
  anyway — a "talk with your data" app that can't see most of the database is
  artificially narrow, and prompt size is milestone 2's problem to solve by
  selecting relevant tables per question.
- **DuckDB file in `db/`, not `data/`.** Accepted Claude's reasoning: the rule
  says ingestion must never write to `data/`, so the derived artifact lives
  outside it. Makes "delete and re-ingest" obviously safe.
- **Rejected a committed exploration script.** Claude proposed running ad-hoc
  queries instead and I agreed — exploration is one-time, its deliverable is
  `docs/data_model.md` plus the integration tests that pin the numbers. A
  script nobody re-runs is weight I'd have to explain.
- **Rejected error wrapping.** `duckdb.Error` propagates unchanged. A custom
  exception class would have had one caller and added no information the
  DuckDB message doesn't already carry.

**What failed**
- My own arithmetic. I asserted that joining 4 fixture orders through items and
  payments yields 8 rows; it yields 7. The test caught it immediately. Mildly
  embarrassing and exactly the point of the fixture — the trap is real enough
  to catch the person who wrote the trap.
- Relative import in `test_ingest.py` (`from .conftest import ...`) failed
  because `tests/` isn't a package. Defined the path locally instead.

**What the data actually said** (several claims in my own CLAUDE.md were
incomplete)
- `review_id` is **not unique** — 99,224 rows, 98,410 distinct, and 789 values
  recur against *different* orders. I had assumed it was a primary key.
- 8 orders are `status = 'delivered'` with a null delivery date. Status and
  timestamps contradict each other.
- `geolocation` is 1M rows for 19k zip prefixes — a lat/lng sample, not a
  lookup table. Joining it naively multiplies rows ~53x.
- The join distortion is measurable, not theoretical: average review score for
  SP customers is 4.1269 through a naive item join versus 4.1741 aggregated
  per order first.
- Only ~41% of reviews have text, which halves the corpus size assumed for
  iteration 3's semantic search.

**Learned**
- `read_csv(..., sample_size = -1)` — scanning the whole file for type
  inference rather than a head sample. Timestamps came out as `TIMESTAMP`
  rather than `VARCHAR` without any manual `types=` override.
- DuckDB's `read_only=True` connection makes write rejection an engine
  guarantee, so the validator in milestone 2 is defense in depth rather than
  the only line of defense. Tested with five write statement forms.
- `count(*) FILTER (WHERE ...)` for computing several conditional counts in
  one pass instead of separate queries.
- pytest markers plus `addopts = "-m 'not integration'"` to make the offline
  suite the default and the 120 MB one opt-in.

**Open question for milestone 2:** nine tables of schema context is too much
for one prompt. Does the schema context get selected per question, or sent
whole and trimmed later? I'd rather not build the selector before I've seen it
actually fail.

### Post-review addendum: the read-only connection was not enough

Testing queries by hand after the milestone-1 commit found a real hole that
all 30 tests had missed, because every test only asked whether *database*
writes were blocked.

On a plain `duckdb.connect(..., read_only=True)`:

- `COPY orders TO '/tmp/leak.csv'` **wrote a 17 MB file to disk.**
- `SELECT * FROM read_csv('/etc/passwd')` **returned 141 rows of the host's
  password file.**
- `INSTALL httpfs` downloaded and installed an extension.

Worse, `run_query` reported the first two as `"query returned no result set"`,
which reads like a rejection and wasn't one. A misleading error is worse than
no check, because it stops you looking.

`read_only=True` protects the *database*. It says nothing about the *host*.
I had conflated the two, and the commit message I wrote ("writes fail at the
engine") was true only for the narrow meaning of "write".

**Fix:** one config flag — `enable_external_access=false` — closes file reads,
file writes, `ATTACH` and `INSTALL`, all at the engine. Six new tests pin it,
written to fail if the flag is ever removed.

**Not fixed, deliberately:** `SELECT 1; SELECT 2` still runs and returns only
the last result. No connection setting rejects it, because it's a syntactic
property rather than a permission. That is milestone 2's validator's job, and
there is now a test named `test_multiple_statements_are_NOT_blocked_here`
asserting the current (wrong-for-production) behaviour, so the gap is
documented rather than assumed closed.

**The lesson worth presenting:** my tests all asked the same question in five
different ways (can it write the database?) and none asked the adjacent one
(can it touch the machine?). Breadth of threat model beats number of
assertions. Thirty passing tests gave false confidence.

## Milestone 2 — Database Intelligence & SQL Safety

**Attempted:** the schema context that goes into the prompt, a validator for
untrusted model-generated SQL, and cost bounds on query execution.

**Where AI helped:** it found that DuckDB already ships what the validator
needs — `duckdb.extract_statements()` returns one `StatementType` per
statement, which is real parsing rather than keyword matching, and no new
dependency. It also established that DuckDB has *no* statement-timeout setting
(only `memory_limit`), which decided the timeout design for me.

**Decisions I made:**

- All nine tables go into the context every time (~4.3k chars). Per-question
  table selection is an optimisation with no observed problem, and it risks
  dropping a table the question needed.
- The cost bound lives in `db.py`, not the pipeline. Defense belongs at the
  layer that executes.
- Semantics as a Python dict in `schema.py` rather than a Markdown file read at
  runtime: one less file, no I/O, diffable next to the code that uses it. The
  tradeoff is prose living in Python.

**What failed:** the first row cap wrapped the query as
`SELECT * FROM (<sql>) LIMIT n+1`. It rewrote the model's SQL — so error
positions pointed at text the model never wrote — and it broke the two existing
tests that document engine behaviour (`SET threads = 2` and
`SELECT 1; SELECT 2` both became syntax errors inside a subquery). Replaced
with `result.fetchmany(max_rows + 1)`, which streams: the cap applies without
materialising the whole result and without touching the SQL.

**Two surprises, both now pinned by tests:**

- DuckDB parses `DESCRIBE`, `SHOW TABLES` and `PRAGMA database_list` as
  `SELECT`. They are read-only and return a result set, so allowing them is
  correct — and the side-effecting spellings (`PRAGMA threads = 4`,
  `PRAGMA enable_external_access = true`) parse as `SET` and are rejected. The
  parse type, not the keyword, is what separates them.
- `statements[0].type is duckdb.StatementType.SELECT` is *always false*. The
  pybind11 enum members are not interned singletons, so identity fails while
  `==` works. Every allowed query was being rejected with the message "this is
  a SELECT statement", which is the kind of error that reads as a logic bug in
  the test rather than in the comparison operator.

**Learned:** classifying SQL by parse type rather than by keyword is both
shorter and stronger — `/* SELECT */ CREATE TABLE evil AS SELECT * FROM orders`
is rejected as a CREATE without any string inspection. And a row cap is not a
cost bound: `SELECT count(*)` over a cross join returns one row after counting
a trillion, which is why the timeout exists as well.

**Known rough edges:** the trap numbers in `TABLE_NOTES` are hand-written
real-data facts, so against the small fixture database the notes and the
introspected row counts disagree. `max_rows=1000` and 30 seconds are guesses,
not measurements. Nothing calls `validate()` yet — wiring it in front of
execution is milestone 3.

### Post-review addendum: the function denylist was the wrong shape

An automated security review flagged `_FORBIDDEN_FUNCTIONS` in `sql_guard.py`
as an incomplete denylist. I went looking for a bypass and found one
immediately:

```sql
SELECT * FROM 'secrets.csv'
```

No function name appears anywhere in that query. DuckDB resolves a bare string
in `FROM` to a file via its replacement scans, and the parse tree calls it a
`BASE_TABLE` named `secrets.csv`. A list of function names cannot see it, and
neither could a longer list.

**Fix:** the second layer is now an allowlist built from the parse tree, not a
denylist of names. `json_serialize_sql` gives the tree (parsed, never
executed); the walk collects every `BASE_TABLE`, every `TABLE_FUNCTION` and
every CTE name, then rejects any table that is not one of the nine ingested
tables and any table function at all. The allowlist comes from
`ingest.TABLES`, so it cannot drift from what was actually loaded.

Consequences I accepted:

- `PRAGMA database_list` is now rejected. It parses as `SELECT`, but the
  serialiser rewrites it into a `pragma_database_list()` reference. No question
  about the data needs it.
- Table functions like `range()` are rejected too. Analytical queries over
  these nine tables do not need them, and "no table functions" is a rule I can
  state in one sentence.
- SQL the serialiser cannot represent is rejected rather than passed through.

**The lesson, and it is the same one as milestone 1's addendum:** my denylist
was enumerating the attacks I had thought of. The allowlist enumerates what the
feature actually needs, which is nine table names. Nine is a number I can
check; the set of ways to name a file is not.
