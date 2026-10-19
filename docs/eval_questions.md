# Evaluation set

Twelve questions, written **before** the first eval run and before any prompt
tuning, so nothing here leaked from watching the model succeed or fail.

Every reference value was produced by SQL I wrote myself, independently of the
model, and is recomputed at eval time rather than hard-coded — if the database
is rebuilt, the references move with it. The reference SQL lives in
`src/twyd/eval.py` next to each question.

Grading is deterministic. A question is **correct** when the model's query
returns the same value as the reference query. There is no LLM judge: deciding
whether 96,096 equals 96,096 does not need a model, and a judge would add a
failure mode the thing being measured already has.

## Graded questions (8)

| # | Question | Covers | Reference |
|---|---|---|---|
| 1 | How many orders are there? | count | 99,441 |
| 2 | How many customers does the company have? | **identity trap** — must be `customer_unique_id`, not `customer_id` | 96,096 |
| 3 | How many orders were placed in 2017? | time filter | 45,101 |
| 4 | What is the average review score? | aggregation | 4.09 |
| 5 | What is the average review score for orders delivered later than estimated? | **join inflation trap** — reviews must be aggregated per order before averaging, and "late" is a timestamp comparison, not a status | 2.57 |
| 6 | How many orders have no reviews at all? | **null/anti-join trap** — an inner join silently drops these | 768 |
| 7 | Which customer state has the most orders? | join + group by + ordering | SP (41,746) |
| 8 | What is the average number of items per order? | aggregation over a child table at the right grain | 1.14 |

## Record-only questions (4)

Iteration 1 has no question classification, no clarification and no repair, so
these have no pass/fail. The eval records what the baseline actually does, and
that record is the evidence for what iteration 2 should fix.

| # | Question | Why it is here | What I expect |
|---|---|---|---|
| 9 | What are the best products? | ambiguous — "best" could mean rating, revenue, units, or repeat purchases | Picks one meaning silently. Iteration 2 should ask instead. |
| 10 | What will sales be next month? | unsupported — no forecast exists in this data | Either refuses, or answers a past-looking question as if it were the forecast. The second would be the more interesting failure. |
| 11 | asdfgh | malformed | Some SQL gets generated and fails, or an answer is produced from nothing. Either way the stage should be named rather than crashing. |
| 12 | How many orders were placed in 2030? | **empty result that is a correct answer** — zero rows is the truth here, not a failure | Answers zero without inventing a reason. |

## What is deliberately not measured

- **Answer wording.** The known defect (the customer count described as
  "distinct customer IDs" when the SQL correctly used `customer_unique_id`) is
  a prose problem, and no assertion here catches it. Graded by reading.
- **SQL string equality.** Two correct queries for the same question rarely
  look alike; only the value is compared.
- **Latency and cost.** Recorded in the results table as context, not graded.
