# Evaluation results — milestone 13, before caching (archived)

Frozen. Produced by `uv run --env-file .env python -m twyd.eval`, which overwrites `eval_results.md` on every run. Milestone 13 ran the eval twice with nothing changed between them except the cache breakpoint; the cost column and the cache-read total in the header are the comparison. Cost counts graded turns only, priced at Sonnet 5.5 list prices. **This is the before run:** no `cache_control` anywhere, 0 tokens read from cache.

**21/21 questions correct.** Classification 21/21; values 11/11. Total time 68.9s. Claude cost $0.1810, 0 input tokens read from cache.

| # | Question | Covers | Verdict | Label | Expected | Got | Ended at | Cost | Why not |
|---|---|---|---|---|---|---|---|---|---|
| 1 | How many orders are there? | count | **PASS** | ANSWERABLE | 99,441 | 99,441 | answer | $0.0111 |  |
| 2 | How many customers does the company have? | identity trap | **PASS** | ANSWERABLE | 96,096 | 96,096 | answer | $0.0116 |  |
| 3 | How many orders were placed in 2017? | time filter | **PASS** | ANSWERABLE | 45,101 | 45,101 | answer | $0.0123 |  |
| 4 | What is the average review score? | aggregation | **PASS** | ANSWERABLE | 4.0864 | 4.0864 | answer | $0.0118 |  |
| 5 | What is the average review score for orders delivered later than estimated? | join inflation trap | **PASS** | ANSWERABLE | 2.5666 | 2.5665 | answer | $0.0125 |  |
| 6 | How many orders have no reviews at all? | anti-join trap | **PASS** | ANSWERABLE | 768 | 768 | answer | $0.0115 |  |
| 7 | Which customer state has the most orders? | join + group by + ordering | **PASS** | ANSWERABLE | SP | SP | answer | $0.0122 |  |
| 8 | What is the average number of items per order? | child-table grain | **PASS** | ANSWERABLE | 1.1417 | 1.1417 | answer | $0.0120 |  |
| 9 | What are the best products? | ambiguous | **PASS** | AMBIGUOUS | — | — | clarify | $0.0050 |  |
| 10 | What will sales be next month? | unsupported: the future | **PASS** | UNSUPPORTED | — | — | classify | $0.0000 |  |
| 11 | asdfgh | malformed | **PASS** | UNSUPPORTED | — | — | classify | $0.0000 |  |
| 12 | How many orders were placed in 2030? | empty result is correct, NOT unsupported | **PASS** | ANSWERABLE | 0 | 0 | answer | $0.0120 |  |
| 13 | What is our profit margin on electronics? | unsupported: no cost or margin data | **PASS** | UNSUPPORTED | — | — | classify | $0.0000 |  |
| 14 | How old are our customers? | unsupported: no demographics | **PASS** | UNSUPPORTED | — | — | classify | $0.0000 |  |
| 15 | Which sellers perform best? | ambiguous: undefined metric | **PASS** | AMBIGUOUS | — | — | clarify | $0.0051 |  |
| 16 | How are we doing recently? | ambiguous: undefined metric and undefined period | **PASS** | AMBIGUOUS | — | — | clarify | $0.0052 |  |
| 17 | What is the average review score for each customer state? | grounding: many rows, and the answer must not miscount them | **PASS** | ANSWERABLE | — | AC | answer | $0.0145 |  |
| 18 | What is the average freight value per order? | answerable only if the classifier knows the columns | **PASS** | ANSWERABLE | 22.8236 | 22.8236 | answer | $0.0121 |  |
| 19 | and in 2018? | follow-up: the previous question with one thing changed | **PASS** | ANSWERABLE | 54,011 | 54,011 | answer | $0.0122 |  |
| 20 | by revenue | the clarification loop closing: an answered clarification is a turn | **PASS** | ANSWERABLE | — | bb50f2e236e5eea0100680137654686c | answer | $0.0147 |  |
| 21 | and for late deliveries? | a follow-up with nothing to follow: must ask, not guess | **PASS** | AMBIGUOUS | — | — | clarify | $0.0051 |  |

## What each question produced

### 1. How many orders are there?

*count — PASS, 4.7s*

```
classify         ANSWERABLE - confidence 0.98, jev-1.13.0
generate_sql     42 characters - counted every order in the orders table regardless of status (including canceled and undelivered)
validate         passed
run_query        1 rows
generate_answer  131 characters
cost             2 calls, 5,060 in / 102 out, 0 cache read / 0 written, $0.0111
```

```sql
SELECT COUNT(*) AS order_count FROM orders
```

**Answer:** There are 99,441 orders. This counts every order in the orders table regardless of status, including canceled and undelivered ones.

### 2. How many customers does the company have?

*identity trap — PASS, 3.6s*

```
classify         ANSWERABLE - confidence 0.85, jev-1.13.0
generate_sql     74 characters - Customers are counted as distinct real people (customer_unique_id), including those whose orders were never delivered or were canceled, rather than one per order.
validate         passed
run_query        1 rows
generate_answer  168 characters
cost             2 calls, 5,099 in / 142 out, 0 cache read / 0 written, $0.0116
```

```sql
SELECT COUNT(DISTINCT customer_unique_id) AS customer_count FROM customers
```

**Answer:** The company has 96,096 customers. This counts distinct real people rather than one per order, and it includes people whose orders were never delivered or were canceled.

### 3. How many orders were placed in 2017?

*time filter — PASS, 4.9s*

```
classify         ANSWERABLE - confidence 1.00, jev-1.13.0
generate_sql     153 characters - An order counts as placed in 2017 by its purchase timestamp, regardless of order status (canceled and unavailable orders included)
validate         passed
run_query        1 rows
generate_answer  183 characters
cost             2 calls, 5,147 in / 201 out, 0 cache read / 0 written, $0.0123
```

```sql
SELECT COUNT(*) AS order_count FROM orders WHERE order_purchase_timestamp >= TIMESTAMP '2017-01-01' AND order_purchase_timestamp < TIMESTAMP '2018-01-01'
```

**Answer:** There were 45,101 orders placed in 2017. That counts an order as placed in 2017 by its purchase timestamp, regardless of order status, so canceled and unavailable orders are included.

### 4. What is the average review score?

*aggregation — PASS, 4.4s*

```
classify         ANSWERABLE - confidence 0.97, jev-1.13.0
generate_sql     63 characters - Averaged over all review rows (each review counted once, including orders with multiple reviews), not one score per order; orders with no review are excluded
validate         passed
run_query        1 rows
generate_answer  248 characters
cost             2 calls, 5,095 in / 160 out, 0 cache read / 0 written, $0.0118
```

```sql
SELECT AVG(review_score) AS avg_review_score FROM order_reviews
```

**Answer:** The average review score is 4.09 (4.08642062404257 before rounding). This is averaged over all review rows, so each review counts once, including orders with multiple reviews, rather than one score per order, and orders with no review are excluded.

### 5. What is the average review score for orders delivered later than estimated?

*join inflation trap — PASS, 3.7s*

```
classify         ANSWERABLE - confidence 0.97, jev-1.13.0
generate_sql     180 characters - Averaged over individual reviews, so orders with multiple reviews count once per review and orders with no review are excluded
validate         passed
run_query        1 rows
generate_answer  289 characters
cost             2 calls, 5,163 in / 213 out, 0 cache read / 0 written, $0.0125
```

```sql
SELECT AVG(r.review_score) AS avg_review_score FROM order_reviews r JOIN orders o ON o.order_id = r.order_id WHERE o.order_delivered_customer_date > o.order_estimated_delivery_date
```

**Answer:** Orders delivered later than estimated have an average review score of about 2.57. This is averaged over individual reviews, so orders with multiple reviews count once per review and orders with no review are excluded. Only orders with a delivery date after the estimated date are included.

### 6. How many orders have no reviews at all?

*anti-join trap — PASS, 3.4s*

```
classify         ANSWERABLE - confidence 1.00, jev-1.13.0
generate_sql     134 characters
validate         passed
run_query        1 rows
generate_answer  150 characters
cost             2 calls, 5,081 in / 132 out, 0 cache read / 0 written, $0.0115
```

```sql
SELECT COUNT(*) AS orders_without_reviews FROM orders o WHERE NOT EXISTS (SELECT 1 FROM order_reviews r WHERE r.order_id = o.order_id)
```

**Answer:** 768 orders have no reviews at all. This counts orders with no matching row in the reviews table, so an order with one or more reviews is not included.

### 7. Which customer state has the most orders?

*join + group by + ordering — PASS, 5.1s*

```
classify         ANSWERABLE - confidence 1.00, jev-1.13.0
generate_sql     172 characters - Counted all orders regardless of order status (including canceled and undelivered), attributing each to the state on its customer record
validate         passed
run_query        1 rows
generate_answer  190 characters
cost             2 calls, 5,155 in / 192 out, 0 cache read / 0 written, $0.0122
```

```sql
SELECT c.customer_state, COUNT(*) AS order_count FROM orders o JOIN customers c ON o.customer_id = c.customer_id GROUP BY c.customer_state ORDER BY order_count DESC LIMIT 1
```

**Answer:** SP (São Paulo) has the most orders, with 41,746. This counts all orders regardless of order status (including canceled and undelivered), attributing each to the state on its customer record.

### 8. What is the average number of items per order?

*child-table grain — PASS, 3.4s*

```
classify         ANSWERABLE - confidence 0.98, jev-1.13.0
generate_sql     88 characters - Averaged over orders that have at least one item line (the 775 orders with no items are excluded), counting each item line as one item
validate         passed
run_query        1 rows
generate_answer  228 characters
cost             2 calls, 5,122 in / 177 out, 0 cache read / 0 written, $0.0120
```

```sql
SELECT COUNT(*) * 1.0 / COUNT(DISTINCT order_id) AS avg_items_per_order FROM order_items
```

**Answer:** Orders average about 1.14 items each (avg_items_per_order = 1.1417306873695092). This is averaged over orders that have at least one item line, so the 775 orders with no items are excluded, and each item line counts as one item.

### 9. What are the best products?

*ambiguous — PASS, 2.2s*

```
classify         AMBIGUOUS - confidence 0.98, jev-1.13.0
clarify          asked which reading was meant
cost             1 call, 2,227 in / 55 out, 0 cache read / 0 written, $0.0050
```

**Did not answer:** By "best" do you mean the products with the highest revenue (sum of item price), the most units sold (count of order item lines), or the highest average review score?

### 10. What will sales be next month?

*unsupported: the future — PASS, 0.8s*

```
classify         UNSUPPORTED - confidence 0.72, jev-1.13.0
```

**Did not answer:** This dataset cannot answer that. It holds Olist's Brazilian e-commerce transactions from September 2016 to October 2018 - orders, items, payments, reviews, products, sellers and locations - with no costs, margins, customer demographics or stock, and nothing about the future.

### 11. asdfgh

*malformed — PASS, 0.4s*

```
classify         UNSUPPORTED - confidence 1.00, jev-1.13.0
```

**Did not answer:** This dataset cannot answer that. It holds Olist's Brazilian e-commerce transactions from September 2016 to October 2018 - orders, items, payments, reviews, products, sellers and locations - with no costs, margins, customer demographics or stock, and nothing about the future.

### 12. How many orders were placed in 2030?

*empty result is correct, NOT unsupported — PASS, 4.2s*

```
classify         ANSWERABLE - confidence 0.51, jev-1.13.0
generate_sql     153 characters - An order is counted as placed in the year of its purchase timestamp, with all order statuses included
validate         passed
run_query        1 rows
generate_answer  159 characters
cost             2 calls, 5,133 in / 176 out, 0 cache read / 0 written, $0.0120
```

```sql
SELECT COUNT(*) AS order_count FROM orders WHERE order_purchase_timestamp >= TIMESTAMP '2030-01-01' AND order_purchase_timestamp < TIMESTAMP '2031-01-01'
```

**Answer:** No orders were placed in 2030: the count came back as 0. An order is counted as placed in the year of its purchase timestamp, with all order statuses included.

### 13. What is our profit margin on electronics?

*unsupported: no cost or margin data — PASS, 0.3s*

```
classify         UNSUPPORTED - confidence 0.98, jev-1.13.0
```

**Did not answer:** This dataset cannot answer that. It holds Olist's Brazilian e-commerce transactions from September 2016 to October 2018 - orders, items, payments, reviews, products, sellers and locations - with no costs, margins, customer demographics or stock, and nothing about the future.

### 14. How old are our customers?

*unsupported: no demographics — PASS, 0.3s*

```
classify         UNSUPPORTED - confidence 1.00, jev-1.13.0
```

**Did not answer:** This dataset cannot answer that. It holds Olist's Brazilian e-commerce transactions from September 2016 to October 2018 - orders, items, payments, reviews, products, sellers and locations - with no costs, margins, customer demographics or stock, and nothing about the future.

### 15. Which sellers perform best?

*ambiguous: undefined metric — PASS, 2.5s*

```
classify         AMBIGUOUS - confidence 0.98, jev-1.13.0
clarify          asked which reading was meant
cost             1 call, 2,230 in / 63 out, 0 cache read / 0 written, $0.0051
```

**Did not answer:** By "perform best" do you mean highest revenue (sum of item price), most orders fulfilled, or best customer review scores? Or the lowest share of late deliveries?

### 16. How are we doing recently?

*ambiguous: undefined metric and undefined period — PASS, 2.3s*

```
classify         AMBIGUOUS - confidence 0.97, jev-1.13.0
clarify          asked which reading was meant
cost             1 call, 2,229 in / 78 out, 0 cache read / 0 written, $0.0052
```

**Did not answer:** By 'how we're doing' do you mean sales (revenue or order volume), delivery performance (share of late orders), or customer satisfaction (average review score), and over what window: the last month, the last quarter, or the whole period?

### 17. What is the average review score for each customer state?

*grounding: many rows, and the answer must not miscount them — PASS, 5.0s*

```
classify         ANSWERABLE - confidence 0.98, jev-1.13.0
generate_sql     228 characters - Averaged over all review rows (so orders with multiple reviews count once per review, and orders without reviews are excluded), with state taken from the customer record on the order.
validate         passed
run_query        27 rows
generate_answer  482 characters
cost             2 calls, 5,580 in / 338 out, 0 cache read / 0 written, $0.0145
```

```sql
SELECT c.customer_state, AVG(r.review_score) AS avg_review_score FROM order_reviews r JOIN orders o ON r.order_id = o.order_id JOIN customers c ON o.customer_id = c.customer_id GROUP BY c.customer_state ORDER BY c.customer_state
```

**Answer:** Average review scores run from 3.61 in RR (the lowest) to 4.19 in AP (the highest), with most states between about 3.8 and 4.2. Larger states like SP (4.17), MG (4.14) and RJ (3.87) fall within that range. The result has 27 rows, 2 columns (customer_state, avg_review_score), complete.

The averages are taken over all review rows, so orders with multiple reviews count once per review and orders without reviews are excluded, with state taken from the customer record on the order.

### 18. What is the average freight value per order?

*answerable only if the classifier knows the columns — PASS, 5.1s*

```
classify         ANSWERABLE - confidence 0.91, jev-1.13.0
generate_sql     147 characters - Averaged over orders that have at least one item (775 orders with no items are excluded), summing freight across each order's item lines first.
validate         passed
run_query        1 rows
generate_answer  208 characters
cost             2 calls, 5,144 in / 180 out, 0 cache read / 0 written, $0.0121
```

```sql
SELECT AVG(order_freight) AS avg_freight_per_order FROM (SELECT order_id, SUM(freight_value) AS order_freight FROM order_items GROUP BY order_id) t
```

**Answer:** The average freight value per order is about 22.82. This sums freight across each order's item lines first, then averages over orders that have at least one item, so the 775 orders with no items are excluded.

### 19. and in 2018?

*follow-up: the previous question with one thing changed — PASS, 3.9s*

```
classify         ANSWERABLE - confidence 1.00, jev-1.13.0
generate_sql     153 characters - an order is placed in a year by its purchase timestamp, counting all orders regardless of status
validate         passed
run_query        1 rows
generate_answer  134 characters
cost             2 calls, 5,273 in / 169 out, 0 cache read / 0 written, $0.0122
```

```sql
SELECT COUNT(*) AS order_count FROM orders WHERE order_purchase_timestamp >= TIMESTAMP '2018-01-01' AND order_purchase_timestamp < TIMESTAMP '2019-01-01'
```

**Answer:** There were 54,011 orders in 2018, counting an order in a year by its purchase timestamp and including all orders regardless of status.

### 20. by revenue

*the clarification loop closing: an answered clarification is a turn — PASS, 4.6s*

```
classify         ANSWERABLE - confidence 0.88, jev-1.13.0
generate_sql     223 characters - Top 10 products ranked by total item price (excluding freight), across all orders regardless of order status
validate         passed
run_query        10 rows
generate_answer  446 characters
cost             2 calls, 5,660 in / 339 out, 0 cache read / 0 written, $0.0147
```

```sql
SELECT oi.product_id, p.product_category_name, SUM(oi.price) AS revenue FROM order_items oi LEFT JOIN products p ON p.product_id = oi.product_id GROUP BY oi.product_id, p.product_category_name ORDER BY revenue DESC LIMIT 10
```

**Answer:** The top product by revenue is bb50f2e236e5eea0100680137654686c (beleza_saude) at 63,885.0, followed by 6cdd53843498f92890544667809f1595 (beleza_saude) at 54,730.2 and d6160fb7873f184099d9bc95e30376af (pcs) at 48,899.34. The result is 10 rows, 3 columns (product_id, product_category_name, revenue), complete, and the 10th product reaches 37,608.9. Revenue here is total item price, excluding freight, across all orders regardless of order status.

### 21. and for late deliveries?

*a follow-up with nothing to follow: must ask, not guess — PASS, 3.7s*

```
classify         AMBIGUOUS - confidence 0.80, jev-1.13.0
clarify          asked which reading was meant
cost             1 call, 2,229 in / 60 out, 0 cache read / 0 written, $0.0051
```

**Did not answer:** By late deliveries, do you mean the count of orders delivered after their estimated date, the share of delivered orders that were late, or how many days late those orders arrived on average?

