# Talk with your data — Olist

Ask a question in plain language. The app writes DuckDB SQL, validates it,
runs it read-only against the Olist Brazilian e-commerce dataset, and explains
the result from the rows it got back.

It answers, asks back, or declines — and says which. A question it cannot
answer from the data ("what is our profit margin?") is refused rather than
guessed at, and a question with more than one honest reading ("what are the
best products?") gets a clarifying question instead of a silent choice.

## Running it

**You need:** Python 3.14 via [`uv`](https://docs.astral.sh/uv/), an Anthropic
API key, and the Olist CSVs. Costs under a cent per question (measured:
$0.007 for a typical answer with the prompt cache warm, $0.012 cold).

**1. Install.**

```bash
uv sync
```

**2. Get the data.** Download the
[Olist Brazilian E-Commerce dataset](https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce)
from Kaggle (~120 MB, not committed) and unzip all nine CSVs into `data/`:

```
data/olist_customers_dataset.csv          data/olist_order_reviews_dataset.csv
data/olist_geolocation_dataset.csv        data/olist_orders_dataset.csv
data/olist_order_items_dataset.csv        data/olist_products_dataset.csv
data/olist_order_payments_dataset.csv     data/olist_sellers_dataset.csv
data/product_category_name_translation.csv
```

`data/` is only ever read, never written.

**3. Build the database.** Takes a few seconds and prints the row count per
table. Safe to re-run: it replaces every table.

```bash
uv run python -m twyd.ingest     # -> db/olist.duckdb
```

**4. Add your API keys.** Copy `.env.example` to `.env` and fill in both:

```bash
cp .env.example .env
```

`ANTHROPIC_API_KEY` writes the SQL and the answer; `TYPESAFE_API_KEY` runs the
classifier. Keys are read from the environment and never committed.

**5. Run.**

```bash
uv run --env-file .env streamlit run src/twyd/app.py
```

It opens on `127.0.0.1:8501` with example questions to click. Set `TWYD_DB` to
point it at a different database file.

### If something goes wrong

| What you see | Fix |
|---|---|
| `database not found: db/olist.duckdb` | Run step 3 |
| `missing CSV files: ...` | Step 2 — the filenames must match exactly |
| An auth or 401 error from the model | Step 4, and check you launched with `--env-file .env` |
| The app answers but the number looks wrong | Open the SQL and the assumption line the answer carries; `docs/data_model.md` records the traps in this dataset |

Delete `db/` and redo step 3 at any time; nothing else depends on it.

## What to ask it

The examples in the app have all been run through the pipeline at least once.
Questions that work well are concrete and single-purpose:

- *How many customers does the company have?* (96,096 people, not 99,441 order rows)
- *What is the average review score for late deliveries?*
- *Which sellers have the most late deliveries, and what is their average review score?*
- *For each month, how many orders were placed and what share of them arrived late?*

It will decline anything the data does not hold — costs, margins, customer
demographics, stock, or the future — and ask back when a word like "best" has
no agreed meaning. Review text is Portuguese and is not read at all.

## Tests

```bash
uv run pytest                                   # 199 offline, no key needed
uv run --env-file .env pytest -m integration    # 40 real-model tests, ~$0.35
```

Offline tests mock the model and run on tiny fixture CSVs. Integration tests
make real calls and assert analytical meaning rather than SQL strings, since
two correct queries for one question rarely look alike.

## Evaluation

A fixed question set, written before the first run, graded on classification
and on value:

```bash
uv run --env-file .env python -m twyd.eval      # 21 questions, ~$0.20
```

**21/21 on the recorded iteration 3 run** (classification 21/21, values
11/11), including the traps that make this dataset interesting: customers
counted as people rather than order rows, reviews aggregated per order before
averaging, orders with no reviews found by anti-join, and lateness defined by
timestamp rather than status.

Read question 8 before believing the 21 — it passed that run and failed the
one before it with nothing changed between them, which is why every run is
recorded in full rather than reduced to a score.

## Documentation

| Document | What's in it |
|---|---|
| [`docs/architecture.md`](docs/architecture.md) | The pipeline stage by stage, the module contracts, the two independent safety layers, and the model settings |
| [`docs/limitations.md`](docs/limitations.md) | What it gets wrong and why, each tied to a recorded run |
| [`docs/data_model.md`](docs/data_model.md) | The measured grain, keys, null rates and semantic traps of the nine tables |
| [`docs/eval_questions.md`](docs/eval_questions.md) | The question set and the grading rules |
| [`docs/eval_results.md`](docs/eval_results.md) | The latest run, every generated query included (overwritten each run) |
| `docs/eval_results_iteration{1,2,3}.md` | The frozen per-iteration runs |
| [`docs/implementation_plan.md`](docs/implementation_plan.md) | The milestones, and what was deliberately not built |
| [`docs/dev_notes.md`](docs/dev_notes.md) | Working notes: what broke, why, and what changed |

**Single user, localhost.** No authentication or rate limiting, and it holds
two API keys. Do not serve it.
