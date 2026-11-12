"""metricbench: run the questions through an agent and grade the answers.

  metricbench build                                  download Olist and build both databases
  metricbench run --arm semantic --backend claude --model claude-haiku-5-5
  metricbench run --arm sql --backend openai --model gemini-3.8-flash \\
      --base-url https://generativelanguage.googleapis.com/v1beta/openai/ --price 0.75,3.75
  metricbench report                                 results table from every file in runs/
  metricbench analyze                                a second look at the saved runs (calls no model)
  metricbench causes                                 which reading of the question explains each wrong answer
                                                     (needs `metricbench build`; calls no model)
  metricbench estimate --arm sql+rule --runs 3       what a run would cost, from the saved runs (calls no model)
"""

from __future__ import annotations

import argparse
import json
import threading
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .analysis import DEFAULT_TURNS, arm_label
from .data import ROOT

RUNS = ROOT / "runs"


def load_gold() -> list[dict]:
    from .reference import GOLD
    return json.loads(GOLD.read_text(encoding="utf-8"))


DEFAULT_TOOL_CALLS = 20


def run_file(model: str, arm: str, run: int = 1, max_turns: int = DEFAULT_TURNS) -> Path:
    """runs/<model>__<arm>.jsonl for the first run with the default budget; later runs add __r2, __r3...
    and a different turn budget adds __t<turns>, so no run overwrites or mixes with another."""
    budget = f"__t{max_turns}" if max_turns != DEFAULT_TURNS else ""
    repeat = f"__r{run}" if run != 1 else ""
    return RUNS / f"{model}__{arm.replace('+', '-')}{budget}{repeat}.jsonl"


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
    from .agent import Budget, ask
    from .backends import make
    from .score import grade

    price = tuple(float(x) for x in args.price.split(",")) if args.price else None
    backend = make(args.backend, args.model, args.base_url, price, args.effort)
    questions = load_gold()
    if args.ids:
        questions = [q for q in questions if q["id"] in set(args.ids.split(","))]
    if args.limit:
        questions = questions[: args.limit]
    budget = Budget(max_turns=args.max_turns, max_tool_calls=args.max_tool_calls)
    out = run_file(backend.model, args.arm, args.run, args.max_turns)
    out.parent.mkdir(exist_ok=True)
    done = set()
    if out.exists():
        done = {json.loads(line)["question_id"] for line in out.read_text(encoding="utf-8").splitlines() if line}
    todo = [q for q in questions if q["id"] not in done]
    semantic = LockedSemantic() if args.arm == "semantic" else None
    lock = threading.Lock()
    spent, skipped = [0.0], [0]
    if args.max_cost is not None and not priced(backend):
        raise SystemExit(f"--max-cost needs prices for {backend.model}: pass --price IN,OUT. Without them every "
                         "question counts as $0 and the cap would never stop the run.")
    print(f"{backend.model} · {args.arm}: {len(todo)} questions ({len(done)} already in {out.name})")

    def one(q: dict) -> None:
        with lock:
            if args.max_cost is not None and spent[0] >= args.max_cost:
                skipped[0] += 1
                return
        ep = ask(q, args.arm, backend, budget, semantic=semantic).to_dict()
        ep["run"], ep["max_turns"], ep["max_tool_calls"] = args.run, budget.max_turns, budget.max_tool_calls
        ep["question"] = q["question"]  # the exact wording asked, so a later rewording can be detected
        ep["grade"] = grade(q, ep["answer"])
        with lock:
            spent[0] += ep["cost_usd"]
            with out.open("a", encoding="utf-8") as f:
                f.write(json.dumps(ep, ensure_ascii=False, default=str) + "\n")
            print(f"  {q['id']:32s} {ep['grade']:10s} {ep['turns']:2d} turns  ${ep['cost_usd']:.4f}  {ep['stopped']}")

    with ThreadPoolExecutor(args.workers) as pool:
        list(pool.map(one, todo))
    print(f"spent ${spent[0]:.4f} in this run")
    if skipped[0]:
        print(f"stopped at --max-cost ${args.max_cost:.2f}: {skipped[0]} questions not asked. "
              "Run the same command again to continue from here.")


def priced(backend) -> bool:
    """Whether this backend can tell what a question cost."""
    from .backends import PRICES
    return backend.model in PRICES or any(getattr(backend, "price", (0.0, 0.0)))


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
                    ep.setdefault("run", 1)
                    episodes.append(ep)
    return episodes


def summarize(episodes: list[dict]) -> list[dict]:
    gold = {q["id"]: q for q in load_gold()}
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for ep in episodes:
        groups[(ep["model"], arm_label(ep))].append(ep)
    rows = []
    for (model, arm), eps in sorted(groups.items()):
        per_run: dict[int, list[dict]] = defaultdict(list)
        for e in eps:
            per_run[e["run"]].append(e)
        run_accuracy = [sum(e["grade"] == "correct" for e in r) / len(r) for _, r in sorted(per_run.items())]
        answerable = [e for e in eps if gold[e["question_id"]]["kind"] != "none"]
        in_layer = [e for e in answerable if gold[e["question_id"]]["in_semantic_layer"]]
        unanswerable = [e for e in eps if gold[e["question_id"]]["kind"] == "none"]
        g = Counter(e["grade"] for e in eps)
        rows.append({
            "model": model, "arm": arm, "questions": len(eps) // len(per_run),
            "accuracy": g["correct"] / len(eps),  # over every run; equal to the run's own with a single run
            "runs": len(per_run), "accuracy_min": min(run_accuracy), "accuracy_max": max(run_accuracy),
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
            acc[tag][arm_label(ep)].append(ep["grade"] == "correct")
    return {tag: {arm: sum(v) / len(v) for arm, v in arms.items()} for tag, arms in sorted(acc.items())}


def cmd_report(args) -> None:
    episodes = load_runs()
    rows = summarize(episodes)
    print(f"{'model':22s} {'arm':13s} {'n':>3s} {'all':>6s} {'answ.':>6s} {'in SL':>6s} {'refuse':>6s} {'cost':>8s}")
    for r in rows:
        spread = f"  {r['runs']} runs, {r['accuracy_min']:.1%} to {r['accuracy_max']:.1%}" if r["runs"] > 1 else ""
        print(f"{r['model']:22s} {r['arm']:13s} {r['questions']:3d} {r['accuracy']:6.1%} {r['answerable_accuracy']:6.1%} "
              f"{r['in_layer_accuracy']:6.1%} {r['refused_unanswerable']:6.1%} ${r['cost_usd']:7.3f}{spread}")
    print("\naccuracy by trap and arm (all models)")
    for tag, arms in by_tag(episodes).items():
        print(f"  {tag:18s} " + "  ".join(f"{a}={v:5.1%}" for a, v in sorted(arms.items())))
    if args.json:
        Path(args.json).write_text(json.dumps({"summary": rows, "by_tag": by_tag(episodes)}, indent=1) + "\n",
                                   encoding="utf-8")
    if args.site:
        Path(args.site).write_text(json.dumps(site_data(episodes, rows), ensure_ascii=False) + "\n", encoding="utf-8")


def cmd_analyze(args) -> None:
    from .analysis import analyze, text
    result = analyze(load_runs(), load_gold())
    print(text(result))
    if args.json:
        Path(args.json).write_text(json.dumps(result, indent=1) + "\n", encoding="utf-8")


def cmd_causes(args) -> None:
    from .causes import classify, listing, markdown, text, variant_answers
    from .data import RAW_DB
    if not RAW_DB.exists():
        raise SystemExit("the raw database is missing: run `metricbench build` first")
    _, by_combo = variant_answers(RAW_DB)
    result = classify(load_runs(), load_gold(), by_combo)
    print(text(result))
    if args.list:
        print("\nEvery wrong answer (model, arm, question, cause):\n" + listing(result))
    if args.json:
        Path(args.json).write_text(json.dumps(result, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    if args.summary:
        with Path(args.summary).open("a", encoding="utf-8") as f:
            f.write(markdown(result))


# Which saved arm a planned arm should cost about the same as.
COST_LIKE = {"sql+rule": "sql"}


def estimate(episodes: list[dict], arm: str, models: list[str] | None, runs: int, questions: int) -> list[dict]:
    """Expected cost of `runs` runs of `arm`, from what the same model spent per question on that arm (or on
    the nearest one) in the saved runs. A larger turn budget costs more than this says."""
    spent: dict[tuple[str, str], list[float]] = defaultdict(list)
    for ep in episodes:
        if ep.get("max_turns", DEFAULT_TURNS) == DEFAULT_TURNS:
            spent[(ep["model"], ep["arm"])].append(ep["cost_usd"])
    known = sorted({m for m, _ in spent})
    rows = []
    for model in models or known:
        basis = arm if (model, arm) in spent else COST_LIKE.get(arm, arm)
        costs = spent.get((model, basis))
        per_question = sum(costs) / len(costs) if costs else None
        rows.append({"model": model, "arm": arm, "basis": basis if costs else None, "per_question": per_question,
                     "total": per_question * questions * runs if costs else None})
    return rows


def cmd_estimate(args) -> None:
    questions = len(load_gold())
    rows = estimate(load_runs(), args.arm, args.models.split(",") if args.models else None, args.runs, questions)
    print(f"{args.runs} run(s) of {args.arm}, {questions} questions each. Estimate only: nothing is sent to a model.")
    for r in rows:
        if r["total"] is None:
            print(f"  {r['model']:22s} no saved run to estimate from")
        else:
            print(f"  {r['model']:22s} ${r['total']:7.2f}  (${r['per_question']:.4f} per question, as in its {r['basis']} run)")
    known = [r["total"] for r in rows if r["total"] is not None]
    print(f"  {'total':22s} ${sum(known):7.2f}")


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
        if ep["run"] != 1:
            continue  # the page shows one answer per model and arm: the first run's
        answers[ep["question_id"]][f"{ep['model']}|{arm_label(ep)}"] = {
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
    r.add_argument("--arm", choices=["sql", "sql+rule", "sql+glossary", "semantic"], required=True)
    r.add_argument("--backend", default="claude")
    r.add_argument("--model")
    r.add_argument("--base-url")
    r.add_argument("--price", help="IN,OUT dollars per million tokens, for non-Claude models")
    r.add_argument("--effort", default="medium")
    r.add_argument("--ids", help="comma-separated question ids")
    r.add_argument("--limit", type=int)
    r.add_argument("--workers", type=int, default=4)
    r.add_argument("--run", type=int, default=1, help="run number, to repeat a run: 2, 3... are saved apart")
    r.add_argument("--max-turns", type=int, default=DEFAULT_TURNS, help="turn budget per question")
    r.add_argument("--max-tool-calls", type=int, default=DEFAULT_TOOL_CALLS, help="tool call budget per question")
    r.add_argument("--max-cost", type=float, help="stop asking new questions once this run has spent this many "
                   "dollars; questions already in flight finish, so it can go over by a few questions")
    rep = sub.add_parser("report", help="results from runs/")
    rep.add_argument("--json", help="also write the results to this file")
    rep.add_argument("--site", help="write the results page data (every answer) to this file")
    an = sub.add_parser("analyze", help="a second look at the saved runs; calls no model")
    an.add_argument("--json", help="also write the analysis to this file")
    ca = sub.add_parser("causes", help="which reading of the question explains each wrong answer; calls no model")
    ca.add_argument("--json", help="also write every wrong answer's cause to this file")
    ca.add_argument("--list", action="store_true", help="also print every wrong answer with its cause")
    ca.add_argument("--summary", help="append a Markdown summary to this file (the CI job summary)")
    est = sub.add_parser("estimate", help="what a run would cost, from the saved runs; calls no model")
    est.add_argument("--arm", choices=["sql", "sql+rule", "sql+glossary", "semantic"], required=True)
    est.add_argument("--models", help="comma-separated; default: every model with a saved run")
    est.add_argument("--runs", type=int, default=1)
    args = p.parse_args()
    if args.cmd == "build":
        from .data import main as build
        from .reference import write
        build()
        write()
    elif args.cmd == "run":
        cmd_run(args)
    elif args.cmd == "analyze":
        cmd_analyze(args)
    elif args.cmd == "causes":
        cmd_causes(args)
    elif args.cmd == "estimate":
        cmd_estimate(args)
    else:
        cmd_report(args)


if __name__ == "__main__":
    main()
