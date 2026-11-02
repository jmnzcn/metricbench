"""Expected answers, computed with the reference SQL over the raw tables."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb

from .data import RAW_DB, ROOT
from .questions import QUESTIONS, REFERENCE_VIEWS, Question

GOLD = ROOT / "benchmark" / "gold.json"


def _plain(v: Any) -> Any:
    return float(v) if isinstance(v, Decimal) else v


def answer(con: duckdb.DuckDBPyConnection, q: Question) -> Any:
    if q.kind == "none":
        return None
    rows = con.sql(q.ref).fetchall()
    if q.kind == "number":
        return _plain(rows[0][0])
    return [[_plain(k), _plain(v)] for k, v in rows]


def connect() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(str(RAW_DB), read_only=True)
    con.execute(REFERENCE_VIEWS)
    return con


def compute() -> dict[str, Any]:
    with connect() as con:
        return {q.id: answer(con, q) for q in QUESTIONS}


def write(path: Path = GOLD) -> None:
    gold = compute()
    out = [
        {"id": q.id, "question": q.text, "kind": q.kind, "tags": q.tags, "ordered": q.ordered,
         "in_semantic_layer": q.in_semantic_layer, "percent": q.extra.get("percent", False),
         "key": q.extra.get("key"),
         "answer": gold[q.id], "notes": q.notes}
        for q in QUESTIONS
    ]
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{len(out)} questions -> {path}")


if __name__ == "__main__":
    write()
