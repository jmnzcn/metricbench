"""`metricbench run` from end to end with a scripted model: the path that spends money, tested without spending."""

import argparse
import json

import pytest

from metricbench import backends, cli
from test_agent import COUNT, Scripted, raw_db  # noqa: F401  (raw_db is a fixture)

GOLD = [{"id": f"orders_{y}", "question": f"How many orders in {y}?", "kind": "number", "answer": 2.0,
         "tags": ["status"], "in_semantic_layer": True} for y in (2016, 2017, 2018)]


def submit(number):
    return ("submit_answer", {"answer_type": "number", "number": number, "table": [], "explanation": ""})


def args(**kw):
    base = dict(backend="claude", model="scripted-1", base_url=None, price=None, effort="medium", arm="sql",
                ids=None, limit=None, workers=1, run=1, max_turns=14, max_tool_calls=20, max_cost=None)
    return argparse.Namespace(**{**base, **kw})


@pytest.fixture
def bench(tmp_path, monkeypatch, raw_db):  # noqa: F811
    monkeypatch.setattr(cli, "RUNS", tmp_path / "runs")
    monkeypatch.setattr(cli, "load_gold", lambda: GOLD)
    made = []

    def use(script):
        def make(*a, **k):
            made.append(Scripted(script))
            return made[-1]
        monkeypatch.setattr(backends, "make", make)
        return made
    return use


def lines(path):
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()]


def test_a_run_writes_one_graded_line_per_question(bench):
    bench([[COUNT], [submit(2)]])
    cli.cmd_run(args())
    rows = lines(cli.RUNS / "scripted-1__sql.jsonl")
    assert [r["question_id"] for r in rows] == ["orders_2016", "orders_2017", "orders_2018"]
    assert {r["grade"] for r in rows} == {"correct"}
    assert {(r["run"], r["max_turns"], r["max_tool_calls"]) for r in rows} == {(1, 14, 20)}


def test_running_again_asks_nothing_new(bench):
    made = bench([[submit(2)]])
    cli.cmd_run(args())
    cli.cmd_run(args())
    assert len(lines(cli.RUNS / "scripted-1__sql.jsonl")) == 3
    assert made[1].sent == []  # the second run found every question done


def test_a_second_run_and_a_larger_budget_are_saved_apart_and_reported_apart(bench):
    bench([[submit(2)]])
    cli.cmd_run(args())
    bench([[submit(5)]])
    cli.cmd_run(args(run=2))
    bench([[COUNT]] * 20 + [[submit(2)]])
    cli.cmd_run(args(max_turns=30, max_tool_calls=40))
    assert sorted(p.name for p in cli.RUNS.iterdir()) == [
        "scripted-1__sql.jsonl", "scripted-1__sql__r2.jsonl", "scripted-1__sql__t30.jsonl"]
    rows = {r["arm"]: r for r in cli.summarize(cli.load_runs())}
    assert (rows["sql"]["runs"], rows["sql"]["accuracy_min"], rows["sql"]["accuracy_max"]) == (2, 0.0, 1.0)
    assert rows["sql"]["questions"] == 3
    assert (rows["sql (30 turns)"]["runs"], rows["sql (30 turns)"]["accuracy"]) == (1, 1.0)


def test_the_default_budget_stops_where_a_larger_one_answers(bench):
    bench([[COUNT]] * 20 + [[submit(2)]])
    cli.cmd_run(args(limit=1))
    (row,) = lines(cli.RUNS / "scripted-1__sql.jsonl")
    assert row["stopped"] == "turn budget" and row["grade"] == "no_answer"


def test_max_cost_stops_asking_and_the_next_run_continues(bench, capsys, monkeypatch):
    monkeypatch.setitem(backends.PRICES, "scripted-1", (1.0, 1.0, 0.1))
    made = bench([[submit(2)]])  # each question costs $0.001 with the scripted model
    cli.cmd_run(args(max_cost=0.0015))
    assert len(lines(cli.RUNS / "scripted-1__sql.jsonl")) == 2
    assert "1 questions not asked" in capsys.readouterr().out
    cli.cmd_run(args(max_cost=0.0015))
    assert len(lines(cli.RUNS / "scripted-1__sql.jsonl")) == 3
    assert len(made[1].sent) == 1


def test_max_cost_refuses_a_model_it_cannot_price(bench):
    made = bench([[submit(2)]])
    with pytest.raises(SystemExit, match="--max-cost needs prices"):
        cli.cmd_run(args(max_cost=1.0))
    assert made[0].sent == []  # nothing was sent to the model
    assert not list(cli.RUNS.glob("*.jsonl"))
