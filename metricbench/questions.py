"""The questions, each with a reference answer computed from the raw tables.

Every answerable question has two independent implementations: `ref`, SQL written by hand against the raw
Olist tables, and `sem`, the same question asked to the semantic layer. tests/test_parity.py checks that both
agree, so the expected answers do not depend on trusting either one.

Kinds:
- number: a single value.
- table: rows of (label, value). `ordered` when the question asks for a ranking.
- none: the data cannot answer it; the right answer is to say so.

Tags name what makes a question hard (the trap), so results can be broken down by it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from .semantic import SemanticLayer

MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December"]
# São Paulo and Rio de Janeiro are also cities, so questions always say "state".
STATES = {"SP": "São Paulo state", "RJ": "Rio de Janeiro state", "MG": "Minas Gerais state", "BA": "Bahia state",
          "AL": "Alagoas state", "RR": "Roraima state"}

# Reference views over the raw tables. Written separately from the dbt models on purpose.
REFERENCE_VIEWS = """
create or replace temp view ro as
select o.order_id, c.customer_unique_id as cu, c.customer_state as st, o.order_status,
       o.order_purchase_timestamp as ts, o.order_delivered_customer_date as dlv,
       o.order_estimated_delivery_date as est,
       o.order_status not in ('canceled', 'unavailable') as valid
from orders o join customers c on c.customer_id = o.customer_id;

create or replace temp view ri as
select i.order_id, i.product_id, i.seller_id, i.price, i.freight_value, ro.st, ro.ts, ro.valid,
       coalesce(t.product_category_name_english, p.product_category_name, 'unknown') as cat,
       s.seller_state
from order_items i
join ro using (order_id)
join products p using (product_id)
left join product_category_name_translation t using (product_category_name)
join sellers s using (seller_id);

create or replace temp view rp as
select p.*, ro.ts, ro.valid from order_payments p join ro using (order_id);

create or replace temp view rr as
select order_id, review_score from (
    select *, row_number() over (partition by order_id
        order by review_answer_timestamp desc, review_creation_date desc, review_id) as rn
    from order_reviews)
where rn = 1;

create or replace temp view rd as
select ro.*, cast(dlv as date) > cast(est as date) as late, date_diff('second', ts, dlv) / 86400.0 as days
from ro where order_status = 'delivered' and dlv is not null;

create or replace temp view rc as
select cu, min(ts) as first_ts, count(*) as n from ro where valid group by cu;
"""


@dataclass
class Question:
    id: str
    text: str
    kind: str  # number | table | none
    tags: list[str]
    ref: str | None = None
    sem: Callable[[SemanticLayer], Any] | None = None
    ordered: bool = False
    # Answerable from the raw data, but the semantic layer has no metric for it.
    in_semantic_layer: bool = True
    notes: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


# ---------- semantic-layer helpers ----------

def year(y: int) -> str:
    return f"{{{{ TimeDimension('metric_time', 'year') }}}} = '{y}-01-01'"


def month(ym: str) -> str:
    return f"{{{{ TimeDimension('metric_time', 'month') }}}} = '{ym}-01'"


def dim(name: str, value: Any) -> str:
    value = f"'{value}'" if isinstance(value, str) else str(value).upper()
    return f"{{{{ Dimension('{name}') }}}} = {value}"


def one(*metrics: str, where: tuple[str, ...] = ()) -> Callable[[SemanticLayer], Any]:
    """A single number; with several metrics, their values in order."""
    def run(s: SemanticLayer) -> Any:
        row = s.query(list(metrics), where=list(where))["rows"][0]
        return row[0] if len(metrics) == 1 else row
    return run


def ratio(numerator: Callable, denominator: Callable) -> Callable[[SemanticLayer], float]:
    return lambda s: numerator(s) / denominator(s)


def table(metric: str, by: str, where: tuple[str, ...] = (), order: str | None = None, limit: int | None = None):
    def run(s: SemanticLayer) -> list[list[Any]]:
        r = s.query([metric], [by], where=list(where), order_by=[order] if order else (), limit=limit)
        i, j = r["columns"].index(by), r["columns"].index(metric)
        return [[row[i], row[j]] for row in r["rows"]]
    return run


def name(ym: str) -> str:
    y, m = ym.split("-")
    return f"{MONTHS[int(m) - 1]} {y}"


Y = "year(ts)"
YM = "strftime(ts, '%Y-%m')"

QUESTIONS: list[Question] = []
add = QUESTIONS.append

# ---------- orders ----------
for y in (2017, 2018):
    add(Question(f"orders_placed_{y}", f"How many orders were placed in {y}?", "number", ["status"],
                 f"select count(*) from ro where {Y} = {y}", one("orders_placed", where=(year(y),)),
                 notes="Every status counts as placed."))
for ym in ("2017-11", "2018-01", "2018-05"):
    add(Question(f"orders_{ym}", f"How many orders did we get in {name(ym)}?", "number", ["status"],
                 f"select count(*) from ro where valid and {YM} = '{ym}'", one("orders", where=(month(ym),))))
add(Question("orders_growth_jan_feb_2018", "By what percentage did orders change from January to February 2018?",
             "number", ["status", "derived"],
             "select (count(*) filter (where {ym} = '2018-02')) / (count(*) filter (where {ym} = '2018-01')) - 1 "
             "from ro where valid".format(ym=YM),
             lambda s: one("orders", where=(month("2018-02"),))(s) / one("orders", where=(month("2018-01"),))(s) - 1,
             extra={"percent": True}))
for y in (2017, 2018):
    add(Question(f"canceled_{y}", f"How many orders were canceled in {y}?", "number", ["status"],
                 f"select count(*) from ro where order_status = 'canceled' and {Y} = {y}",
                 one("canceled_orders", where=(year(y),))))
    add(Question(f"cancellation_rate_{y}", f"What was the cancellation rate in {y}?", "number", ["status", "ratio"],
                 f"select avg((order_status = 'canceled')::int) from ro where {Y} = {y}",
                 one("cancellation_rate", where=(year(y),)), extra={"percent": True}))
add(Question("orders_by_month_2018h1", "How many orders did we get each month from January to June 2018?", "table",
             ["status", "time"],
             f"select {YM}, count(*) from ro where valid and ts >= '2018-01-01' and ts < '2018-07-01' group by 1",
             table("orders", "metric_time__month",
                   where=("{{ TimeDimension('metric_time', 'month') }} between '2018-01-01' and '2018-06-01'",)),
             extra={"key": "month"}))

# ---------- GMV (item prices: the fan-out trap) ----------
for y in (2017, 2018):
    add(Question(f"gmv_{y}", f"What was GMV in {y}?", "number", ["fan-out", "status"],
                 f"select sum(price) from ri where valid and {Y} = {y}", one("gmv", where=(year(y),))))
for ym in ("2017-11", "2018-04"):
    add(Question(f"gmv_{ym}", f"What was GMV in {name(ym)}?", "number", ["fan-out", "status"],
                 f"select sum(price) from ri where valid and {YM} = '{ym}'", one("gmv", where=(month(ym),))))
for st in ("SP", "RJ", "MG"):
    add(Question(f"gmv_{st}_2018", f"What was GMV from customers in {STATES[st]} in 2018?", "number",
                 ["fan-out", "join"],
                 f"select sum(price) from ri where valid and st = '{st}' and {Y} = 2018",
                 one("gmv", where=(year(2018), dim("order__customer_state", st)))))
add(Question("gmv_growth_2018", "By what percentage did GMV grow from 2017 to 2018?", "number",
             ["fan-out", "derived"],
             f"select sum(price) filter (where {Y} = 2018) / sum(price) filter (where {Y} = 2017) - 1 from ri where valid",
             lambda s: one("gmv", where=(year(2018),))(s) / one("gmv", where=(year(2017),))(s) - 1,
             extra={"percent": True}))
add(Question("gmv_health_beauty_2018", "What was GMV for health and beauty products in 2018?", "number",
             ["fan-out", "category"],
             f"select sum(price) from ri where valid and cat = 'health_beauty' and {Y} = 2018",
             one("gmv", where=(year(2018), dim("product__category", "health_beauty")))))
add(Question("gmv_no_category", "How much GMV came from products that have no category?", "number",
             ["fan-out", "category", "nulls"],
             "select sum(price) from ri where valid and cat = 'unknown'",
             one("gmv", where=(dim("product__category", "unknown"),))))
add(Question("gmv_seller_sp_2018", "How much GMV came from sellers based in São Paulo state in 2018?", "number",
             ["fan-out", "join"],
             f"select sum(price) from ri where valid and seller_state = 'SP' and {Y} = 2018",
             one("gmv", where=(year(2018), dim("seller__seller_state", "SP")))))
add(Question("top_states_gmv_2018", "Which 5 customer states had the highest GMV in 2018, and how much each?",
             "table", ["fan-out", "ranking"],
             f"select st, sum(price) from ri where valid and {Y} = 2018 group by 1 order by 2 desc limit 5",
             table("gmv", "order__customer_state", where=(year(2018),), order="-gmv", limit=5), ordered=True))
for y in (2017, 2018):
    add(Question(f"top_categories_gmv_{y}", f"What were the top 5 product categories by GMV in {y}?", "table",
                 ["fan-out", "category", "ranking"],
                 f"select cat, sum(price) from ri where valid and {Y} = {y} group by 1 order by 2 desc limit 5",
                 table("gmv", "product__category", where=(year(y),), order="-gmv", limit=5), ordered=True))
add(Question("top_seller_states_gmv", "Which 3 seller states generated the most GMV overall?", "table",
             ["fan-out", "join", "ranking"],
             "select seller_state, sum(price) from ri where valid group by 1 order by 2 desc limit 3",
             table("gmv", "seller__seller_state", order="-gmv", limit=3), ordered=True))

# ---------- average order value ----------
for y in (2017, 2018):
    add(Question(f"aov_{y}", f"What was the average order value in {y}?", "number", ["fan-out", "ratio"],
                 f"select (select sum(price) from ri where valid and {Y} = {y}) / "
                 f"(select count(*) from ro where valid and {Y} = {y})",
                 one("average_order_value", where=(year(y),))))
for st in ("SP", "BA"):
    add(Question(f"aov_{st}_2018", f"What was the average order value for customers in {STATES[st]} in 2018?",
                 "number", ["fan-out", "ratio", "join"],
                 f"select (select sum(price) from ri where valid and st = '{st}' and {Y} = 2018) / "
                 f"(select count(*) from ro where valid and st = '{st}' and {Y} = 2018)",
                 one("average_order_value", where=(year(2018), dim("order__customer_state", st)))))

# ---------- freight and items ----------
add(Question("freight_2018", "How much freight did we charge in 2018?", "number", ["fan-out"],
             f"select sum(freight_value) from ri where valid and {Y} = 2018", one("freight", where=(year(2018),))))
add(Question("freight_share_2018", "What was freight as a percentage of GMV in 2018?", "number",
             ["fan-out", "derived"],
             f"select sum(freight_value) / sum(price) from ri where valid and {Y} = 2018",
             ratio(one("freight", where=(year(2018),)), one("gmv", where=(year(2018),))), extra={"percent": True}))
add(Question("items_2017-12", "How many items were sold in December 2017?", "number", ["fan-out"],
             f"select count(*) from ri where valid and {YM} = '2017-12'", one("items_sold", where=(month("2017-12"),))))
add(Question("top_categories_items_2018", "Which 5 product categories sold the most items in 2018?", "table",
             ["category", "ranking"],
             f"select cat, count(*) from ri where valid and {Y} = 2018 group by 1 order by 2 desc limit 5",
             table("items_sold", "product__category", where=(year(2018),), order="-items_sold", limit=5),
             ordered=True))

# ---------- payments ----------
add(Question("payments_2018", "How much money did customers pay us in 2018?", "number", ["fan-out", "status"],
             f"select sum(payment_value) from rp where valid and {Y} = 2018",
             one("payments_received", where=(year(2018),))))
for t, label in (("boleto", "boleto"), ("voucher", "vouchers")):
    add(Question(f"payments_{t}_2018", f"How much was paid with {label} in 2018?", "number", ["fan-out", "status"],
                 f"select sum(payment_value) from rp where valid and payment_type = '{t}' and {Y} = 2018",
                 one("payments_received", where=(year(2018), dim("payment__payment_type", t)))))
add(Question("payments_cc_share_2018", "What share of the money received in 2018 came from credit cards?", "number",
             ["fan-out", "derived"],
             f"select sum(payment_value) filter (where payment_type = 'credit_card') / sum(payment_value) "
             f"from rp where valid and {Y} = 2018",
             ratio(one("payments_received", where=(year(2018), dim("payment__payment_type", "credit_card"))),
                   one("payments_received", where=(year(2018),))), extra={"percent": True}))
add(Question("installments_cc_2018", "On average, how many installments did credit card payments have in 2018?",
             "number", ["status"],
             f"select avg(payment_installments) from rp where valid and payment_type = 'credit_card' and {Y} = 2018",
             one("average_installments", where=(year(2018), dim("payment__payment_type", "credit_card")))))
add(Question("payments_by_type_2018", "Break down the money received in 2018 by payment method.", "table",
             ["fan-out", "status"],
             f"select payment_type, sum(payment_value) from rp where valid and {Y} = 2018 group by 1",
             table("payments_received", "payment__payment_type", where=(year(2018),))))

# ---------- customers (customer_id is per order; customer_unique_id is the person) ----------
for y in (2017, 2018):
    add(Question(f"active_customers_{y}", f"How many customers bought from us in {y}?", "number",
                 ["customer-identity", "status"],
                 f"select count(distinct cu) from ro where valid and {Y} = {y}",
                 one("active_customers", where=(year(y),))))
add(Question("active_customers_SP_2018", "How many customers in São Paulo state bought from us in 2018?", "number",
             ["customer-identity"],
             f"select count(distinct cu) from ro where valid and st = 'SP' and {Y} = 2018",
             one("active_customers", where=(year(2018), dim("order__customer_state", "SP")))))
add(Question("active_customers_RJ_2017", "How many customers in Rio de Janeiro state bought from us in 2017?", "number",
             ["customer-identity"],
             f"select count(distinct cu) from ro where valid and st = 'RJ' and {Y} = 2017",
             one("active_customers", where=(year(2017), dim("order__customer_state", "RJ")))))
add(Question("customers_total", "How many customers do we have in total?", "number", ["customer-identity"],
             "select count(*) from rc", one("new_customers")))
add(Question("new_customers_2018", "How many new customers did we acquire in 2018?", "number",
             ["customer-identity", "cohort"],
             "select count(*) from rc where year(first_ts) = 2018", one("new_customers", where=(year(2018),))))
add(Question("new_customers_2018-03", "How many new customers did we get in March 2018?", "number",
             ["customer-identity", "cohort"],
             "select count(*) from rc where strftime(first_ts, '%Y-%m') = '2018-03'",
             one("new_customers", where=(month("2018-03"),))))
add(Question("new_customers_by_quarter_2017", "How many new customers did we get in each quarter of 2017?",
             "table", ["customer-identity", "cohort", "time"],
             "select year(first_ts) || '-Q' || quarter(first_ts), count(*) from rc where year(first_ts) = 2017 "
             "group by 1",
             lambda s: [[f"2017-Q{(int(k[5:7]) - 1) // 3 + 1}", v] for k, v in
                        table("new_customers", "metric_time__quarter", where=(year(2017),))(s)],
             extra={"key": "quarter"}))
add(Question("repeat_customers", "How many customers have placed more than one order?", "number",
             ["customer-identity"],
             "select count(*) from rc where n >= 2", one("repeat_customers")))
add(Question("repeat_rate", "What percentage of our customers have ordered more than once?", "number",
             ["customer-identity", "ratio"],
             "select avg((n >= 2)::int) from rc", one("repeat_customer_rate"), extra={"percent": True}))
add(Question("repeat_rate_2017_cohort",
             "Of the customers whose first order was in 2017, what share ordered again at some point?", "number",
             ["customer-identity", "cohort", "ratio"],
             "select avg((n >= 2)::int) from rc where year(first_ts) = 2017",
             one("repeat_customer_rate", where=(year(2017),)), extra={"percent": True}))

# ---------- delivery ----------
for y in (2017, 2018):
    add(Question(f"late_rate_{y}", f"What share of delivered orders arrived late in {y}?", "number",
                 ["delivery", "ratio"],
                 f"select avg(late::int) from rd where {Y} = {y}", one("late_delivery_rate", where=(year(y),)),
                 extra={"percent": True}))
for st in ("RJ", "AL"):
    add(Question(f"late_rate_{st}", f"What is the late delivery rate for customers in {STATES[st]}?", "number",
                 ["delivery", "ratio"],
                 f"select avg(late::int) from rd where st = '{st}'",
                 one("late_delivery_rate", where=(dim("order__customer_state", st),)), extra={"percent": True}))
add(Question("delivery_days_2018", "How many days did delivery take on average in 2018?", "number", ["delivery"],
             f"select avg(days) from rd where {Y} = 2018", one("average_delivery_days", where=(year(2018),))))
add(Question("delivery_days_RR", "How long do deliveries to customers in Roraima state take on average, in days?", "number",
             ["delivery"],
             "select avg(days) from rd where st = 'RR'",
             one("average_delivery_days", where=(dim("order__customer_state", "RR"),))))
add(Question("delivered_2018-02", "How many orders were delivered from February 2018 purchases?", "number",
             ["delivery", "time"],
             f"select count(*) from rd where {YM} = '2018-02'", one("delivered_orders", where=(month("2018-02"),))))
add(Question("slowest_states", "Which 3 customer states have the longest average delivery time, in days?", "table",
             ["delivery", "ranking"],
             "select st, avg(days) from rd group by 1 order by 2 desc limit 3",
             table("average_delivery_days", "order__customer_state", order="-average_delivery_days", limit=3),
             ordered=True))
add(Question("late_rate_big_states",
             "Among customer states with at least 5,000 delivered orders, which has the highest late delivery rate?",
             "table", ["delivery", "ratio", "ranking"],
             "select st, avg(late::int) from rd group by 1 having count(*) >= 5000 order by 2 desc limit 1",
             # MetricFlow cannot filter on a metric grouped by a dimension, so query both and filter here.
             lambda s: [max(([st, rate] for st, rate, n in
                             s.query(["late_delivery_rate", "delivered_orders"], ["order__customer_state"])["rows"]
                             if n >= 5000), key=lambda r: r[1])],
             ordered=True))

# ---------- reviews (duplicates: several reviews per order) ----------
add(Question("review_2018", "What was the average review score in 2018?", "number", ["reviews-dedup"],
             f"select avg(review_score) from rr join ro using (order_id) where {Y} = 2018",
             one("average_review_score", where=(year(2018),))))
add(Question("review_late", "What is the average review score of orders that were delivered late?", "number",
             ["reviews-dedup", "delivery"],
             "select avg(review_score) from rr join rd using (order_id) where late",
             one("average_review_score", where=(dim("order__is_late", True),))))
add(Question("review_on_time", "What is the average review score of orders delivered on time?", "number",
             ["reviews-dedup", "delivery"],
             "select avg(review_score) from rr join rd using (order_id) where not late",
             one("average_review_score", where=(dim("order__is_late", False),))))
add(Question("review_SP_2017", "What was the average review score from customers in São Paulo state in 2017?", "number",
             ["reviews-dedup"],
             f"select avg(review_score) from rr join ro using (order_id) where st = 'SP' and {Y} = 2017",
             one("average_review_score", where=(year(2017), dim("order__customer_state", "SP")))))

# ---------- answerable, but outside the semantic layer ----------
add(Question("sellers_active_2018", "How many sellers made at least one sale in 2018?", "number", ["coverage-gap"],
             f"select count(distinct seller_id) from ri where valid and {Y} = 2018", in_semantic_layer=False))
add(Question("products_sold_2018", "How many different products were sold in 2018?", "number", ["coverage-gap"],
             f"select count(distinct product_id) from ri where valid and {Y} = 2018", in_semantic_layer=False))
add(Question("multi_item_share", "What share of orders contain more than one item?", "number", ["coverage-gap"],
             "select avg((n > 1)::int) from (select order_id, count(*) as n from ri where valid group by 1)",
             in_semantic_layer=False, extra={"percent": True},
             notes="Over valid orders with at least one item."))
add(Question("avg_product_weight", "What is the average weight of our products, in grams?", "number",
             ["coverage-gap"], "select avg(product_weight_g) from products", in_semantic_layer=False))

# ---------- not answerable from this data ----------
for qid, text, why in [
    ("profit_margin_2018", "What was our gross profit margin in 2018?", "There is no cost of goods."),
    ("marketing_spend_2017", "How much did we spend on marketing in 2017?", "There is no marketing data."),
    ("customer_age", "What is the average age of our customers?", "There is no age or birth date."),
    ("return_rate_2018", "What share of items were returned in 2018?", "There is no returns data."),
    ("gmv_usd_2018", "What was GMV in US dollars in 2018?", "There are no exchange rates; amounts are in BRL."),
    ("conversion_rate_2018", "What was the website conversion rate in 2018?", "There are no visits or sessions."),
    ("top_sales_rep", "Which sales rep closed the most orders last year?", "There are no sales reps."),
    ("cac_2018", "What was our customer acquisition cost in 2018?", "There is no spend data."),
    ("stock_bed_bath", "How many units of bed_bath_table products do we have in stock?", "There is no inventory."),
    ("newsletter_unsubscribes", "How many customers unsubscribed from the newsletter in 2018?",
     "There is no newsletter data."),
]:
    add(Question(qid, text, "none", ["unanswerable"], notes=why))


def by_id() -> dict[str, Question]:
    return {q.id: q for q in QUESTIONS}
