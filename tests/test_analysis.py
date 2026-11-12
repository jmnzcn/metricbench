import pytest

from metricbench.analysis import (
    agreement,
    analyze,
    answerable,
    band,
    family,
    misses,
    offers_expected,
    relative_error,
    tolerance,
)
from metricbench.analysis import arm_label
from metricbench.cli import estimate, run_file


def q(qid, answer=100.0, kind="number", **kw):
    return {"id": qid, "kind": kind, "answer": answer, **kw}


def ep(qid, number=None, model="m1", arm="sql", answer_type="number", **kw):
    answer = None if answer_type is None else {"answer_type": answer_type, "number": number, "table": []}
    return {"question_id": qid, "model": model, "arm": arm, "answer": answer, "cost_usd": 0.01, **kw}


GOLD = {x["id"]: x for x in [q("gmv_2017"), q("gmv_2018"), q("gmv_SP_2018"), q("aov_2018"),
                             q("profit_margin_2018", None, "none")]}


def test_family_drops_the_period_and_the_state():
    assert family("gmv_2017") == family("gmv_2018-04") == family("gmv_SP_2018") == "gmv"
    assert family("orders_by_month_2018h1") == "orders_by_month"
    assert family("late_rate_RJ") == "late_rate"
    assert family("gmv_seller_sp_2018") == "gmv_seller_sp"  # a different metric, not the state suffix
    assert family("repeat_rate_2017_cohort") == "repeat_rate_cohort"


def test_relative_error_and_bands():
    assert relative_error(q("a"), {"answer_type": "number", "number": 100.6}) == pytest.approx(0.006)
    assert band(q("a"), {"answer_type": "number", "number": 100.6}) == "within 1%"
    assert band(q("a"), {"answer_type": "number", "number": 104}) == "1% to 5%"
    assert band(q("a"), {"answer_type": "number", "number": 117}) == "over 5%"
    assert band(q("a"), {"answer_type": "number", "number": None, "table": []}) == "no number"
    assert band(q("a", [["SP", 1]], "table"), {"answer_type": "table", "table": []}) == "wrong table"
    # a rate given as a percent is compared as the fraction it means
    assert relative_error(q("a", 0.0773, percent=True), {"answer_type": "number", "number": 7.73}) < 1e-9


def test_answerable_leaves_out_unanswerable_and_separates_no_answer():
    episodes = [ep("gmv_2017", 100), ep("gmv_2018", 117), ep("gmv_SP_2018", answer_type=None),
                ep("aov_2018", 100), ep("profit_margin_2018", answer_type="cannot_answer")]
    (row,) = answerable(episodes, GOLD)
    assert (row["questions"], row["correct"], row["wrong"], row["no_answer"]) == (4, 2, 1, 1)
    assert row["accuracy"] == 0.5
    assert row["accuracy_when_answered"] == 2 / 3
    assert 0 <= row["low"] <= row["accuracy"] <= row["high"] <= 1


def test_misses_counts_only_wrong_answers():
    episodes = [ep("gmv_2017", 100.6), ep("gmv_2018", 117), ep("aov_2018", 100), ep("gmv_SP_2018", answer_type=None)]
    (row,) = misses(episodes, GOLD)
    assert (row["wrong"], row["within 1%"], row["over 5%"]) == (2, 1, 1)


def test_an_explanation_that_also_gives_the_expected_number_is_noticed():
    def said(text):
        return {"answer_type": "number", "number": 1.0, "explanation": text}

    gmv = q("gmv", 993592.98)
    assert offers_expected(gmv, said("All statuses: R$996,647.75. Excluding canceled orders gives R$993,592.98."))
    assert not offers_expected(gmv, said("Sum of price for April 2018, all order statuses: R$996,647.75."))
    rate = q("late", 0.0773, percent=True)
    assert offers_expected(rate, said("9.37% by timestamp; by calendar day it is 7.73%."))
    assert not offers_expected(rate, said("9.37% of delivered orders arrived late."))
    assert not offers_expected(q("t", [["SP", 1]], "table"), said("1"))
    assert not offers_expected(gmv, None)


def test_tolerance_changes_what_counts_as_right():
    (row,) = tolerance([ep("gmv_2017", 100.6), ep("gmv_2018", 104), ep("aov_2018", 100)], GOLD)
    assert (row["0.001"], row["0.01"], row["0.05"]) == (1 / 3, 2 / 3, 1.0)


def test_agreement_leaves_out_a_model_that_mostly_did_not_answer():
    episodes = [ep("gmv_2017", 100, "a"), ep("aov_2018", 5, "a"), ep("gmv_2018", 5, "a"),
                ep("gmv_2017", 100, "b"), ep("aov_2018", 5, "b"), ep("gmv_2018", 100, "b"),
                ep("gmv_2017", answer_type=None, model="c"), ep("aov_2018", 100, "c"), ep("gmv_2018", 100, "c")]
    (row,) = agreement(episodes, GOLD)
    assert row["models"] == ["a", "b"] and row["left_out"] == ["c"]
    assert (row["all_right"], row["all_wrong"], row["split"]) == (1, 1, 1)


def test_later_runs_do_not_count_in_the_analysis():
    result = analyze([ep("gmv_2017", 100), ep("gmv_2017", 5, run=2)], list(GOLD.values()))
    assert result["answerable"][0]["questions"] == 1 and result["answerable"][0]["correct"] == 1


def test_runs_and_budgets_get_their_own_file_and_row():
    assert run_file("m", "sql+rule").name == "m__sql-rule.jsonl"
    assert run_file("m", "sql", run=3).name == "m__sql__r3.jsonl"
    assert run_file("m", "sql", max_turns=30).name == "m__sql__t30.jsonl"
    assert arm_label({"arm": "sql"}) == "sql"
    assert arm_label({"arm": "sql", "max_turns": 30}) == "sql (30 turns)"


def test_estimate_uses_the_saved_cost_of_the_nearest_arm():
    episodes = [ep("gmv_2017", 100, "m1"), ep("gmv_2018", 100, "m1"), ep("gmv_2017", 100, "m2", "semantic")]
    rows = estimate(episodes, "sql+rule", None, runs=3, questions=10)
    assert rows[0]["basis"] == "sql" and abs(rows[0]["total"] - 0.3) < 1e-9
    assert rows[1]["total"] is None  # m2 has no sql run to estimate from


def test_a_different_turn_budget_is_never_mixed_with_the_default():
    episodes = [ep("gmv_2017", 5), ep("gmv_2017", 100, max_turns=30)]
    rows = {r["arm"]: r for r in answerable(episodes, GOLD)}
    assert rows["sql"]["correct"] == 0 and rows["sql (30 turns)"]["correct"] == 1
    assert {r["arm"] for r in analyze(episodes, list(GOLD.values()))["misses"]} == {"sql", "sql (30 turns)"}
