"""The three ways an agent can reach the data, and the loop that asks it one question.

  sql           SQL over the raw tables, with their schema. What you get by pointing a model at the database.
  sql+glossary  the same, plus the official metric definitions.
  semantic      the metric definitions and the semantic layer (MetricFlow) as tools. No SQL.

All three get the same instructions for answering, so differences come from how the data is reached.
"""

from __future__ import annotations

import ast
import json
import operator
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import duckdb

from .backends import Backend
from .data import RAW_DB
from .semantic import SemanticLayer

ARMS = ("sql", "sql+glossary", "semantic")
GLOSSARY = (Path(__file__).with_name("glossary.md")).read_text(encoding="utf-8")
MAX_ROWS = 50

ANSWERING = """You are a data analyst at Olist, a Brazilian e-commerce marketplace. Answer the business question
with the tools, then call submit_answer once.
- Give numbers with full precision (at least 4 significant digits). Do not round to thousands.
- A percentage or rate can be a fraction (0.25) or a percent (25).
- For a list or ranking, use answer_type "table": one row per item, with its label and value.
- If the data cannot answer the question, use answer_type "cannot_answer" and say why. Do not guess and do
  not use outside data."""

SEMANTIC_HELP = """You reach the data through the company's semantic layer (MetricFlow); there is no SQL access.
- list_metrics, then list_dimensions for the metrics you need, then query_metrics.
- Time: group by metric_time__day, metric_time__week, metric_time__month, metric_time__quarter or
  metric_time__year. Filter time with where, e.g. {{ TimeDimension('metric_time', 'year') }} = '2018-01-01'
  or {{ TimeDimension('metric_time', 'month') }} between '2018-01-01' and '2018-06-01'.
- Other filters use the dimension names list_dimensions gives, e.g. {{ Dimension('order__customer_state') }} = 'SP'.
- Use calculate for arithmetic on the results."""

SUBMIT = {
    "name": "submit_answer",
    "description": "Submit the final answer to the question. Call it once.",
    "input_schema": {
        "type": "object",
        "properties": {
            "answer_type": {"type": "string", "enum": ["number", "table", "cannot_answer"]},
            "number": {"type": ["number", "null"], "description": "The answer when answer_type is number."},
            "table": {
                "type": "array",
                "description": "The rows when answer_type is table; otherwise empty.",
                "items": {
                    "type": "object",
                    "properties": {"label": {"type": "string"}, "value": {"type": "number"}},
                    "required": ["label", "value"],
                    "additionalProperties": False,
                },
            },
            "explanation": {"type": "string", "description": "One or two sentences on how you got it."},
        },
        "required": ["answer_type", "number", "table", "explanation"],
        "additionalProperties": False,
    },
}

RUN_SQL = {
    "name": "run_sql",
    "description": f"Run one read-only DuckDB SQL query on the database. Returns at most {MAX_ROWS} rows.",
    "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"],
                     "additionalProperties": False},
}

LIST_METRICS = {
    "name": "list_metrics",
    "description": "List the metrics in the semantic layer, with their definitions.",
    "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
}

LIST_DIMENSIONS = {
    "name": "list_dimensions",
    "description": "List the dimensions that all the given metrics can be grouped or filtered by.",
    "input_schema": {"type": "object", "properties": {"metrics": {"type": "array", "items": {"type": "string"}}},
                     "required": ["metrics"], "additionalProperties": False},
}

QUERY_METRICS = {
    "name": "query_metrics",
    "description": f"Query metrics, optionally grouped, filtered, ordered and limited. Returns at most {MAX_ROWS} rows.",
    "input_schema": {
        "type": "object",
        "properties": {
            "metrics": {"type": "array", "items": {"type": "string"}},
            "group_by": {"type": "array", "items": {"type": "string"}},
            "where": {"type": "array", "items": {"type": "string"},
                      "description": "Filters with Jinja dimension references; all must hold."},
            "order_by": {"type": "array", "items": {"type": "string"},
                         "description": "Metric or group-by names; prefix with - for descending."},
            "limit": {"type": ["integer", "null"]},
        },
        "required": ["metrics", "group_by", "where", "order_by", "limit"],
        "additionalProperties": False,
    },
}

CALCULATE = {
    "name": "calculate",
    "description": "Evaluate an arithmetic expression with + - * / ** and parentheses.",
    "input_schema": {"type": "object", "properties": {"expression": {"type": "string"}},
                     "required": ["expression"], "additionalProperties": False},
}

_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.Pow: operator.pow, ast.USub: operator.neg, ast.UAdd: operator.pos}


def calculate(expression: str) -> float:
    def ev(node: ast.AST) -> float:
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
            return _OPS[type(node.op)](ev(node.left), ev(node.right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
            return _OPS[type(node.op)](ev(node.operand))
        raise ValueError("only numbers and + - * / ** ( ) are allowed")
    return ev(ast.parse(expression.replace(",", ""), mode="eval").body)


def schema_text(con: duckdb.DuckDBPyConnection) -> str:
    rows = con.sql("select table_name, column_name, data_type from information_schema.columns "
                   "order by table_name, ordinal_position").fetchall()
    tables: dict[str, list[str]] = {}
    for t, c, ty in rows:
        tables.setdefault(t, []).append(f"{c} {ty}")
    return "\n".join(f"- {t}({', '.join(cols)})" for t, cols in tables.items())


def _json(value: Any) -> str:
    return json.dumps(value, default=str, ensure_ascii=False)


class Tools:
    """The tools of one arm, plus the submitted answer."""

    def __init__(self, arm: str, semantic: SemanticLayer | None = None):
        self.arm = arm
        self.answer: dict | None = None
        self.calls: list[dict] = []
        if arm == "semantic":
            self.semantic = semantic or SemanticLayer()
            self.specs = [LIST_METRICS, LIST_DIMENSIONS, QUERY_METRICS, CALCULATE, SUBMIT]
        else:
            self.con = duckdb.connect(str(RAW_DB), read_only=True)
            self.specs = [RUN_SQL, SUBMIT]

    def system_prompt(self) -> str:
        if self.arm == "semantic":
            return f"{ANSWERING}\n\n{SEMANTIC_HELP}\n\n{GLOSSARY}"
        db = f"You can query the company database (DuckDB SQL) with run_sql. Tables:\n{schema_text(self.con)}"
        return f"{ANSWERING}\n\n{db}" + (f"\n\n{GLOSSARY}" if self.arm == "sql+glossary" else "")

    def _sql(self, query: str) -> str:
        timer = threading.Timer(60, self.con.interrupt)
        timer.start()
        try:
            cur = self.con.execute(query)
            cols = [d[0] for d in cur.description or []]
            rows = cur.fetchmany(MAX_ROWS + 1)
        finally:
            timer.cancel()
        more = len(rows) > MAX_ROWS
        return _json({"columns": cols, "rows": [list(r) for r in rows[:MAX_ROWS]], "truncated": more})

    def call(self, name: str, args: dict) -> tuple[str, bool]:
        error = False
        try:
            if name == "submit_answer":
                self.answer = args
                out = "Answer recorded."
            elif name == "run_sql" and self.arm != "semantic":
                out = self._sql(args["query"])
            elif name == "list_metrics" and self.arm == "semantic":
                out = _json(self.semantic.list_metrics())
            elif name == "list_dimensions" and self.arm == "semantic":
                out = _json(self.semantic.list_dimensions(args["metrics"]))
            elif name == "query_metrics" and self.arm == "semantic":
                r = self.semantic.query(args["metrics"], args.get("group_by") or (), args.get("where") or (),
                                        args.get("order_by") or (), args.get("limit"))
                out = _json({"columns": r["columns"], "rows": r["rows"][:MAX_ROWS],
                             "truncated": len(r["rows"]) > MAX_ROWS})
            elif name == "calculate" and self.arm == "semantic":
                out = _json({"result": calculate(args["expression"])})
            else:
                out, error = f"Unknown tool {name}.", True
        except Exception as exc:  # the model sees the error and can fix its call
            out, error = f"{type(exc).__name__}: {str(exc)[:1500]}", True
        self.calls.append({"name": name, "args": args, "is_error": error, "result": out[:2000]})
        return out, error


@dataclass
class Budget:
    max_turns: int = 14
    max_tool_calls: int = 20
    max_seconds: float = 300.0


@dataclass
class Episode:
    question_id: str
    arm: str
    backend: str
    model: str
    answer: dict | None
    calls: list[dict]
    turns: int
    input_tokens: int
    output_tokens: int
    seconds: float
    cost_usd: float
    stopped: str
    texts: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


NUDGE = "You have not submitted an answer. Call submit_answer now with what you have found."


def ask(question: dict, arm: str, backend: Backend, budget: Budget | None = None,
        semantic: SemanticLayer | None = None) -> Episode:
    budget = budget or Budget()
    tools = Tools(arm, semantic)
    conv = backend.conversation(tools.system_prompt(), tools.specs)
    t0 = time.perf_counter()
    stopped, nudged = "answer", False
    try:
        turn = conv.send(question["question"])
        while tools.answer is None:
            if len(conv.turns) >= budget.max_turns:
                stopped = "turn budget"
                break
            if time.perf_counter() - t0 > budget.max_seconds:
                stopped = "time budget"
                break
            if not turn.calls:
                if nudged:
                    stopped = "no answer"
                    break
                nudged = True
                turn = conv.send(NUDGE)
                continue
            if len(tools.calls) + len(turn.calls) > budget.max_tool_calls:
                stopped = "tool budget"
                break
            results = []
            for c in turn.calls:
                out, err = tools.call(c.name, c.args)
                results.append((c, out, err))
            if tools.answer is not None:
                break
            turn = conv.send_results(results)
    except Exception as exc:  # an API or transport failure ends the question; it is scored, not hidden
        stopped = f"error: {type(exc).__name__}: {str(exc)[:200]}"
    return Episode(
        question_id=question["id"], arm=arm, backend=backend.name, model=backend.model, answer=tools.answer,
        calls=tools.calls, turns=len(conv.turns), input_tokens=sum(t.input_tokens for t in conv.turns),
        output_tokens=sum(t.output_tokens for t in conv.turns), seconds=round(time.perf_counter() - t0, 2),
        cost_usd=round(sum(t.cost_usd for t in conv.turns), 5), stopped=stopped,
        texts=[t.text for t in conv.turns if t.text])
