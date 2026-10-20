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

A second automated review then flagged the CTE handling in that same walk: CTE
names are collected into one flat set, so a CTE defined in one subquery excuses
that name anywhere in the statement. I tested the obvious exploit before
changing anything:

```sql
WITH "secrets.csv" AS (SELECT 1 AS x) SELECT * FROM 'secrets.csv'
-- passed the validator; DuckDB returned (1,) - the CTE, not the file
```

So the bypass gets past the validator but reads nothing: the CTE shadows the
replacement scan. Not a leak, but it depends on DuckDB's name-resolution order,
which is not a thing I want a safety property resting on. Two cheap rules
instead of a scope stack: a CTE name must be a plain identifier
(`^[A-Za-z_][A-Za-z0-9_]*$`), and a catalog- or schema-qualified reference is
never excused by a CTE name, since a CTE can carry neither. `main.orders` still
works.

The flat-set shortcut is still there, marked with a `ponytail:` comment naming
the ceiling: an out-of-scope name fails to bind in the engine anyway, and a
file-like name can no longer be a CTE. A scope stack goes in only if CTE names
ever stop being plain identifiers.

**Worth noting about the process:** the first review was right and I had
argued, in writing, that the denylist was "deliberately redundant". The second
review was right about the mechanism and wrong about the impact. Testing the
exploit took two minutes and was the only way to tell those two apart.

## Milestone 3 — LLM integration and the end-to-end pipeline

**Attempted:** the two model calls (`llm.py`) and the pipeline that connects
them to the milestone 1 executor and the milestone 2 validator
(`pipeline.py`).

**Where AI helped — and where it would have been wrong from memory.** I had
Claude read its own `claude-api` skill rather than recall the API, and three
things came back that contradict every text-to-SQL example I have seen:

- `temperature` is rejected with a 400 on Sonnet 5.5. The standard
  `temperature=0` for deterministic SQL does not exist on this model.
- Forced tool use (`tool_choice: {type: "any"}`) is also a 400, so the "force
  a tool call to get clean JSON" trick is gone.
- Assistant prefill is a 400 too, so the other usual trick for "emit only
  SQL" is gone as well.

All three were ways of constraining output. Structured outputs
(`output_config.format` with `additionalProperties: false`) replaces all of
them in one parameter, and a test pins that `temperature` and `thinking` never
reappear in the request.

**Decisions I made:** both calls live in one file, which is the only file that
imports `anthropic`; `generate_answer` takes columns and rows rather than a
`QueryResult`, so `llm.py` does not depend on the database layer; the caller
owns the connection, so a Streamlit layer can keep one open; the pipeline
catches only `UnsafeSQL`, `duckdb.Error` and `TimeoutError`, so a missing key
or a malformed response still raises as an app problem rather than being
reported as a bad question.

**What the first real run showed.** All 15 integration tests passed on the
first attempt (~$0.06), including the two trap assertions, and an inspection
run over four harder questions was correct on all four. The one I did not
expect:

```sql
WITH late AS (SELECT order_id FROM orders
              WHERE order_delivered_customer_date > order_estimated_delivery_date),
     r AS (SELECT order_id, AVG(review_score) AS s FROM order_reviews GROUP BY order_id)
SELECT AVG(r.s), COUNT(*) FROM late JOIN r ON r.order_id = late.order_id
```

It aggregated reviews per order *before* averaging, which is precisely what
trap #3 warns about and what a naive `orders JOIN order_reviews` would get
wrong. The schema notes are doing real work, not decorating the prompt.

**What is still wrong:** the prose for "how many customers" said "counted as
distinct customer IDs", while the SQL correctly used `customer_unique_id`.
Right number, misleading sentence - and "customer ID" is the name of the trap.
The answer prompt needs to make the model name the column it used or say
nothing about method. Noted for milestone 5 rather than tuned now, because
tuning before the eval set exists is how you leak the eval into the prompt.

**Learned:** the failure I expected (the model falling for the customer-identity
trap) did not happen, and the failure I got was in the *explanation* of a
correct query. Grounding is not only about numbers; a true number with a wrong
account of where it came from is still a wrong answer to a person who trusts it.

## Milestone 4 — the Streamlit layer

**Attempted:** a UI thin enough that nothing in it decides anything.
`app.py` collects a question, calls `answer_question`, and renders the answer,
the SQL and the result table. The failure path shows which stage broke
(`generate` / `validate` / `execute`), because that is the field worth reading
when something goes wrong.

**Decisions I made:** `@st.cache_resource` for the connection rather than a
module global of my own - it is Streamlit's own mechanism for a per-session
resource, so there is no connection pool to explain. The result table is built
as a `{column: values}` dict, which renders with real headers and keeps pandas
out of the dependency list.

**What failed, and it is the useful part of this milestone.** My first UI test
monkeypatched `twyd.app.DB_PATH` to the fixture database. It passed - and it
was lying. `AppTest` re-executes `app.py`, so the module-level constant is
rebuilt from the source and my patch never applied; the test was quietly
running against the real 53 MB database. A passing test that does not test
what it says is worse than no test, and the only reason I caught it was that
the second test (missing database) failed for the same cause and made the
mechanism visible.

Fix: `DB_PATH = Path(os.environ.get("TWYD_DB", "db/olist.duckdb"))`, which the
tests set before the app runs. The fixture-backed test now finishes in ~1s
instead of ~10s, which is itself the evidence it changed database.

**New tools learned:** `streamlit.testing.v1.AppTest` runs the app headlessly
and lets you assert on rendered elements (`app.success`, `app.code`,
`app.dataframe`, `app.exception`). Two offline tests cover rendering with no
model call, and one integration test types a real question and checks the
answer, the SQL and the table all arrive - so a broken widget call shows up in
a test run rather than during a live demo.

**Still true:** no business logic in the UI. If something in `app.py` starts
looking like a decision, it belongs in `pipeline.py`.

### Post-review addendum: two findings in the UI

**1. `st.cache_resource` is not per session.** My docstring said "one
read-only connection per session"; it is one connection shared by every
session. That matters because `run_query`'s timeout calls
`conn.interrupt()` - on a shared connection, cancelling one slow query would
cancel a concurrent one in another browser tab. Each run now takes
`_database().cursor()`, which is a separate connection to the same database.
Verified that a cursor still inherits `read_only` and
`enable_external_access` (a `CREATE TABLE` and a `read_csv('/etc/passwd')`
both still fail on it) and that interrupting one cursor leaves another
working.

**2. `st.success` renders Markdown, and the answer can quote reviews.**
`review_comment_message` is public free text. I checked before believing the
finding: 5,591 reviews contain `[`, `!` or `http`, and real ones carry raw
URLs like `http://prntscr.com/jkx7hr`. So "what do customers complain about?"
can route a stranger's text through the model into a Markdown renderer, where
`![](http://...)` becomes an auto-fetched image and `[text](http://...)` a
clickable link - from data, not from me. `_plain()` escapes `[ ] ! \``
before display. HTML was already inert, since Streamlit escapes it unless
`unsafe_allow_html` is set.

**Pattern worth noting across all three security reviews so far:** every
finding was about a boundary I had not thought of rather than a rule I had
broken. Milestone 1: read-only protects the database, not the host.
Milestone 2: a denylist of function names cannot see `FROM 'secrets.csv'`.
Milestone 4: the data itself is untrusted input to the *renderer*, not just to
SQL. Each time the fix was two lines and the blind spot was conceptual.

A second review of the same file found both of my fixes incomplete, and it was
right twice:

**The escape missed the syntax-free case.** I escaped `[`, `]`, `!` and
backtick, which kills `![](url)` and `[text](url)`. But Streamlit's Markdown
renderer has GFM autolinking - its bundle carries `autolinkProtocol` and
`autolinkEmail` - so a bare `http://evil/x`, a `www.evil.com` or an
`a@b.com` becomes a link with no Markdown syntax at all. I had closed the
vector that needs punctuation and left the one that needs none. A U+200B after
the scheme now breaks the linkifier while staying invisible.

I did not take the rest of the reviewer's suggestion, which was to escape
`(`, `)`, `:`, `*`, `_`, `<`, `>` as well. `<` and `>` are already inert
(Streamlit escapes HTML unless `unsafe_allow_html` is set), `*` and `_` only
make text italic - cosmetic, no destination - and escaping `(`, `)` and `:`
would mangle every legitimate answer this app produces: "2.57 (per order)",
"Note: averaged per order". A fix that makes correct output unreadable to
close nothing is a bad trade, and the test now pins that ordinary prose
survives.

**The app was listening on every interface.** `streamlit run` binds to `*` by
default - I confirmed it with `lsof`: `TCP *:8601 (LISTEN)`. The app holds an
API key and has no authentication, so on office or conference wifi anyone on
the network could have opened it and spent the key. `.streamlit/config.toml`
now sets `address = "127.0.0.1"`, verified by `lsof` showing
`127.0.0.1:8602` and by a request to the machine's LAN address
(`192.168.1.190`) now failing where it would have been served. No rate
limiting: one local user cannot outspend themselves, and that is marked with a
`ponytail:` comment naming when it would be needed.

**Worth presenting:** this is the first finding that was not about the data or
the SQL at all. It was about where the process listens. I had reasoned
carefully about untrusted SQL, untrusted review text and untrusted model
output, and never once asked which network interface the demo server was on.

A third review found the escaper bypassable again, and this time the right
response was to delete it.

The bypass: `\[x](http://evil)`. My escaper turned that into
`\\[x\\](http://evil)`, and CommonMark reads `\\` as a literal backslash -
after which `[x](http://evil)` is a live link. Any escaper that does not
escape the escape character first has this hole. That was the third round on
the same three lines: brackets, then GFM's syntax-free autolinks, then the
backslash.

**I had been writing a denylist of Markdown constructs** - which is precisely
the mistake milestone 2 taught me to stop making, in a different costume. So
the answer and the error detail now go through `st.text`, which renders no
Markdown at all. `_plain()` is gone, the `re` import with it, and with them
the whole class: no bracket syntax, no autolinks, no protocol-relative `//`,
no `<url>`, no backslash differential, and nothing new to patch when
Streamlit's renderer gains a feature.

What it cost: the green success box. The answer sits in a bordered container
with a caption instead. Given that the alternative was a permanent escaping
treadmill against a renderer I do not control, that is a cheap trade.

The test changed shape too. Instead of asserting what `_plain()` does to
twelve inputs, one test asserts the property: `app.py` contains no
`st.success(`, `st.markdown(` or `st.write(`, and the answer and error go to
`st.text`. A test that pins the property survives the next renderer change;
twelve tests pinning escape sequences would not have.

**The arc across four security reviews, which is the thing I would actually
present:** read-only protects the database but not the host; a function
denylist cannot see `FROM 'secrets.csv'`; untrusted data is input to the
renderer, not just to SQL; and when a sanitiser needs a third patch, the
sanitiser is the wrong design. Each fix was small. The reason each was needed
was that my threat model was one layer narrower than the system.

## Milestone 5 — evaluation and baseline stabilisation

**Attempted:** twelve questions written *before* the first run
(`docs/eval_questions.md`), a deterministic runner (`eval.py`), one recorded
run, and the documentation.

**Decisions I made:** no LLM judge. Each graded question carries reference SQL
I wrote myself, executed at eval time, and the model's value is compared to
it - deciding whether 96,096 equals 96,096 does not need a model, and a judge
would add the exact failure mode I am trying to measure. Four questions are
record-only: iteration 1 has no classification or clarification, so grading
them pass/fail would measure a feature that does not exist.

**My grading was wrong before the model was.** First run showed
`PASS … got=1.13 want=1.14` for "average number of items per order", which is
a contradiction on its face. The model had used `LEFT JOIN`, averaging over
all 99,441 orders with zero-item orders counted as zero (1.1328); my reference
averaged over the 98,666 orders that have items (1.1417). Two different
questions, 0.0089 apart - and my tolerance of 0.01, chosen for float noise,
was just wide enough to call that agreement. Tightened to 0.005: wide enough
that a model rounding 2.5665 to 2.57 still matches, narrow enough that a
different query does not.

I did **not** change the question or the reference to match what the model
produced. Rewriting a reference after seeing the output is how an eval starts
flattering the thing it measures.

**Then the same question answered differently.** The re-run returned 1.1417 -
the other interpretation - with no change to the prompt or settings. The
question is underspecified about zero-item orders, and `temperature` does not
exist on this model, so this is not something I can pin down with sampling
settings. It is the cleanest possible argument for iteration 2's `AMBIGUOUS`
class: the fix is to ask, not to tune.

**Three findings from the record-only questions:**

- `asdfgh` produced `SELECT 'Your question looks like random keystrokes...' AS
  message`. A valid, safe, table-free query used as a message channel. The
  pipeline has no way to say "I cannot use this", so the SQL channel gets used
  as one - which is an argument for classification *before* SQL generation.
- "What will sales be next month?" produced a trailing three-month average,
  labelled as an estimate rather than a prediction. Honest about method, still
  a number in answer to a question this data cannot answer.
- "What are the best products?" picked revenue, said so at the end - and then
  claimed "the list shows 19 products, not the 20 the query asked for". I ran
  its own SQL: 20 rows, 20 distinct products, and no product is missing from
  the catalog. It invented a limitation and built a caveat on top of it.

**The defect worth presenting.** Both answer-level defects now on record - the
"distinct customer IDs" wording and this fabricated row count - have the same
shape: the *values* are grounded in the result, while *statements about* the
result are not. My answer prompt says "use only numbers that appear in the
result", which the model obeyed; it says nothing about claims regarding the
shape of the result, and that is where it drifts. Grounding a value and
grounding a description of a value are different constraints, and I had only
written the first one.

**Not fixed, on purpose.** The prompt stays as it is for now. The eval set
exists to measure iteration 2 against this baseline, and tuning the prompt in
the same breath as writing the eval would leave nothing to compare to.

## Milestone 7 — classification before SQL

**Attempted:** give the pipeline a way to decline. Two baseline defects had
one cause: when the only available action is to generate SQL, every input gets
SQL - including `asdfgh`, which came back as
`SELECT 'your question looks like random keystrokes' AS message`.

**The decision I made, and why it is not the obvious one:** the classifier is
TypeSafe's Jev, not a third Claude call. Three labels, no generation and a
calibrated confidence per label is the shape of a zero-shot decision model
rather than of a chat model. $0.042/MTok on input with free output makes it
roughly a hundredth of a Sonnet call, but at one call per question the money
is not the argument - the argument is that `AMBIGUOUS` is better expressed as
low confidence in a label than as a label a chat model elects to apply to
itself. Claude flagged the honest cost (a second provider, a second key, a
second failure mode, calibration not independently verified) and I took the
tradeoff anyway, with the escape hatch that `classify()` is one module behind
one contract.

**A third outcome, which I had not planned for.** `Answer` held either an
answer or an error, and a decline is neither: the pipeline understood the
question well enough to refuse it. Adding `message`/`declined` alongside
`error`/`ok` was the part of this milestone that actually changed the design,
and the app now renders three states - a decline shown through `st.error`
would read as a bug in the app rather than as a property of the question.

**What failed, and it was my prompt again.** My first criteria labelled "how
many orders were placed in 2030?" `UNSUPPORTED` at 0.98 confidence. That is
eval question 12, the case where an empty result is the correct answer. My
`UNSUPPORTED` text said "any period outside September 2016 to October 2018",
which beat the more general "an answer of zero still belongs in ANSWERABLE" -
two clauses in conflict, and the specific one won. The model was right about
my wording. Same lesson as milestone 6: the defect was in what I wrote, and
only running the real thing showed it.

**Evidence against a knob I was tempted by.** Confidence is recorded but does
not affect routing. After the criteria fix, the out-of-window questions land
at 0.46-0.55 while the clear ones sit at 0.85-1.00 - so a sensible-looking
0.7 threshold would have routed exactly the empty-result question to
"ambiguous, please rephrase". Worth presenting: the calibration is informative
*and* acting on it would have made the system worse.

**Small thing learned about pinning.** `jev-latest` is an alias and the models
endpoint publishes no concrete version, so "pin the model id" could not be
done as planned. The version that answered (`jev-1.13.0` today) is recorded on
every classification instead, so a recorded run can still say what labelled
it.

**New technique:** the Jev stub in `conftest.py` builds the SDK's own
`SystemOneResponse`/`ChoiceAnswer` types instead of duck-typing them. If
TypeSafe changes the shape of an answer, our offline tests fail rather than
production.

## Milestone 8 — asking back, and a trace that cannot lie

**Attempted:** turn the AMBIGUOUS label into an actual question, and make the
pipeline inspectable.

**The division of labour I settled on.** Jev decides *that* a question is
underspecified; a Claude call decides *what to ask about it*. The alternative
I considered was a second fixed-taxonomy Jev question (metric / time range /
entity undefined) templated into prose, which is cheaper and much worse: "By
'best' do you mean highest revenue (sum of item price), most units sold (count
of order items), or highest average review score?" names columns that exist,
and a template cannot.

**A setting I had claimed was impossible, now used.** The clarify call runs
with `thinking={"type": "between_tools"}` - thinking off, which milestone 6
discovered was available on this model after my own code comment said it was
not. Verified on a real call before relying on it. Naming readings needs no
reasoning, and a clarification that varies between runs is itself a kind of
ambiguity.

**The trace is derived, not recorded.** `Answer.trace` reads the fields the
pipeline already set and reports the stages from them. My first instinct was
to append to a list as the pipeline went, which is a second source of truth
about the same run - and the failure mode of that design is a trace that
claims a stage ran when it did not. Deriving it means the trace cannot
disagree with the outcome.

**The mistake I made while verifying, which is the thing worth presenting.** I
saw an answer say an average was "based on 7,662 late orders" and a result
with one column and one value, and concluded I had found a fresh grounding
defect of exactly the kind milestone 6 was supposed to close. I even wrote a
prompt rule forbidding it. I had compared an answer from one run against the
result of *another* run: the generated SQL differs between runs, and on the
runs that mention 7,662 the model's own query returns it as a second column.
The claim was grounded the whole time.

Two lessons. The first is about verification: with a non-deterministic
pipeline, an answer and a result have to come from the *same* call, and
printing them separately invites exactly this error. The second is about
process: I had added a rule to the prompt before confirming the defect, and
the rule actively made good answers worse - the model started tacking "the
result also contains a column, late_orders_with_reviews, with a value of
7,662" onto otherwise clean prose to satisfy it. It is reverted. No rule
without evidence, including rules that look obviously safe.

## Milestone 9 — grading the thing iteration 2 actually added

**Attempted:** make the eval measure classification, not just values, and put
the iteration 2 numbers next to the iteration 1 baseline.

**The shape of the change.** Every question now has an `expected_label`, so
the four that were record-only in iteration 1 are gradeable — there was no
classifier then to be right or wrong about them. Six new questions, written
before the run: two more `UNSUPPORTED` (no cost data, no demographics), two
more `AMBIGUOUS`, one many-row grounding case, and one that is only answerable
if the classifier knows the columns. Three independent checks: label, value,
and (for the many-row case) row count. An `AMBIGUOUS` question also has to
*ask* something — checked by looking for a question mark, because labelling a
question ambiguous and then saying nothing useful is not a pass.

**Result: 16/18.** Classification 17/18, values 9/10. The baseline was 8/8 on
a set that could not grade the four questions this iteration is about, so the
honest comparison is not 8/8 → 16/18; it is that the four questions which had
no verdict now have one, and three of them pass.

**The interesting failure, and I am not fixing it.** Question 8, "what is the
average number of items per order?", is labelled `ANSWERABLE` at 0.98 and
still returned 1.1328 where the reference says 1.1417 — the same split as
iteration 1 (all 99,441 orders with zero-item orders counted as zero, versus
the 98,666 orders that have items). So the defect iteration 2 was supposed to
fix by asking never got asked about.

That told me something I had not understood when I wrote the plan: the
classifier catches **lexical** ambiguity — an undefined word like "best" or
"perform" — and not **semantic** ambiguity, where the sentence reads precisely
and the answer still depends on a modelling choice. Those are different
problems. I could add "questions whose denominator is unstated" to the
AMBIGUOUS criteria, and it would be wrong twice over: every average has a
denominator choice, so the app would start interrogating reasonable questions;
and I would be tuning the criteria against a question I had just measured,
which is how an eval starts flattering the thing it measures. Recorded as the
headline limitation instead, and it is the first item of iteration 3.

**The other failure is a genuine margin.** "How are we doing recently?" came
back `UNSUPPORTED` at 0.67 where I expected `AMBIGUOUS`. The data *can* answer
it once a metric is chosen, so asking back serves the user better than
declining — and 0.67 says the model found it close too. The criteria's
"not a question about this data at all" clause and the undefined-metric clause
genuinely overlap here. Fixable, but changing a classifier the eval already
measures needs its own before/after run, so it belongs to iteration 3 rather
than to a quiet edit inside this one.

**Two settings settled by measurement rather than taste.** A confidence
threshold is rejected: question 12, whose correct answer is zero, sits at
0.40-0.55, *below* several questions that are genuinely harder, so any
threshold misroutes exactly the case the set exists to protect. And thinking
stays on for the answer call — `between_tools` earned its place in the
clarification, where a question that varies between runs is itself a kind of
ambiguity, but judging which caveats are real is the one place in the pipeline
where reasoning is worth paying for.

**The repair attempt was not built, and that is the result.** Zero validation
failures, zero execution failures across 18 questions. The repair loop had a
condition attached to it from the start, and the condition did not fire. It
would have been easy to build it anyway and call the iteration more complete;
what makes it a decision rather than laziness is that the trigger is written
down — a recorded run that fails at `validate` or `execute`.


## Milestone 10 — where vagueness ends and missing data begins

*Iteration 3, first of three. Scope for the iteration was fixed before any
code: this, the modelling assumption behind a number (11), and follow-up
questions with bounded history (12). Review text and the agent loop are out.*

- **Attempted.** Move "how are we doing recently?" from `UNSUPPORTED` (0.67)
  to `AMBIGUOUS` by changing the classifier's criteria text and nothing else.
  No code path, no new call, no new parameter — the only diff in `classify.py`
  is inside two strings.
- **Where AI helped.** Drafting the criteria wording, and pushing back that a
  prompt change to the thing the eval measures only counts if it is recorded
  as its own before/after run rather than argued for.
- **What I decided.** Widen `AMBIGUOUS` to cover an undefined *period*, not
  just an undefined metric, and make `UNSUPPORTED` say what it is actually
  about: what the data **lacks**, not how loosely the question is worded. I
  rejected thresholding confidence for the second time (question 12 still sits
  low), and rejected relaxing question 8's reference so the remaining failure
  would disappear — that reference is the only evidence the limitation exists.
- **What failed.** The first widening was too wide: "how will we be doing next
  quarter?" moved to `AMBIGUOUS` as well. A control test written alongside the
  change caught it; re-reading the prompt had not, and would not have. The
  criteria now carve forecasts out explicitly — the label is only ever for a
  choice the *data* could make. Question 12 ("orders in 2030") is the case
  that carve-out could have broken, and it still passes, which is the only
  reason I trust the wording.
- **Result.** 16/18 → 17/18, classification 17/18 → 18/18, question 16 from
  0.67 `UNSUPPORTED` to 0.95 `AMBIGUOUS`, nothing else moved. Question 8 is
  the only remaining failure.
- **Learned.** A prompt change needs controls on the side you are *not* trying
  to move. Without them you trade one misclassification for another and the
  headline score hides the swap, because it only counts the net. Separately,
  and measured rather than assumed: `LOAD fts` and `vss` are impossible on a
  connection with `enable_external_access=false` (`Permission Error`, duckdb
  1.5.6), and neither is even installed. That is what took full-text and
  vector search over the reviews off the table — the remaining honest route
  would have been embeddings in a plain column ranked with core
  `list_cosine_similarity`, which is real work rather than a flag.

## Milestone 11 — stating the choice behind the number

- **Attempted.** Question 8 of the eval ("the average number of items per
  order") reads precisely and still has two defensible answers, 1.1328 and
  1.1417, depending on whether the 775 orders with no items count. The
  classifier is right to call it ANSWERABLE, so the pipeline cannot ask. It
  can say which denominator it used.
- **What I decided.** Surface, do not detect. The alternative on the table was
  generating the query under both readings and comparing — evidence rather
  than a prompt rule, and genuinely the nicer idea — but it doubles the cost
  of every answerable question and turns question 8 into a clarification,
  which is a change to what the eval expects rather than to what the app gets
  right. `assumption` is a second **required** field on the SQL call's output
  schema: required, because optional would mean absent on the questions that
  need it most.
- **What I refused.** Relaxing question 8's reference so both readings pass.
  That reference is the only evidence the limitation exists; a test changed to
  make an implementation look right measures the implementation's opinion of
  itself.
- **What failed.** My integration test asserted that "how many orders are
  there?" leaves nothing open. The model answered "counted every order
  regardless of status (including canceled and unavailable ones)" — a real
  choice, since 2,965 orders were never delivered. The premise was wrong, not
  the output. I changed the assertion to the failure mode the field actually
  invites: restating the SQL instead of naming the choice.
- **Learned.** A field like this changes what the *tests* can claim. Nothing
  branches on the assumption, no test asserts its wording, and three tests
  assert only that it exists, stays a clause, and is not a paraphrase of the
  query. That is the honest limit of testing prose.

## Milestone 12 — follow-up questions, and the dead end iteration 2 shipped

- **Attempted.** Iteration 2 could ask "revenue, units sold, or review score?"
  and then had no way to hear the answer. One mechanism closes both halves: an
  answered clarification *is* a follow-up turn.
- **Where AI helped.** Drafting `history_block` and the chat rewrite of
  `app.py`. The design decisions below are mine, and two of them are the only
  interesting part of the milestone.
- **Decision: history carries SQL, never rows.** `Turn(question, sql, asked)`
  and nothing else. A follow-up re-derives its scope by querying again,
  because every grounded claim in this app comes from a result its own query
  produced. Pasting the previous rows into the prompt would have been less
  code and would have quietly made the second answer evidence for nothing.
- **Decision: history goes in the user message, not the system prompt.** Two
  reasons that happen to agree. Prompt caching is a prefix match, so a
  per-turn string in the system prompt would invalidate the ~1,070-token
  schema context behind it on every question. And earlier turns are the user's
  own text: in the system prompt they would carry operator authority, which is
  how a question talks the rules out of a model. The rules stay above, the
  conversation below, and a test asserts the placement rather than trusting it.
- **Both caps, not one.** 3 turns *and* 2,000 characters, oldest dropped. I
  wrote the turn cap first and then noticed three turns of 2,000-character SQL
  is a different prompt from three of 300. An oversized single turn is dropped
  rather than truncated — a cut-off query is worse than none, because the
  model reads it as the shape to follow.
- **What failed, twice.** The sidebar's turn counter and reset button lagged a
  question behind: they were written before the new turn was appended.
  `st.sidebar` is a container, so moving that block to the end of the script
  fixed it. Then the full test suite started failing in `test_ingest.py` while
  passing alone — my new UI tests were the first to actually open the app's
  cached connection, and `st.cache_resource` is process-wide, so it stayed
  open for the rest of the session. DuckDB refuses a second connection to the
  same file with a different configuration, which is precisely what a test
  using plain `duckdb.connect()` does. Released in the fixture teardown.
- **The result, and what it does not mean.** 21/21, up from 16/18 at the end
  of iteration 2. But question 8 passed this run and failed the previous one
  with nothing changed in between: this run took the other denominator. That
  is the semantic-ambiguity limitation doing exactly what it was recorded to
  do, and the assumption line is what makes the flip visible instead of
  mysterious. A single score cannot represent that question, which is why
  every run is recorded in full.
- **Learned.** "Write the eval questions before tuning against them" paid for
  itself here: questions 19–21 and the grading note for 20 (label only, because
  a reference value would grade which column the model chose to return rather
  than whether the follow-up worked) were written before any of them had ever
  run.

## Milestone 13 — what a question costs, and the cache that was never on

- **Attempted.** Put a price on every question: tokens per Claude call, $ per
  answer, a cost column in the eval. It turned into an audit of a design claim.
- **What it found.** The history placement in milestone 12 was argued partly
  "for caching" — history in the user message cannot invalidate the cached
  schema. But no request ever set `cache_control`, and the API caches nothing
  unasked. The first metered run: 21/21, $0.181, **0 tokens read from cache**.
  The reasoning was right and the feature it reasoned about was off.
- **Where AI helped.** Claude spotted the missing `cache_control` while reading
  `llm.py` during planning, and proposed measuring before fixing so the gap
  would be a recorded number rather than an assertion.
- **Decision: wrap the client, don't change `llm.py`.** `pipeline._Metered`
  sits between the pipeline and the Claude client and records
  `response.usage` per call. The alternative — every `llm.py` function
  returning its usage — would have changed three return types and their
  tests to carry a fact those functions do not care about.
- **The fix was one breakpoint.** A 5-minute `cache_control` closing the SQL
  and clarify system prompts. Same 21 questions, nothing else changed: 21/21,
  $0.115 (-36%), 38,073 tokens from cache, only the first question writes.
  Both runs archived (`eval_results_m13_*.md`).
- **What nearly went wrong.** Changing `system` from a string to a block list
  made the history-placement test pass vacuously: `"x" not in [ {...} ]` is
  always true. Caught on review and pointed at the block text — a test can be
  weakened by a change to the thing it inspects, not only by editing it.
- **Open.** The answer call is now most of the cost (~2,450 uncached tokens
  per answer) because its schema follows the result rows. Moving it is a
  prompt change that could move answers, so it waits.
- **Learned.** Prompt caching is opt-in and silent when absent; `usage`
  (`cache_read_input_tokens`) is the only place it shows. And the minimum
  cacheable prefix on Sonnet 5.5 is 512 tokens, so a ~2,600-token schema
  prompt qualifies easily.

## Milestone 14 — a chart from the shape of the result

- **Attempted.** Draw a chart when a result has one, chosen by code from the
  column types: time on x is a line, a short list of categories is a bar,
  one number is no chart. "Deterministic before LLM" applied to presentation:
  asking the model which chart to draw would be a call that can be wrong
  about something the types already say.
- **Decision: one measure at a time.** A selector rather than every numeric
  column on one axis, because "orders per month" and "share late" together
  flatten the share into a line along the bottom.
- **What failed, and only the real run showed it.** Every offline test
  passed, then clicking the example buttons against the real pipeline found
  two defects. The default column was the first numeric one, which drew
  `review_count` — the count that qualifies a category — instead of
  `avg_review_score`, the thing asked about; both multi-measure examples put
  the qualifier first, so the default is now the last (a habit of the
  generated SQL, and recorded as one). And month strings sat on a category
  axis, where a month with no orders (2016-11) silently vanishes and the line
  looks unbroken; they are dates now, so the gap shows. Each got a test after
  it was found.
- **Where AI helped.** Writing `chart.py` and the tests, and checking
  `st.bar_chart`'s signature on the installed version (`sort=False` exists in
  1.66, so a ranked result keeps its order) instead of guessing.
- **Learned.** Streamlit's `AppTest` can click the app's own example buttons
  against the live pipeline and read back the Vega-Lite spec of each chart —
  mark, axis type, sort — which made a real end-to-end check cheap (~$0.02)
  and repeatable without a browser. It reads the spec, not the pixels, so a
  look in the browser is still the last check.
