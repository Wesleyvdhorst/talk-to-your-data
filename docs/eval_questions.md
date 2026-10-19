# Evaluation set

Twenty-one questions. Twelve were written **before** the first iteration 1
run; six more (13–18) **before** the first iteration 2 run; three more
(19–21) **before** the first run with follow-ups. Questions 1–8 are untouched
— they are the baseline every later iteration is measured against.

Every reference value is produced by SQL I wrote myself, independently of the
model, and is recomputed at eval time rather than hard-coded — if the database
is rebuilt, the references move with it. The reference SQL lives in
`src/twyd/eval.py` next to each question.

Grading is deterministic. There is no LLM judge: deciding whether 96,096
equals 96,096 does not need a model, and a judge would add the failure mode
the thing being measured already has.

## What is graded

Three checks, each of which can fail on its own:

| Check | Applies to | Passes when |
|---|---|---|
| **Label** | every question | the classifier returns the expected `ANSWERABLE` / `AMBIGUOUS` / `UNSUPPORTED` |
| **Value** | the 11 questions with a reference query | the model's query returns the same value, within 0.005 |
| **Rows** | question 17 | the result has as many rows as the reference count |

An `AMBIGUOUS` question has one extra condition: the pipeline must *ask*
something, checked by looking for a question mark in the message. Labelling a
question ambiguous and then saying nothing useful is not a pass — the point of
the label is to ask.

A question expected to be declined has **no** reference query. There is
nothing to execute: if the pipeline generated SQL for it, that is the failure.

## Graded on label and value (10)

| # | Question | Covers | Label | Reference |
|---|---|---|---|---|
| 1 | How many orders are there? | count | ANSWERABLE | 99,441 |
| 2 | How many customers does the company have? | **identity trap** — must be `customer_unique_id` | ANSWERABLE | 96,096 |
| 3 | How many orders were placed in 2017? | time filter | ANSWERABLE | 45,101 |
| 4 | What is the average review score? | aggregation | ANSWERABLE | 4.09 |
| 5 | What is the average review score for orders delivered later than estimated? | **join inflation trap** — aggregate reviews per order first; "late" is a timestamp comparison, not a status | ANSWERABLE | 2.57 |
| 6 | How many orders have no reviews at all? | **anti-join trap** — an inner join silently drops these | ANSWERABLE | 768 |
| 7 | Which customer state has the most orders? | join + group by + ordering | ANSWERABLE | SP |
| 8 | What is the average number of items per order? | child-table grain | ANSWERABLE | 1.14 |
| 12 | How many orders were placed in 2030? | **an empty result that is correct** — and emphatically *not* UNSUPPORTED | ANSWERABLE | 0 |
| 18 | What is the average freight value per order? | answerable only if the classifier knows the **columns**, not just the table names | ANSWERABLE | — |

Question 12 is the one the first version of the classification criteria got
wrong, at 0.98 confidence: "outside the covered period" beat "an answer of
zero is still an answer". It is graded here so that cannot regress silently.

## Graded on label and row count (1)

| # | Question | Covers | Label |
|---|---|---|---|
| 17 | What is the average review score for each customer state? | **grounding** — a many-row result whose answer must not miscount it | ANSWERABLE |

This is the deterministic half of the defect milestone 6 fixed, where a 20-row
result was described as 19 rows. The row count is graded; the prose is read.

## Graded on label alone (8)

These have no value to check: a declined question produces no SQL.

| # | Question | Why it is here | Label |
|---|---|---|---|
| 9 | What are the best products? | "best" could mean revenue, units, or review score | AMBIGUOUS |
| 10 | What will sales be next month? | the future is not in this data | UNSUPPORTED |
| 11 | asdfgh | not a question at all; iteration 1 answered it *through SQL* | UNSUPPORTED |
| 13 | What is our profit margin on electronics? | no cost or margin data exists | UNSUPPORTED |
| 14 | How old are our customers? | no demographics exist | UNSUPPORTED |
| 15 | Which sellers perform best? | undefined metric | AMBIGUOUS |
| 16 | How are we doing recently? | undefined metric **and** undefined period | AMBIGUOUS |

Questions 9–12 were record-only in iteration 1, which had no classifier to be
right or wrong. They are graded now, and that is most of what iteration 2 adds
to this set.

## Follow-ups (3)

New in iteration 3. Each needs conversation history to mean anything, which is
the point: questions 19 and 20 carry a **prior turn** that is asked first,
through the same pipeline, so the history under test is real rather than a
hand-written transcript. Question 21 carries none.

| # | Prior turn | Question | Graded on | Why |
|---|---|---|---|---|
| 19 | "How many orders were placed in 2017?" | "and in 2018?" | label + value | The plain follow-up: the previous question with one thing changed. The reference is a `count(*)` for 2018, so it is as deterministic as question 3 |
| 20 | "What are the best products?" (which asks back) | "by revenue" | label only, must be ANSWERABLE | The clarification loop closing. Iteration 2's dead end: the app asked which reading and the user had no way to answer |
| 21 | *(none)* | "and for late deliveries?" | label only, must be AMBIGUOUS | A follow-up with nothing to follow. Must ask rather than guess a measure |

Question 20 is graded on its label alone on purpose. A reference value would
grade which column the model chose to return — `product_id`, a category name,
a top-10 list — and not whether the follow-up worked. If the clarification
loop breaks, the label comes back `AMBIGUOUS` again and the question fails,
which is exactly the signal wanted.

## What is deliberately not measured

- **Answer wording.** Whether prose is well grounded is graded by reading it.
  The row count behind it (question 17) is the part a test can check.
- **The clarification's content.** That it asks is checked; which readings it
  offers is read.
- **Classifier confidence.** Recorded in the run, never a pass condition.
  Thresholding it would have failed question 12, which sits at 0.40–0.55 while
  the clear questions sit above 0.90.
- **SQL string equality.** Two correct queries rarely look alike; only the
  value is compared.
- **Latency and cost.** Recorded as context, not graded.
