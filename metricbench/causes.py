"""Why each wrong answer is wrong: which known reading of the question reproduces it.

Every reference answer is computed again under each *variant*: one convention changed the way a model that
does not know the company's definitions would plausibly change it (count every order status, count
`customer_id`, add freight to GMV...). A wrong answer that equals a variant's answer within the scoring
tolerance is explained by that variant. Pairs of variants are tried too, for answers that take two wrong
turns. What matches nothing is reported as unexplained, not guessed.

Most variants change the reference views, so they apply to every question at once. A few change the
question's own SQL (the join that repeats rows, the average per item) and only apply where that SQL has
the pattern. Needs the raw database (`metricbench build`); calls no model.
"""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path
from typing import Any, Callable, Iterable

import duckdb

from .analysis import arm_label
from .questions import QUESTIONS, REFERENCE_VIEWS, Question
from .score import grade

UNEXPLAINED = "unexplained"


def swap(old: str, new: str) -> Callable[[str], str]:
    """Replace `old` with `new`; the text must contain `old` exactly once, so a reworded view is noticed."""
    def apply(sql: str) -> str:
        if sql.count(old) != 1:
            raise ValueError(f"expected exactly one {old!r}")
        return sql.replace(old, new)
    return apply


def swap_any(pattern: str, new: str) -> Callable[[str], str | None]:
    """Replace every match of `pattern` in a question's SQL; None when it has none (the variant doesn't apply)."""
    def apply(sql: str) -> str | None:
        out, n = re.subn(pattern, new, sql)
        return out if n else None
    return apply


@dataclass(frozen=True)
class Variant:
    name: str
    what: str
    views: Callable[[str], str] | None = None           # changes the reference views
    query: Callable[[str], str | None] | None = None    # changes the question's SQL, where it applies
    group: str = ""                                     # variants of one group are alternatives, never combined


_VALID = "o.order_status not in ('canceled', 'unavailable') as valid"

VARIANTS: tuple[Variant, ...] = (
    Variant("every order status", "Canceled and unavailable orders are counted.",
            views=swap(_VALID, "true as valid"), group="status"),
    Variant("only canceled excluded", "Unavailable orders are counted; canceled ones are not.",
            views=swap(_VALID, "o.order_status <> 'canceled' as valid"), group="status"),
    Variant("only delivered orders", "Only orders with status delivered are counted.",
            views=swap(_VALID, "o.order_status = 'delivered' as valid"), group="status"),
    Variant("customer_id as the customer", "Customers are counted by customer_id, which is new on every order.",
            views=swap("c.customer_unique_id as cu", "o.customer_id as cu")),
    Variant("freight inside the price", "Item value is price plus freight.",
            views=swap("i.price, i.freight_value,", "i.price + i.freight_value as price, i.freight_value,")),
    Variant("late by timestamp", "Late means delivered after the estimated timestamp, not on a later calendar day.",
            views=swap("cast(dlv as date) > cast(est as date) as late", "dlv > est as late")),
    Variant("whole days", "Delivery time is the difference between calendar dates, without fractions.",
            views=swap("date_diff('second', ts, dlv) / 86400.0 as days", "date_diff('day', ts, dlv) as days")),
    Variant("every review", "All reviews of an order are averaged, not only the latest.",
            views=swap("""select order_id, review_score from (
    select *, row_number() over (partition by order_id
        order by review_answer_timestamp desc, review_creation_date desc, review_id) as rn
    from order_reviews)
where rn = 1;""", "select order_id, review_score from order_reviews;")),
    Variant("only translated categories", "Products without a translated category are dropped by an inner join.",
            views=swap("left join product_category_name_translation t", "join product_category_name_translation t")),
    Variant("unavailable as canceled", "Unavailable orders are counted as cancellations.",
            query=swap_any(r"order_status = 'canceled'", "order_status in ('canceled', 'unavailable')")),
    Variant("items joined to payments", "Item prices are summed after joining payments, which repeats rows.",
            query=swap_any(r"\bfrom ri\b", "from ri join order_payments using (order_id)")),
    Variant("payments joined to items", "Payments are summed after joining items, which repeats rows.",
            query=swap_any(r"\bfrom rp\b", "from rp join order_items using (order_id)")),
    Variant("payments as revenue", "Revenue is what customers paid (with freight and interest), not item prices.",
            query=swap_any(r"sum\(price\)(.*?)\bfrom ri\b", r"sum(payment_value)\1from rp"), group="revenue"),
    Variant("average per item", "The average is taken over items, not over orders.",
            query=swap_any(r"^select \(select sum\(price\) from ri where (.*?)\) / \(select count\(\*\) from ro .*$",
                           r"select avg(price) from ri where \1"), group="revenue"),
)


def combos(variants: Iterable[Variant] = VARIANTS) -> list[tuple[Variant, ...]]:
    """Each variant alone, then every pair that can go together."""
    variants = tuple(variants)
    pairs = [(a, b) for a, b in combinations(variants, 2) if not (a.group and a.group == b.group)]
    return [(v,) for v in variants] + pairs


def _plain(value: Any) -> Any:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return value
    return number if math.isfinite(number) else None


def answers(con: duckdb.DuckDBPyConnection, combo: tuple[Variant, ...] = (),
            questions: Iterable[Question] = QUESTIONS) -> dict[str, Any]:
    """The answer to every answerable question under `combo`; None where a variant doesn't apply or fails."""
    views = REFERENCE_VIEWS
    for v in combo:
        if v.views:
            views = v.views(views)
    con.execute(views)
    out: dict[str, Any] = {}
    for q in questions:
        if q.kind == "none":
            continue
        sql: str | None = q.ref
        for v in combo:
            if v.query and sql is not None:
                sql = v.query(sql)  # None: this question's SQL has nothing for the variant to change
        if sql is None:
            out[q.id] = None
            continue
        try:
            rows = con.sql(sql).fetchall()
        except duckdb.Error:
            out[q.id] = None  # e.g. the swapped view has no such column for this question
            continue
        if q.kind == "number":
            out[q.id] = _plain(rows[0][0]) if rows else None
        else:
            table = [[k, _plain(v)] for k, v in rows]
            out[q.id] = table if table and all(v is not None for _, v in table) else None
    return out


def variant_answers(db: Path | str) -> tuple[dict[str, Any], dict[tuple[str, ...], dict[str, Any]]]:
    """(reference answers, answers under every variant and pair). Variants that leave an answer unchanged
    are dropped for that question: they explain nothing there."""
    with duckdb.connect(str(db), read_only=True) as con:
        reference = answers(con)
        table: dict[tuple[str, ...], dict[str, Any]] = {}
        for combo in combos():
            got = answers(con, combo)
            table[tuple(v.name for v in combo)] = {qid: a for qid, a in got.items()
                                                   if a is not None and a != reference[qid]}
    return reference, table


def explain(question: dict, answer: dict | None, by_combo: dict[tuple[str, ...], dict[str, Any]]) -> list[str]:
    """The simplest variants (or pairs, joined with " + ") whose answer the model's answer matches."""
    found: list[tuple[str, ...]] = []
    for names, per_question in by_combo.items():
        expected = per_question.get(question["id"])
        if expected is None:
            continue
        if grade({**question, "answer": expected}, answer) == "correct":
            found.append(names)
    if not found:
        return []
    shortest = min(len(names) for names in found)
    return [" + ".join(names) for names in found if len(names) == shortest]


def classify(episodes: Iterable[dict], gold_list: list[dict],
             by_combo: dict[tuple[str, ...], dict[str, Any]]) -> dict[str, Any]:
    gold = {q["id"]: q for q in gold_list}
    rows: dict[tuple[str, str], Counter] = defaultdict(Counter)
    details = []
    for ep in episodes:
        q = gold.get(ep["question_id"])
        if not q or q["kind"] == "none" or ep.get("run", 1) != 1 or grade(q, ep["answer"]) != "wrong":
            continue
        causes = explain(q, ep["answer"], by_combo)
        label = " or ".join(causes) if causes else UNEXPLAINED
        rows[(ep["model"], arm_label(ep))][label] += 1
        details.append({"model": ep["model"], "arm": arm_label(ep), "question_id": q["id"], "cause": label})
    summary = [{"model": m, "arm": a, "wrong": sum(c.values()), "explained": sum(c.values()) - c[UNEXPLAINED],
                "causes": dict(c.most_common())} for (m, a), c in sorted(rows.items())]
    return {"variants": [{"name": v.name, "what": v.what} for v in VARIANTS], "summary": summary, "answers": details}


def text(result: dict[str, Any], arm: str = "sql") -> str:
    total: Counter = Counter()
    out = []
    for r in result["summary"]:
        out.append(f"{r['model']:20s} {r['arm']:13s} wrong {r['wrong']:3d}  explained {r['explained']:3d}")
        if r["arm"] == arm:
            total.update(r["causes"])
    wrong = sum(total.values())
    out += ["", f"Causes of the {wrong} wrong answers on {arm}, all models:"]
    out += [f"  {n:4d}  {cause}" for cause, n in total.most_common()]
    for r in result["summary"]:
        if r["arm"] == arm:
            out += ["", f"{r['model']}, {arm}:"] + [f"  {n:4d}  {cause}" for cause, n in r["causes"].items()]
    return "\n".join(out)


def listing(result: dict[str, Any]) -> str:
    """One line per wrong answer: model, arm, question and the cause it was given."""
    return "\n".join(f"{a['model']}\t{a['arm']}\t{a['question_id']}\t{a['cause']}" for a in result["answers"])


def markdown(result: dict[str, Any], arm: str = "sql") -> str:
    """The same summary as a table, for the CI job summary."""
    total: Counter = Counter()
    for r in result["summary"]:
        if r["arm"] == arm:
            total.update(r["causes"])
    wrong = sum(total.values())
    lines = [f"### Causes of the {wrong} wrong answers on `{arm}`, all models", "", "| Cause | Wrong answers |",
             "|---|---:|"]
    lines += [f"| {cause} | {n} |" for cause, n in total.most_common()]
    lines += ["", "| Model | Arm | Wrong | Explained |", "|---|---|---:|---:|"]
    lines += [f"| {r['model']} | {r['arm']} | {r['wrong']} | {r['explained']} |" for r in result["summary"]]
    return "\n".join(lines) + "\n"
