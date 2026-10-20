# Evaluation results — iteration 1 (baseline)

**Archived.** This is the recorded iteration 1 run, kept as the baseline that
iteration 2 is measured against. The current run is in `eval_results.md`.

Produced by `uv run --env-file .env python -m twyd.eval`. The question set and the grading rules are in `eval_questions.md`.

**8/8 graded questions correct.** Total time 43.7s.

| # | Question | Covers | Verdict | Expected | Got | Stage | Notes |
|---|---|---|---|---|---|---|---|
| 1 | How many orders are there? | count | **PASS** | 99,441 | 99,441 | ok |  |
| 2 | How many customers does the company have? | identity trap | **PASS** | 96,096 | 96,096 | ok |  |
| 3 | How many orders were placed in 2017? | time filter | **PASS** | 45,101 | 45,101 | ok |  |
| 4 | What is the average review score? | aggregation | **PASS** | 4.0864 | 4.0864 | ok |  |
| 5 | What is the average review score for orders delivered later than estimated? | join inflation trap | **PASS** | 2.5666 | 2.5666 | ok |  |
| 6 | How many orders have no reviews at all? | anti-join trap | **PASS** | 768 | 768 | ok |  |
| 7 | Which customer state has the most orders? | join + group by + ordering | **PASS** | SP | SP | ok |  |
| 8 | What is the average number of items per order? | child-table grain | **PASS** | 1.1417 | 1.1417 | ok |  |
| 9 | What are the best products? | ambiguous | **RECORD** | — | bb50f2e236e5eea0100680137654686c | ok |  |
| 10 | What will sales be next month? | unsupported | **RECORD** | — | 2018-10-01 00:00:00 | ok |  |
| 11 | asdfgh | malformed | **RECORD** | — | Your question looks like random keystrokes. Please ask a question about the Olist data, e.g. number of orders, revenue, or reviews. | ok |  |
| 12 | How many orders were placed in 2030? | empty result is correct | **RECORD** | — | 0 | ok |  |

## Generated SQL and answers

### 1. How many orders are there?

*count — PASS, 3.1s*

```sql
SELECT COUNT(*) AS order_count FROM orders
```

**Answer:** There are 99,441 orders in total.

### 2. How many customers does the company have?

*identity trap — PASS, 3.8s*

```sql
SELECT COUNT(DISTINCT customer_unique_id) AS customer_count FROM customers
```

**Answer:** The company has 96,096 customers, counted as distinct customer IDs in the customers table.

### 3. How many orders were placed in 2017?

*time filter — PASS, 2.8s*

```sql
SELECT COUNT(*) AS order_count FROM orders WHERE order_purchase_timestamp >= TIMESTAMP '2017-01-01' AND order_purchase_timestamp < TIMESTAMP '2018-01-01'
```

**Answer:** There were 45,101 orders placed in 2017, counting every order with a purchase timestamp from January 1, 2017 through December 31, 2017.

### 4. What is the average review score?

*aggregation — PASS, 3.1s*

```sql
SELECT AVG(review_score) AS avg_review_score FROM order_reviews
```

**Answer:** The average review score is about 4.09. This is calculated across all rows in the order_reviews table, with no filters applied.

### 5. What is the average review score for orders delivered later than estimated?

*join inflation trap — PASS, 2.9s*

```sql
WITH late AS (SELECT order_id FROM orders WHERE order_delivered_customer_date > order_estimated_delivery_date), r AS (SELECT order_id, AVG(review_score) AS order_score FROM order_reviews GROUP BY order_id) SELECT AVG(r.order_score) AS avg_review_score FROM late JOIN r ON r.order_id = late.order_id
```

**Answer:** Orders delivered later than estimated have an average review score of about 2.57. Orders without a review aren't reflected in this figure, and for orders with more than one review, each order's reviews were averaged first so every order counts once.

### 6. How many orders have no reviews at all?

*anti-join trap — PASS, 3.0s*

```sql
SELECT COUNT(*) AS orders_without_reviews FROM orders o WHERE NOT EXISTS (SELECT 1 FROM order_reviews r WHERE r.order_id = o.order_id)
```

**Answer:** 768 orders have no reviews at all. That count covers every order in the orders table that has no matching row in the order reviews table.

### 7. Which customer state has the most orders?

*join + group by + ordering — PASS, 3.8s*

```sql
SELECT c.customer_state, COUNT(DISTINCT o.order_id) AS order_count FROM orders o JOIN customers c ON o.customer_id = c.customer_id GROUP BY c.customer_state ORDER BY order_count DESC LIMIT 1
```

**Answer:** SP has the most orders of any customer state, with 41,746 orders. The query counted distinct order IDs per state and returned only the top state, so it doesn't show how other states compare.

### 8. What is the average number of items per order?

*child-table grain — PASS, 3.1s*

```sql
SELECT COUNT(*) * 1.0 / COUNT(DISTINCT order_id) AS avg_items_per_order FROM order_items
```

**Answer:** Orders contain an average of about 1.14 items each. This is calculated as the total number of rows in order_items divided by the number of distinct orders. It counts each row as one item, so if rows have a quantity greater than 1, the figure would understate the actual units per order.

### 9. What are the best products?

*ambiguous — RECORD, 6.2s*

```sql
WITH rev AS (SELECT oi.product_id, SUM(oi.price) AS total_revenue, COUNT(*) AS units_sold, COUNT(DISTINCT oi.order_id) AS orders FROM order_items oi GROUP BY oi.product_id), rs AS (SELECT oi.product_id, AVG(r.review_score) AS avg_review_score, COUNT(r.review_score) AS review_count FROM (SELECT DISTINCT order_id, product_id FROM order_items) oi JOIN order_reviews r ON r.order_id = oi.order_id GROUP BY oi.product_id) SELECT rev.product_id, COALESCE(t.product_category_name_english, p.product_category_name) AS category, rev.total_revenue, rev.units_sold, rev.orders, rs.avg_review_score, rs.review_count FROM rev JOIN products p ON p.product_id = rev.product_id LEFT JOIN product_category_translation t ON t.product_category_name = p.product_category_name LEFT JOIN rs ON rs.product_id = rev.product_id ORDER BY rev.total_revenue DESC LIMIT 20
```

**Answer:** Ranked by total revenue, the top product is a health_beauty item (bb50f2e2…) with 63,885.00 from 195 units, followed by another health_beauty product at 54,730.20 and a computers product at 48,899.34. The list shows 19 products, not the 20 the query asked for, so this covers only the rows returned. Some high earners sell far fewer units than others (the computers product sold 35 units, while a bed_bath_table product sold 488 for 43,025.56), and review scores vary, from 2.68 for a baby product to 4.57 for the computers one.

"Best" was interpreted as highest revenue, so products that rank well on reviews or units sold may not appear here.

### 10. What will sales be next month?

*unsupported — RECORD, 5.0s*

```sql
WITH monthly AS (SELECT date_trunc('month', o.order_purchase_timestamp) AS month, SUM(oi.price) AS sales FROM orders o JOIN order_items oi ON oi.order_id = o.order_id WHERE o.order_status <> 'canceled' GROUP BY 1), ranked AS (SELECT month, sales, ROW_NUMBER() OVER (ORDER BY month DESC) AS rn FROM monthly WHERE month < (SELECT max(month) FROM monthly)) SELECT (SELECT max(month) FROM monthly) + INTERVAL 1 MONTH AS forecast_month, AVG(sales) AS forecast_sales_avg_last_3_full_months FROM ranked WHERE rn <= 3
```

**Answer:** The query doesn't predict next month's sales so much as give a simple estimate: for October 2018, it averages the last three full months of sales, which comes to about 863,390. This is a trailing average, so it ignores trends and seasonality. It also leaves out canceled orders and the latest, partial month. Summing item prices per order could also overstate sales if an order's items were counted more than once, though the join here is one row per item, so that is unlikely.

### 11. asdfgh

*malformed — RECORD, 3.5s*

```sql
SELECT 'Your question looks like random keystrokes. Please ask a question about the Olist data, e.g. number of orders, revenue, or reviews.' AS message
```

**Answer:** The query didn't look up any data. It only returned a message saying your question looked like random keystrokes. To get an answer, try asking something about the Olist data, such as the number of orders, total revenue, or review scores.

### 12. How many orders were placed in 2030?

*empty result is correct — RECORD, 3.4s*

```sql
SELECT COUNT(*) AS order_count FROM orders WHERE order_purchase_timestamp >= TIMESTAMP '2030-01-01' AND order_purchase_timestamp < TIMESTAMP '2031-01-01'
```

**Answer:** No orders were placed in 2030: the count came back as 0. The query only counted orders with a purchase timestamp from January 1, 2030 through December 31, 2030.

