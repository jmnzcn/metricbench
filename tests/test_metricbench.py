import math

import pytest

from metricbench.agent import calculate
from metricbench.data import RAW_DB, SEMANTIC_DB
from metricbench.questions import QUESTIONS
from metricbench.score import grade, key

built = pytest.mark.skipif(not (RAW_DB.exists() and SEMANTIC_DB.exists()), reason="run `metricbench build` first")


def q(kind="number", answer=1234.5, **kw):
    return {"kind": kind, "answer": answer, **kw}


def a(answer_type="number", number=None, table=()):
    return {"answer_type": answer_type, "number": number, "table": list(table), "explanation": ""}


def test_numbers_within_a_tenth_of_a_percent():
    assert grade(q(), a(number=1234.5)) == "correct"
    assert grade(q(), a(number=1235.6)) == "correct"
    assert grade(q(), a(number=1242.0)) == "wrong"  # 0.6%: the smallest trap must not pass


def test_rates_as_fraction_or_percent():
    assert grade(q(answer=0.0773, percent=True), a(number=7.73)) == "correct"
    assert grade(q(answer=0.0773, percent=True), a(number=0.0773)) == "correct"
    assert grade(q(answer=0.0773), a(number=7.73)) == "wrong"


def test_abstaining_and_inventing():
    assert grade(q(kind="none", answer=None), a("cannot_answer")) == "correct"
    assert grade(q(kind="none", answer=None), a(number=0.3)) == "invented"
    assert grade(q(), a("cannot_answer")) == "abstained"
    assert grade(q(), None) == "no_answer"


def test_table_labels_are_compared_loosely():
    expected = q("table", [["SP", 10.0], ["RJ", 5.0]], ordered=True)
    assert grade(expected, a("table", table=[{"label": "São Paulo", "value": 10}, {"label": "rj", "value": 5}])) == "correct"
    assert grade(expected, a("table", table=[{"label": "RJ", "value": 5}, {"label": "SP", "value": 10}])) == "wrong"
    assert grade({**expected, "ordered": False},
                 a("table", table=[{"label": "RJ", "value": 5}, {"label": "SP", "value": 10}])) == "correct"


@pytest.mark.parametrize("label,canonical", [
    ("2018-01", "201801"), ("2018-01-01", "201801"), ("January 2018", "201801"), ("Jan 2018", "201801"),
    ("2017-Q3", "2017q3"), ("Q3 2017", "2017q3"), ("health & beauty", "healthandbeauty"),
    ("health_beauty", "healthbeauty"), ("Credit card", "creditcard"), ("Rio de Janeiro", "rj"),
])
def test_label_keys(label, canonical):
    assert key(label) == canonical


def test_calculate_only_does_arithmetic():
    assert calculate("(7341182.41 / 6108489.52 - 1) * 100") == pytest.approx(20.18, abs=0.01)
    with pytest.raises(ValueError):
        calculate("__import__('os')")


def test_question_ids_are_unique():
    ids = [q.id for q in QUESTIONS]
    assert len(ids) == len(set(ids))


@built
def test_semantic_layer_matches_the_reference_sql():
    """Both implementations of every answerable question agree to nine digits."""
    from metricbench.reference import compute
    from metricbench.semantic import SemanticLayer

    gold, layer = compute(), SemanticLayer()

    def same(x, y):
        return math.isclose(float(x), float(y), rel_tol=1e-9, abs_tol=1e-9)

    for question in QUESTIONS:
        if question.kind == "none" or not question.in_semantic_layer:
            continue
        got, expected = question.sem(layer), gold[question.id]
        if question.kind == "number":
            assert same(got, expected), question.id
            continue
        got = [(key(k), v) for k, v in got]
        expected = [(key(k), v) for k, v in expected]
        if not question.ordered:
            got, expected = sorted(got), sorted(expected)
        assert [k for k, _ in got] == [k for k, _ in expected], question.id
        assert all(same(x, y) for (_, x), (_, y) in zip(got, expected)), question.id


def test_a_full_ranking_answers_which_one_is_highest():
    expected = q("table", [["RJ", 0.121]], ordered=True)
    ranking = [{"label": "RJ", "value": 0.121}, {"label": "RS", "value": 0.06}]
    assert grade(expected, a("table", table=ranking)) == "correct"
    assert grade(expected, a("table", table=ranking[::-1])) == "wrong"


def test_quarters_within_a_year_can_be_named_any_way():
    expected = q("table", [["2017-Q1", 10], ["2017-Q2", 20]], key="quarter")
    for labels in (["2017-01-01", "2017-04-01"], ["Q1", "Q2"], ["Q1 2017", "Q2 2017"]):
        rows = [{"label": l, "value": v} for l, v in zip(labels, (10, 20))]
        assert grade(expected, a("table", table=rows)) == "correct", labels
