"""Causes of wrong answers, on a small database with Olist's tables. No model and no download."""

import duckdb
import pytest

from metricbench import cli
from metricbench.causes import UNEXPLAINED, VARIANTS, answers, classify, combos, explain, variant_answers
from metricbench.questions import QUESTIONS, REFERENCE_VIEWS

SCHEMA = """
create table customers as select * from (values
    ('c1', 'ana', 'SP'), ('c2', 'ana', 'SP'), ('c3', 'beto', 'RJ'), ('c4', 'caro', 'SP'), ('c5', 'dani', 'RJ')
) t(customer_id, customer_unique_id, customer_state);
create table orders as select order_id, customer_id, order_status, ts::timestamp as order_purchase_timestamp,
    dlv::timestamp as order_delivered_customer_date, est::timestamp as order_estimated_delivery_date from (values
    ('o1', 'c1', 'delivered',   '2018-01-05 10:00', '2018-01-09 22:00', '2018-01-09 00:00'),
    ('o2', 'c2', 'delivered',   '2018-02-01 09:00', '2018-02-10 08:00', '2018-02-08 00:00'),
    ('o3', 'c3', 'canceled',    '2018-01-20 12:00', null,               '2018-02-01 00:00'),
    ('o4', 'c4', 'unavailable', '2018-01-22 12:00', null,               '2018-02-02 00:00'),
    ('o5', 'c5', 'shipped',     '2018-03-02 12:00', null,               '2018-03-20 00:00')
) t(order_id, customer_id, order_status, ts, dlv, est);
create table products as select * from (values ('p1', 'beleza_saude', 100), ('p2', null, 300), ('p3', 'sem_traducao', 50)
) t(product_id, product_category_name, product_weight_g);
create table product_category_name_translation as select * from (values ('beleza_saude', 'health_beauty')
) t(product_category_name, product_category_name_english);
create table sellers as select * from (values ('s1', 'SP'), ('s2', 'MG')) t(seller_id, seller_state);
create table order_items as select * from (values
    ('o1', 'p1', 's1', 100.0, 10.0), ('o1', 'p2', 's2', 50.0, 5.0), ('o2', 'p1', 's1', 80.0, 8.0),
    ('o3', 'p3', 's1', 40.0, 4.0), ('o4', 'p2', 's2', 30.0, 3.0), ('o5', 'p3', 's2', 20.0, 2.0)
) t(order_id, product_id, seller_id, price, freight_value);
create table order_payments as select * from (values
    ('o1', 'credit_card', 3, 120.0), ('o1', 'voucher', 1, 46.0), ('o2', 'boleto', 1, 88.0),
    ('o3', 'credit_card', 2, 44.0), ('o4', 'boleto', 1, 33.0), ('o5', 'credit_card', 1, 22.0)
) t(order_id, payment_type, payment_installments, payment_value);
create table order_reviews as select review_id, order_id, review_score, a::timestamp as review_answer_timestamp,
    c::timestamp as review_creation_date from (values
    ('r1', 'o1', 2, '2018-01-12', '2018-01-10'), ('r2', 'o1', 5, '2018-01-15', '2018-01-11'),
    ('r3', 'o2', 4, '2018-02-12', '2018-02-11')
) t(review_id, order_id, review_score, a, c);
"""


@pytest.fixture(scope="module")
def db(tmp_path_factory):
    path = tmp_path_factory.mktemp("olist") / "raw.duckdb"
    with duckdb.connect(str(path)) as con:
        con.execute(SCHEMA)
    return path


@pytest.fixture(scope="module")
def computed(db):
    return variant_answers(db)


def number(value):
    return {"answer_type": "number", "number": value, "table": [], "explanation": ""}


def gold_for(reference, qid):
    q = next(q for q in QUESTIONS if q.id == qid)
    return {"id": qid, "kind": q.kind, "answer": reference[qid], "ordered": q.ordered,
            "percent": q.extra.get("percent", False), "key": q.extra.get("key")}


def test_every_view_variant_still_finds_its_text():
    for v in VARIANTS:
        if v.views:
            assert v.views(REFERENCE_VIEWS) != REFERENCE_VIEWS, v.name


def test_every_query_variant_applies_to_some_question():
    for v in VARIANTS:
        if v.query:
            assert any(q.ref and v.query(q.ref) for q in QUESTIONS), v.name


def test_alternatives_are_not_combined():
    names = [tuple(v.name for v in c) for c in combos()]
    assert ("every order status",) in names
    assert ("every order status", "customer_id as the customer") in names
    assert ("every order status", "only delivered orders") not in names


def test_the_reference_runs_on_the_small_database(computed):
    reference, _ = computed
    assert reference["orders_placed_2018"] == 5
    assert reference["orders_2018-01"] == 1          # o1; o3 canceled and o4 unavailable are not valid
    assert reference["gmv_2018"] == 250.0            # o1 150 + o2 80 + o5 20
    assert reference["customers_total"] == 2         # ana (two orders) and dani


def test_variants_give_the_answer_of_the_wrong_reading(computed):
    _, by = computed
    assert by[("every order status",)]["gmv_2018"] == 320.0
    assert by[("only canceled excluded",)]["gmv_2018"] == 280.0
    assert by[("only delivered orders",)]["gmv_2018"] == 230.0
    assert by[("freight inside the price",)]["gmv_2018"] == 275.0
    assert by[("items joined to payments",)]["gmv_2018"] == 400.0   # o1's two items counted twice
    assert by[("payments as revenue",)]["gmv_2018"] == 276.0
    assert by[("customer_id as the customer",)]["customers_total"] == 3
    assert by[("unavailable as canceled",)]["canceled_2018"] == 2
    assert by[("every review",)]["review_2018"] == pytest.approx(11 / 3)
    assert by[("late by timestamp",)]["late_rate_2018"] == 1.0       # o1 arrived the same day, after the hour
    assert by[("every order status", "customer_id as the customer")]["customers_total"] == 5


def test_a_variant_that_changes_nothing_for_a_question_is_left_out(computed):
    _, by = computed
    assert "gmv_2018" not in by[("customer_id as the customer",)]
    assert "avg_product_weight" not in by[("every order status",)]


def test_a_variant_whose_sql_fails_gives_no_answer(db):
    with duckdb.connect(str(db), read_only=True) as con:
        got = answers(con, (next(v for v in VARIANTS if v.name == "payments as revenue"),))
    assert got["gmv_SP_2018"] is None   # payments have no customer state


def test_explain_prefers_the_single_cause_and_admits_what_it_cannot_explain(computed):
    reference, by = computed
    q = gold_for(reference, "gmv_2018")
    assert explain(q, number(320.0), by) == ["every order status"]
    assert explain(q, number(275.0), by) == ["freight inside the price"]
    assert explain(q, number(352.0), by) == ["every order status + freight inside the price"]
    assert explain(q, number(999.0), by) == []
    assert explain(q, {"answer_type": "cannot_answer"}, by) == []


def test_classify_counts_only_wrong_answers_of_the_first_run(computed):
    reference, by = computed
    gold = [gold_for(reference, "gmv_2018"), gold_for(reference, "customers_total")]

    def ep(qid, value, **kw):
        return {"question_id": qid, "model": "m", "arm": "sql", "answer": number(value), **kw}

    result = classify([ep("gmv_2018", 250.0), ep("gmv_2018", 320.0, model="n"), ep("customers_total", 3),
                       ep("customers_total", 77), ep("gmv_2018", 320.0, run=2)], gold, by)
    rows = {r["model"]: r for r in result["summary"]}
    assert rows["m"] == {"model": "m", "arm": "sql", "wrong": 2, "explained": 1,
                         # on this small database two readings give 3 customers, and both are reported
                         "causes": {"only canceled excluded or customer_id as the customer": 1, UNEXPLAINED: 1}}
    assert rows["n"]["causes"] == {"every order status": 1}
    assert len(result["answers"]) == 3


def test_the_command_refuses_without_the_database(tmp_path, monkeypatch):
    from metricbench import data
    monkeypatch.setattr(data, "RAW_DB", tmp_path / "missing.duckdb")
    with pytest.raises(SystemExit, match="metricbench build"):
        cli.cmd_causes(None)
