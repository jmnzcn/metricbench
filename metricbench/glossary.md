# Metric glossary — Olist marketplace

These are the official definitions. Answer with them, not with your own.

## Data
- Brazilian e-commerce marketplace, orders from September 2016 to October 2018. Amounts are in Brazilian reais (BRL).
- Every metric is dated by the order's purchase timestamp. "In 2018" means purchased in 2018.
- A **valid order** is any order whose status is not `canceled` or `unavailable`.

## Orders
- **Orders placed**: every order, in any status.
- **Orders**: valid orders.
- **Canceled orders**: orders with status `canceled` (`unavailable` does not count as canceled).
- **Cancellation rate**: canceled orders ÷ orders placed.

## Sales
- **GMV** (gross merchandise value): sum of item prices of valid orders. Excludes freight.
- **Freight**: sum of freight charged on the items of valid orders.
- **Items sold**: number of items in valid orders. An order can have several items.
- **Average order value**: GMV ÷ orders.
- **Payments received**: what customers paid for valid orders, including freight, vouchers and installment interest. An order can be paid in several parts with different methods.
- **Average installments**: average number of installments per payment, for payments of valid orders.

## Customers
- A customer is a person, identified by `customer_unique_id`. Olist creates a new `customer_id` for every order, so `customer_id` cannot count customers.
- **Active customers**: distinct customers with at least one valid order in the period.
- **New customers**: customers whose first valid order falls in the period. All-time new customers is the total number of customers.
- **Repeat customers**: customers with two or more valid orders ever, counted in the period of their first valid order.
- **Repeat customer rate**: repeat customers ÷ new customers.

## Delivery
- **Delivered orders**: orders with status `delivered` and a delivery date.
- **Late delivery**: a delivered order that arrived on a later calendar day than its estimated delivery date.
- **Late delivery rate**: late deliveries ÷ delivered orders.
- **Average delivery days**: average time from purchase to delivery, in days (with fractions), over delivered orders.

## Reviews
- **Average review score**: average score (1 to 5) over orders that have a review. When an order has several reviews, only the most recently answered one counts.

## Products and sellers
- **Product category**: the English name from the translation table; the Portuguese name when there is no translation; `unknown` when the product has no category.
- **Customer state** is the state of the customer on that order; **seller state** is the state of the seller of the item.
