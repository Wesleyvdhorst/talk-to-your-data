# Evaluation results — iteration 3 (archived)

Frozen: the recorded iteration 3 run, kept as the baseline iteration 4 is measured against. It was produced by `uv run --env-file .env python -m twyd.eval`, which overwrites `eval_results.md` on every run. The question set and the grading rules are in `eval_questions.md`; iteration 2 is archived in `eval_results_iteration2.md` and iteration 1 in `eval_results_iteration1.md`.

Read question 8 before believing the 21: it passed this run and failed the previous one with nothing changed between them. That is the semantic-ambiguity limitation, not a fix — see `limitations.md`.

**21/21 questions correct.** Classification 21/21; values 11/11. Total time 66.4s.

| # | Question | Covers | Verdict | Label | Expected | Got | Ended at | Why not |
|---|---|---|---|---|---|---|---|---|
| 1 | How many orders are there? | count | **PASS** | ANSWERABLE | 99,441 | 99,441 | answer |  |
| 2 | How many customers does the company have? | identity trap | **PASS** | ANSWERABLE | 96,096 | 96,096 | answer |  |
| 3 | How many orders were placed in 2017? | time filter | **PASS** | ANSWERABLE | 45,101 | 45,101 | answer |  |
| 4 | What is the average review score? | aggregation | **PASS** | ANSWERABLE | 4.0864 | 4.0864 | answer |  |
| 5 | What is the average review score for orders delivered later than estimated? | join inflation trap | **PASS** | ANSWERABLE | 2.5666 | 2.5665 | answer |  |
| 6 | How many orders have no reviews at all? | anti-join trap | **PASS** | ANSWERABLE | 768 | 768 | answer |  |
| 7 | Which customer state has the most orders? | join + group by + ordering | **PASS** | ANSWERABLE | SP | SP | answer |  |
| 8 | What is the average number of items per order? | child-table grain | **PASS** | ANSWERABLE | 1.1417 | 1.1417 | answer |  |
| 9 | What are the best products? | ambiguous | **PASS** | AMBIGUOUS | — | — | clarify |  |
| 10 | What will sales be next month? | unsupported: the future | **PASS** | UNSUPPORTED | — | — | classify |  |
| 11 | asdfgh | malformed | **PASS** | UNSUPPORTED | — | — | classify |  |
| 12 | How many orders were placed in 2030? | empty result is correct, NOT unsupported | **PASS** | ANSWERABLE | 0 | 0 | answer |  |
| 13 | What is our profit margin on electronics? | unsupported: no cost or margin data | **PASS** | UNSUPPORTED | — | — | classify |  |
| 14 | How old are our customers? | unsupported: no demographics | **PASS** | UNSUPPORTED | — | — | classify |  |
| 15 | Which sellers perform best? | ambiguous: undefined metric | **PASS** | AMBIGUOUS | — | — | clarify |  |
| 16 | How are we doing recently? | ambiguous: undefined metric and undefined period | **PASS** | AMBIGUOUS | — | — | clarify |  |
| 17 | What is the average review score for each customer state? | grounding: many rows, and the answer must not miscount them | **PASS** | ANSWERABLE | — | AC | answer |  |
| 18 | What is the average freight value per order? | answerable only if the classifier knows the columns | **PASS** | ANSWERABLE | 22.8236 | 22.8236 | answer |  |
| 19 | and in 2018? | follow-up: the previous question with one thing changed | **PASS** | ANSWERABLE | 54,011 | 54,011 | answer |  |
| 20 | by revenue | the clarification loop closing: an answered clarification is a turn | **PASS** | ANSWERABLE | — | bb50f2e236e5eea0100680137654686c | answer |  |
| 21 | and for late deliveries? | a follow-up with nothing to follow: must ask, not guess | **PASS** | AMBIGUOUS | — | — | clarify |  |

## What each question produced

### 1. How many orders are there?

*count — PASS, 3.7s*

```
classify         ANSWERABLE - confidence 0.97, jev-1.13.0
generate_sql     42 characters - counted every order in the orders table regardless of status (including canceled and unavailable)
validate         passed
run_query        1 rows
generate_answer  139 characters
```

```sql
SELECT COUNT(*) AS order_count FROM orders
```

**Answer:** There are 99441 orders in total. This counts every order in the orders table regardless of status, including canceled and unavailable ones.

### 2. How many customers does the company have?

*identity trap — PASS, 3.8s*

```
classify         ANSWERABLE - confidence 0.82, jev-1.13.0
generate_sql     74 characters - Customers are counted as distinct real people (customer_unique_id), including those whose orders were canceled or never delivered, rather than one per order.
validate         passed
run_query        1 rows
generate_answer  187 characters
```

```sql
SELECT COUNT(DISTINCT customer_unique_id) AS customer_count FROM customers
```

**Answer:** The company has 96,096 customers. This counts distinct real people (by customer_unique_id) rather than one per order, and it includes people whose orders were canceled or never delivered.

### 3. How many orders were placed in 2017?

*time filter — PASS, 4.5s*

```
classify         ANSWERABLE - confidence 1.00, jev-1.13.0
generate_sql     153 characters - An order counts as placed in 2017 by its purchase timestamp, and all order statuses (including canceled) are included.
validate         passed
run_query        1 rows
generate_answer  159 characters
```

```sql
SELECT COUNT(*) AS order_count FROM orders WHERE order_purchase_timestamp >= TIMESTAMP '2017-01-01' AND order_purchase_timestamp < TIMESTAMP '2018-01-01'
```

**Answer:** There were 45,101 orders placed in 2017. An order counts as placed in 2017 by its purchase timestamp, and all order statuses, including canceled, are included.

### 4. What is the average review score?

*aggregation — PASS, 5.3s*

```
classify         ANSWERABLE - confidence 0.97, jev-1.13.0
generate_sql     63 characters - Averaged over all review rows, so orders with multiple reviews count once per review and orders with no review are excluded
validate         passed
run_query        1 rows
generate_answer  172 characters
```

```sql
SELECT AVG(review_score) AS avg_review_score FROM order_reviews
```

**Answer:** The average review score is about 4.09. This is averaged over all review rows, so orders with multiple reviews count once per review and orders with no review are excluded.

### 5. What is the average review score for orders delivered later than estimated?

*join inflation trap — PASS, 6.8s*

```
classify         ANSWERABLE - confidence 0.97, jev-1.13.0
generate_sql     217 characters - Averaged over all reviews of late orders (orders with multiple reviews count once per review; late orders with no review are excluded)
validate         passed
run_query        1 rows
generate_answer  227 characters
```

```sql
WITH late AS (SELECT order_id FROM orders WHERE order_delivered_customer_date > order_estimated_delivery_date) SELECT AVG(r.review_score) AS avg_review_score FROM late l JOIN order_reviews r ON r.order_id = l.order_id
```

**Answer:** Orders delivered later than estimated have an average review score of about 2.57. This averages over all reviews of late orders, so orders with multiple reviews count once per review and late orders with no review are excluded.

### 6. How many orders have no reviews at all?

*anti-join trap — PASS, 3.4s*

```
classify         ANSWERABLE - confidence 1.00, jev-1.13.0
generate_sql     134 characters
validate         passed
run_query        1 rows
generate_answer  130 characters
```

```sql
SELECT COUNT(*) AS orders_without_reviews FROM orders o WHERE NOT EXISTS (SELECT 1 FROM order_reviews r WHERE r.order_id = o.order_id)
```

**Answer:** 768 orders have no reviews at all. The count covers every order in the orders table that has no matching row in the reviews table.

### 7. Which customer state has the most orders?

*join + group by + ordering — PASS, 4.4s*

```
classify         ANSWERABLE - confidence 1.00, jev-1.13.0
generate_sql     172 characters - Counted all orders regardless of order status (including canceled and unavailable), attributing each to the state on its customer record.
validate         passed
run_query        1 rows
generate_answer  192 characters
```

```sql
SELECT c.customer_state, COUNT(*) AS order_count FROM orders o JOIN customers c ON o.customer_id = c.customer_id GROUP BY c.customer_state ORDER BY order_count DESC LIMIT 1
```

**Answer:** São Paulo (SP) has the most orders, with 41,746. That count includes every order regardless of status (including canceled and unavailable), each attributed to the state on its customer record.

### 8. What is the average number of items per order?

*child-table grain — PASS, 3.1s*

```
classify         ANSWERABLE - confidence 0.98, jev-1.13.0
generate_sql     129 characters - Averaged only over orders that have at least one item line (the 775 orders with no items are excluded), counting each item line as one item regardless of quantity.
validate         passed
run_query        1 rows
generate_answer  216 characters
```

```sql
SELECT AVG(item_count) AS avg_items_per_order FROM (SELECT order_id, COUNT(*) AS item_count FROM order_items GROUP BY order_id) t
```

**Answer:** Orders have about 1.14 items on average. This counts each item line as one item regardless of quantity, and it averages only over orders that have at least one item line, so the 775 orders with no items are excluded.

### 9. What are the best products?

*ambiguous — PASS, 1.8s*

```
classify         AMBIGUOUS - confidence 0.98, jev-1.13.0
clarify          asked which reading was meant
```

**Did not answer:** By "best" do you mean the highest revenue (sum of item price), the most units sold (count of order item lines), or the highest average review score?

### 10. What will sales be next month?

*unsupported: the future — PASS, 0.3s*

```
classify         UNSUPPORTED - confidence 0.66, jev-1.13.0
```

**Did not answer:** This dataset cannot answer that. It holds Olist's Brazilian e-commerce transactions from September 2016 to October 2018 - orders, items, payments, reviews, products, sellers and locations - with no costs, margins, customer demographics or stock, and nothing about the future.

### 11. asdfgh

*malformed — PASS, 0.3s*

```
classify         UNSUPPORTED - confidence 1.00, jev-1.13.0
```

**Did not answer:** This dataset cannot answer that. It holds Olist's Brazilian e-commerce transactions from September 2016 to October 2018 - orders, items, payments, reviews, products, sellers and locations - with no costs, margins, customer demographics or stock, and nothing about the future.

### 12. How many orders were placed in 2030?

*empty result is correct, NOT unsupported — PASS, 4.4s*

```
classify         ANSWERABLE - confidence 0.51, jev-1.13.0
generate_sql     153 characters - An order is 'placed' on its purchase timestamp, counting all order statuses including cancelled ones
validate         passed
run_query        1 rows
generate_answer  163 characters
```

```sql
SELECT COUNT(*) AS order_count FROM orders WHERE order_purchase_timestamp >= TIMESTAMP '2030-01-01' AND order_purchase_timestamp < TIMESTAMP '2031-01-01'
```

**Answer:** No orders were placed in 2030: the count is 0. This counts an order as placed on its purchase timestamp and includes all order statuses, cancelled ones among them.

### 13. What is our profit margin on electronics?

*unsupported: no cost or margin data — PASS, 0.3s*

```
classify         UNSUPPORTED - confidence 0.98, jev-1.13.0
```

**Did not answer:** This dataset cannot answer that. It holds Olist's Brazilian e-commerce transactions from September 2016 to October 2018 - orders, items, payments, reviews, products, sellers and locations - with no costs, margins, customer demographics or stock, and nothing about the future.

### 14. How old are our customers?

*unsupported: no demographics — PASS, 0.2s*

```
classify         UNSUPPORTED - confidence 1.00, jev-1.13.0
```

**Did not answer:** This dataset cannot answer that. It holds Olist's Brazilian e-commerce transactions from September 2016 to October 2018 - orders, items, payments, reviews, products, sellers and locations - with no costs, margins, customer demographics or stock, and nothing about the future.

### 15. Which sellers perform best?

*ambiguous: undefined metric — PASS, 2.1s*

```
classify         AMBIGUOUS - confidence 0.97, jev-1.13.0
clarify          asked which reading was meant
```

**Did not answer:** By "perform best" do you mean highest revenue (sum of item prices), most orders fulfilled, highest average review score, or fewest late deliveries?

### 16. How are we doing recently?

*ambiguous: undefined metric and undefined period — PASS, 2.4s*

```
classify         AMBIGUOUS - confidence 0.98, jev-1.13.0
clarify          asked which reading was meant
```

**Did not answer:** By "how are we doing" do you mean sales (revenue or order count), customer satisfaction (average review score), or delivery performance (share of late orders), and over what window: the last month, the last quarter, or the whole period?

### 17. What is the average review score for each customer state?

*grounding: many rows, and the answer must not miscount them — PASS, 4.4s*

```
classify         ANSWERABLE - confidence 0.98, jev-1.13.0
generate_sql     228 characters - Averaged over all individual reviews (orders with multiple reviews count each review, orders with no review are excluded), grouped by the state on the customer's order record.
validate         passed
run_query        27 rows
generate_answer  505 characters
```

```sql
SELECT c.customer_state, AVG(r.review_score) AS avg_review_score FROM order_reviews r JOIN orders o ON o.order_id = r.order_id JOIN customers c ON c.customer_id = o.customer_id GROUP BY c.customer_state ORDER BY c.customer_state
```

**Answer:** The query returned average review scores for all 27 customer states (27 rows, complete). Most states sit between about 3.8 and 4.2: SP averages 4.17, MG 4.14 and PR 4.18, while RJ is 3.87 and BA 3.86. The lowest is RR at 3.61, and the highest is AM at 4.18 and AP at 4.19 (AP edges slightly ahead of PR's 4.18).

The averages are taken over all individual reviews, so orders with multiple reviews count each review and orders with no review are excluded. States are taken from the customer's order record.

### 18. What is the average freight value per order?

*answerable only if the classifier knows the columns — PASS, 4.6s*

```
classify         ANSWERABLE - confidence 0.85, jev-1.13.0
generate_sql     157 characters - Averaged over orders that have at least one item (the 775 orders without items are excluded), using the sum of freight across each order's item lines
validate         passed
run_query        1 rows
generate_answer  205 characters
```

```sql
WITH f AS (SELECT order_id, SUM(freight_value) AS total_freight FROM order_items GROUP BY order_id) SELECT AVG(total_freight) AS avg_freight_per_order FROM f
```

**Answer:** The average freight value per order is about 22.82, summing freight across each order's item lines. This averages only over orders that have at least one item, so the 775 orders without items are excluded.

### 19. and in 2018?

*follow-up: the previous question with one thing changed — PASS, 3.7s*

```
classify         ANSWERABLE - confidence 1.00, jev-1.13.0
generate_sql     153 characters - Orders are dated by purchase timestamp and all statuses (including canceled) are counted
validate         passed
run_query        1 rows
generate_answer  131 characters
```

```sql
SELECT COUNT(*) AS order_count FROM orders WHERE order_purchase_timestamp >= TIMESTAMP '2018-01-01' AND order_purchase_timestamp < TIMESTAMP '2019-01-01'
```

**Answer:** There were 54,011 orders placed in 2018. This counts orders by purchase date and includes every status, canceled orders among them.

### 20. by revenue

*the clarification loop closing: an answered clarification is a turn — PASS, 5.1s*

```
classify         ANSWERABLE - confidence 0.91, jev-1.13.0
generate_sql     223 characters - Revenue is the sum of item price (excluding freight) over all order items regardless of order status, and 'best' means the top 10 products
validate         passed
run_query        10 rows
generate_answer  520 characters
```

```sql
SELECT oi.product_id, p.product_category_name, SUM(oi.price) AS revenue FROM order_items oi LEFT JOIN products p ON p.product_id = oi.product_id GROUP BY oi.product_id, p.product_category_name ORDER BY revenue DESC LIMIT 10
```

**Answer:** The top product by revenue is bb50f2e236e5eea0100680137654686c (beleza_saude) at 63,885.0, followed by 6cdd53843498f92890544667809f1595 (beleza_saude) at 54,730.2 and d6160fb7873f184099d9bc95e30376af (pcs) at 48,899.34. The result has 10 rows and is complete, and the 10th product, aca2eb7d00ea1a7b8ebd4e68314663af (moveis_decoracao), comes in at 37,608.9.

Revenue here is the sum of item price (excluding freight) over all order items regardless of order status, and "by revenue" was taken to mean the top 10 products.

### 21. and for late deliveries?

*a follow-up with nothing to follow: must ask, not guess — PASS, 1.8s*

```
classify         AMBIGUOUS - confidence 0.79, jev-1.13.0
clarify          asked which reading was meant
```

**Did not answer:** Do you want to know how many orders were late, what share of delivered orders were late, or how many days late they ran on average?

