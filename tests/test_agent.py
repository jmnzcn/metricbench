"""The agent loop, with a scripted model in place of a real one. No API is called."""

import duckdb
import pytest

from metricbench import agent
from metricbench.agent import ARMS, VALID_ORDER_RULE, Budget, Tools, ask
from metricbench.backends import Backend, Conversation, ToolCall, Turn

QUESTION = {"id": "orders", "question": "How many orders?"}


class Scripted(Backend):
    """Plays back a list of turns; each turn is a list of (tool, args) calls. Repeats the last one."""

    name, model = "scripted", "scripted-1"

    def __init__(self, turns, fail=False):
        self.script, self.fail, self.sent = turns, fail, []

    def conversation(self, system, tools):
        backend = self

        class Conv(Conversation):
            def _next(self):
                if backend.fail:
                    raise RuntimeError("HTTP 500")
                calls = backend.script[min(len(self.turns), len(backend.script) - 1)]
                turn = Turn("", [ToolCall(f"c{len(self.turns)}_{i}", n, a) for i, (n, a) in enumerate(calls)], 10, 5,
                            cost_usd=0.001)
                self.turns.append(turn)
                return turn

            def send(self, user_text):
                backend.sent.append(user_text)
                return self._next()

            def send_results(self, results):
                backend.sent.append(results)
                return self._next()

        self.system = system
        return Conv(system, tools)


@pytest.fixture
def raw_db(tmp_path, monkeypatch):
    path = tmp_path / "raw.duckdb"
    with duckdb.connect(str(path)) as con:
        con.execute("create table orders as select * from (values (1, 'delivered'), (2, 'canceled')) t(order_id, order_status)")
    monkeypatch.setattr(agent, "RAW_DB", path)
    return path


SUBMIT = ("submit_answer", {"answer_type": "number", "number": 2, "table": [], "explanation": "counted"})
COUNT = ("run_sql", {"query": "select count(*) from orders"})


def test_runs_sql_then_records_the_answer(raw_db):
    episode = ask(QUESTION, "sql", Scripted([[COUNT], [SUBMIT]]))
    assert episode.stopped == "answer" and episode.answer["number"] == 2
    assert [c["name"] for c in episode.calls] == ["run_sql", "submit_answer"]
    assert '"rows": [[2]]' in episode.calls[0]["result"]
    assert (episode.turns, episode.input_tokens, episode.cost_usd) == (2, 20, 0.002)


def test_a_sql_error_goes_back_to_the_model(raw_db):
    episode = ask(QUESTION, "sql", Scripted([[("run_sql", {"query": "select * from nowhere"})], [SUBMIT]]))
    assert episode.calls[0]["is_error"] and episode.stopped == "answer"


def test_the_database_is_read_only(raw_db):
    episode = ask(QUESTION, "sql", Scripted([[("run_sql", {"query": "drop table orders"})], [SUBMIT]]))
    assert episode.calls[0]["is_error"]
    with duckdb.connect(str(raw_db), read_only=True) as con:
        assert con.sql("select count(*) from orders").fetchone()[0] == 2


def test_nudges_once_then_gives_up(raw_db):
    backend = Scripted([[]])
    episode = ask(QUESTION, "sql", backend)
    assert episode.stopped == "no answer" and episode.answer is None
    assert backend.sent == [QUESTION["question"], agent.NUDGE]


def test_stops_at_the_turn_budget(raw_db):
    episode = ask(QUESTION, "sql", Scripted([[COUNT]]), Budget(max_turns=3))
    assert episode.stopped == "turn budget" and episode.turns == 3 and episode.answer is None


def test_stops_at_the_tool_budget(raw_db):
    episode = ask(QUESTION, "sql", Scripted([[COUNT, COUNT]]), Budget(max_tool_calls=3))
    assert episode.stopped == "tool budget" and len(episode.calls) == 2


def test_an_api_failure_is_recorded_not_raised(raw_db):
    episode = ask(QUESTION, "sql", Scripted([[SUBMIT]], fail=True))
    assert episode.stopped.startswith("error: RuntimeError") and episode.answer is None


def test_sql_tools_cannot_reach_the_semantic_layer(raw_db):
    tools = Tools("sql")
    out, error = tools.call("query_metrics", {"metrics": ["gmv"]})
    assert error and "Unknown tool" in out


def test_each_sql_arm_gets_only_its_own_context(raw_db):
    prompts = {arm: Tools(arm).system_prompt() for arm in ARMS if arm != "semantic"}
    assert "orders(order_id" in prompts["sql"]
    assert VALID_ORDER_RULE not in prompts["sql"] and "Metric glossary" not in prompts["sql"]
    assert VALID_ORDER_RULE in prompts["sql+rule"] and "Metric glossary" not in prompts["sql+rule"]
    assert "Metric glossary" in prompts["sql+glossary"]
    assert prompts["sql+rule"].startswith(prompts["sql"])  # the rule is the only difference
    assert "valid order is any order whose status is not `canceled` or `unavailable`" in agent.GLOSSARY.replace("*", "")
    assert VALID_ORDER_RULE.endswith("A valid order is any order whose status is not `canceled` or `unavailable`.")
    assert "placed" not in VALID_ORDER_RULE  # no exceptions: one convention only


def test_unknown_arm_is_refused():
    with pytest.raises(ValueError):
        Tools("sql+everything")
