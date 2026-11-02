"""metricbench: run the questions through an agent and grade the answers.

  metricbench build                                  download Olist and build both databases
  metricbench run --arm semantic --backend claude --model claude-haiku-5-5
  metricbench run --arm sql --backend openai --model gemini-3.8-flash \\
      --base-url https://generativelanguage.googleapis.com/v1beta/openai/ --price 0.75,3.75
  metricbench report                                 results table from every file in runs/
"""

from __future__ import annotations

import argparse
import json
import threading
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .data import ROOT

RUNS = ROOT / "runs"


def load_gold() -> list[dict]:
    from .reference import GOLD
    return json.loads(GOLD.read_text(encoding="utf-8"))


def run_file(model: str, arm: str) -> Path:
    return RUNS / f"{model}__{arm.replace('+', '-')}.jsonl"


class LockedSemantic:
    """One MetricFlow engine shared by the worker threads, one query at a time."""

    def __init__(self):
        from .semantic import SemanticLayer
        self.inner, self.lock = SemanticLayer(), threading.Lock()

    def __getattr__(self, name):
        attr = getattr(self.inner, name)
        if not callable(attr):
            return attr

        def locked(*a, **k):
            with self.lock:
                return attr(*a, **k)
        return locked


def cmd_run(args) -> None:
    from .agent import ask
    from .backends import make
    from .score import grade

    price = tuple(float(x) for x in args.price.split(",")) if args.price else None
    backend = make(args.backend, args.model, args.base_url, price, args.effort)
    questions = load_gold()
    if args.ids:
        questions = [q for q in questions if q["id"] in set(args.ids.split(","))]
    if args.limit:
        questions = questions[: args.limit]
    out = run_file(backend.model, args.arm)
    out.parent.mkdir(exist_ok=True)
    done = set()
    if out.exists():
        done = {json.loads(line)["question_id"] for line in out.read_text(encoding="utf-8").splitlines() if line}
    todo = [q for q in questions if q["id"] not in done]
    semantic = LockedSemantic() if args.arm == "semantic" else None
    lock = threading.Lock()
    print(f"{backend.model} · {args.arm}: {len(todo)} questions ({len(done)} already in {out.name})")

    def one(q: dict) -> None:
        ep = ask(q, args.arm, backend, semantic=semantic).to_dict()
        ep["question"] = q["question"]  # the exact wording asked, so a later rewording can be detected
        ep["grade"] = grade(q, ep["answer"])
        with lock:
            with out.open("a", encoding="utf-8") as f:
                f.write(json.dumps(ep, ensure_ascii=False, default=str) + "\n")
            print(f"  {q['id']:32s} {ep['grade']:10s} {ep['turns']:2d} turns  ${ep['cost_usd']:.4f}  {ep['stopped']}")

    with ThreadPoolExecutor(args.workers) as pool:
        list(pool.map(one, todo))


def load_runs() -> list[dict]:
    from .score import grade
    gold = {q["id"]: q for q in load_gold()}
    episodes = []
    for path in sorted(RUNS.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line:
                ep = json.loads(line)
                if ep["question_id"] in gold:
                    ep["grade"] = grade(gold[ep["question_id"]], ep["answer"])  # regrade with the current rules
                    episodes.append(ep)
    return episodes


def summarize(episodes: list[dict]) -> list[dict]:
    gold = {q["id"]: q for q in load_gold()}
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for ep in episodes:
        groups[(ep["model"], ep["arm"])].append(ep)
    rows = []
    for (model, arm), eps in sorted(groups.items()):
        answerable = [e for e in eps if gold[e["question_id"]]["kind"] != "none"]
        in_layer = [e for e in answerable if gold[e["question_id"]]["in_semantic_layer"]]
        unanswerable = [e for e in eps if gold[e["question_id"]]["kind"] == "none"]
        g = Counter(e["grade"] for e in eps)
        rows.append({
            "model": model, "arm": arm, "questions": len(eps),
            "accuracy": g["correct"] / len(eps),
            "answerable_accuracy": sum(e["grade"] == "correct" for e in answerable) / max(len(answerable), 1),
            "in_layer_accuracy": sum(e["grade"] == "correct" for e in in_layer) / max(len(in_layer), 1),
            "refused_unanswerable": sum(e["grade"] == "correct" for e in unanswerable) / max(len(unanswerable), 1),
            "grades": dict(g),
            "cost_usd": round(sum(e["cost_usd"] for e in eps), 4),
            "mean_seconds": round(sum(e["seconds"] for e in eps) / len(eps), 1),
        })
    return rows


def by_tag(episodes: list[dict]) -> dict[str, dict[str, float]]:
    gold = {q["id"]: q for q in load_gold()}
    acc: dict[str, dict[str, list[bool]]] = defaultdict(lambda: defaultdict(list))
    for ep in episodes:
        for tag in gold[ep["question_id"]]["tags"]:
            acc[tag][ep["arm"]].append(ep["grade"] == "correct")
    return {tag: {arm: sum(v) / len(v) for arm, v in arms.items()} for tag, arms in sorted(acc.items())}


def cmd_report(args) -> None:
    episodes = load_runs()
    rows = summarize(episodes)
    print(f"{'model':22s} {'arm':13s} {'n':>3s} {'all':>6s} {'answ.':>6s} {'in SL':>6s} {'refuse':>6s} {'cost':>8s}")
    for r in rows:
        print(f"{r['model']:22s} {r['arm']:13s} {r['questions']:3d} {r['accuracy']:6.1%} {r['answerable_accuracy']:6.1%} "
              f"{r['in_layer_accuracy']:6.1%} {r['refused_unanswerable']:6.1%} ${r['cost_usd']:7.3f}")
    print("\naccuracy by trap and arm (all models)")
    for tag, arms in by_tag(episodes).items():
        print(f"  {tag:18s} " + "  ".join(f"{a}={v:5.1%}" for a, v in sorted(arms.items())))
    if args.json:
        Path(args.json).write_text(json.dumps({"summary": rows, "by_tag": by_tag(episodes)}, indent=1) + "\n",
                                   encoding="utf-8")
    if args.site:
        Path(args.site).write_text(json.dumps(site_data(episodes, rows), ensure_ascii=False) + "\n", encoding="utf-8")


def _got(answer: dict | None) -> object:
    if not answer:
        return None
    if answer.get("answer_type") == "cannot_answer":
        return "cannot answer"
    if answer.get("answer_type") == "table":
        return [[r.get("label"), r.get("value")] for r in answer.get("table") or []]
    return answer.get("number")


def site_data(episodes: list[dict], rows: list[dict]) -> dict:
    """Everything the results page shows: the summary, and every answer to every question."""
    gold = load_gold()
    answers: dict[str, dict[str, dict]] = defaultdict(dict)
    for ep in episodes:
        answers[ep["question_id"]][f"{ep['model']}|{ep['arm']}"] = {
            "grade": ep["grade"], "got": _got(ep["answer"]),
            "why": (ep["answer"] or {}).get("explanation", "")[:600] or ep["stopped"],
            "sql": [c["args"].get("query") or c["args"] for c in ep["calls"]
                    if c["name"] in ("run_sql", "query_metrics")][-1:],
        }
    return {
        "summary": rows, "by_tag": by_tag(episodes),
        "questions": [{**{k: q[k] for k in ("id", "question", "kind", "tags", "answer", "in_semantic_layer",
                                            "notes")}, "results": answers[q["id"]]} for q in gold],
    }


def main() -> None:
    p = argparse.ArgumentParser(prog="metricbench", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("build", help="download Olist, build the databases and the expected answers")
    r = sub.add_parser("run", help="ask an agent every question")
    r.add_argument("--arm", choices=["sql", "sql+glossary", "semantic"], required=True)
    r.add_argument("--backend", default="claude")
    r.add_argument("--model")
    r.add_argument("--base-url")
    r.add_argument("--price", help="IN,OUT dollars per million tokens, for non-Claude models")
    r.add_argument("--effort", default="medium")
    r.add_argument("--ids", help="comma-separated question ids")
    r.add_argument("--limit", type=int)
    r.add_argument("--workers", type=int, default=4)
    rep = sub.add_parser("report", help="results from runs/")
    rep.add_argument("--json", help="also write the results to this file")
    rep.add_argument("--site", help="write the results page data (every answer) to this file")
    args = p.parse_args()
    if args.cmd == "build":
        from .data import main as build
        from .reference import write
        build()
        write()
    elif args.cmd == "run":
        cmd_run(args)
    else:
        cmd_report(args)


if __name__ == "__main__":
    main()
