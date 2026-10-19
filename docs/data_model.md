# Olist data model

Every number below was measured against the CSVs in `data/` with the query
layer in `src/twyd/db.py`, not taken from the dataset's documentation. The row
counts are asserted in `tests/test_real_data.py`, so a changed dataset fails a
test rather than silently invalidating this document.

Measured 2026-10-19. Purchase timestamps span **2016-09-04 → 2018-10-17**.

## Tables

| Table | Rows | Grain (one row per…) |
|---|---:|---|
| `orders` | 99,441 | order. `order_id` is unique |
| `customers` | 99,441 | **order**, not person (see below) |
| `order_reviews` | 99,224 | review. `review_id` is *not* unique |
| `order_items` | 112,650 | item line within an order |
| `order_payments` | 103,886 | payment instalment line within an order |
| `products` | 32,951 | product. `product_id` unique |
| `sellers` | 3,095 | seller. `seller_id` unique |
| `geolocation` | 1,000,163 | lat/lng sample, **not** zip code |
| `product_category_translation` | 71 | category |

## Keys and referential integrity

- `orders.customer_id` → `customers.customer_id`: **0 orphans**.
- `order_items.product_id` → `products.product_id`: **0 orphans**.
- `order_items.seller_id` → `sellers.seller_id`: clean.
- 768 orders have **no review**, 775 have **no items**, 1 has **no payment**.
  Inner joins therefore drop orders; use `LEFT JOIN` when the question is about
  orders rather than about reviews.

## The traps, with numbers

### 1. `customers` is per order, not per person

```
count(*)                        = 99,441
count(DISTINCT customer_id)     = 99,441   -- one row per order
count(DISTINCT customer_unique_id) = 96,096   -- actual people
```

2,997 people ordered more than once; the most frequent placed **17** orders.
"How many customers" means `count(DISTINCT customer_unique_id)`. Using
`customer_id` overstates the answer by about 3.5%.

### 2. `review_id` is not a primary key

```
rows                      = 99,224
distinct review_id        = 98,410
distinct order_id         = 98,673
```

789 `review_id` values appear more than once, and those duplicates point at
**different** `order_id`s — there are no fully identical rows. 547 orders carry
more than one review. Neither column is a usable key on its own.

### 3. Joins multiply rows and distort averages

Up to **21** items and **29** payment instalments on a single order. Joining
`orders → order_items → order_payments` turns 4 fixture orders into 7 rows; on
the real data the distortion is measurable:

```
average review score, customers in SP
  naive join through order_items   = 4.1269
  aggregated per order first       = 4.1741
```

The naive figure is not a rounding difference — it weights each order by its
item count. Aggregate before joining.

### 4. Delivery timestamps are null for undelivered orders

```
null order_approved_at             =   160
null order_delivered_carrier_date  = 1,783
null order_delivered_customer_date = 2,965
null order_estimated_delivery_date =     0
```

Late means `order_delivered_customer_date > order_estimated_delivery_date`:
**7,827 late**, 88,649 on time, 2,965 undecidable. A late *rate* must state
its denominator — 7,827/96,476 delivered is 8.1%, 7,827/99,441 of all orders is
7.9%.

**Caveat:** 8 orders have `order_status = 'delivered'` but a null delivery
date. Status and timestamps disagree; trust the timestamp.

### 5. Review text is Portuguese and mostly absent

```
null review_comment_title   = 87,656  (88%)
null review_comment_message = 58,247  (59%)
```

Only ~41% of reviews have text. Scores skew positive: 57,328 fives against
11,424 ones. Relevant to iteration 3's semantic search — the corpus is
~41k documents, not 99k.

### 6. `geolocation` is not a zip lookup table

1,000,163 rows for 19,015 distinct zip prefixes — roughly 53 rows per prefix.
Joining it to `customers` on the zip prefix multiplies rows ~53×. It needs
aggregating (`avg(lat)`, `avg(lng)`) before any join.

### 7. Two product categories have no English translation

`pc_gamer` and `portateis_cozinha_e_preparadores_de_alimentos`. 610 products
have a null category. Inner-joining the translation table silently drops them.

## Status values

`delivered` 96,478 · `shipped` 1,107 · `canceled` 625 · `unavailable` 609 ·
`invoiced` 314 · `processing` 301 · `created` 5 · `approved` 2

## Payment types

`credit_card` 76,795 · `boleto` 19,784 · `voucher` 5,775 · `debit_card` 1,529 ·
`not_defined` 3
