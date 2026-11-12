"""A second look at the saved runs. Nothing here calls a model.

  answerable   accuracy on the 68 questions the data can answer, with "ran out of budget" kept apart from
               "answered wrong", and a 95% interval
  misses       how far the wrong numbers land from the expected one
  tolerance    accuracy at 0.1%, 1% and 5%
  agreement    whether the models miss the same questions

The 10 unanswerable questions are left out of `answerable`: every model refuses them in every arm, so they
add the same ten points everywhere and hide the differences.
"""

from __future__ import annotations

import random
import re
from collections import Counter, defaultdict
from typing import Any, Iterable

from .score import grade

TOLERANCES = (0.001, 0.01, 0.05)
BANDS = ("within 1%", "1% to 5%", "over 5%", "wrong table", "no number")
RESAMPLES = 5000
# A model that leaves more than this share unanswered is not compared question by question.
MAX_UNANSWERED = 0.10

DEFAULT_TURNS = 14


def arm_label(ep: dict) -> str:
    """The arm as reported: a run with a different turn budget is its own row, never mixed with the default."""
    turns = ep.get("max_turns", DEFAULT_TURNS)
    return ep["arm"] if turns == DEFAULT_TURNS else f"{ep['arm']} ({turns} turns)"


_PERIOD = re.compile(r"_\d{4}(-\d{2}|h\d)?")
_STATE = re.compile(r"_(SP|RJ|MG|BA|AL|RR)(?=_|$)")


def family(question_id: str) -> str:
    """Questions that differ only in the period or the state: gmv_2017 and gmv_SP_2018 are both "gmv".

    They are not independent (a model that gets the definition wrong misses all of them), so the interval
    resamples families, not questions."""
    return _STATE.sub("", _PERIOD.sub("", question_id))


def relative_error(question: dict, answer: dict | None) -> float | None:
    """|got - expected| / |expected| for a number question, or None when there is no number to compare."""
    if question["kind"] != "number" or not answer or answer.get("answer_type") == "cannot_answer":
        return None
    got = answer.get("number")
    if got is None and len(answer.get("table") or []) == 1:
        got = answer["table"][0].get("value")
    try:
        got = float(got)
    except (TypeError, ValueError):
        return None
    expected = float(question["answer"])
    candidates = [got, got / 100] if question.get("percent") else [got]
    scale = abs(expected) or 1.0
    return min(abs(c - expected) / scale for c in candidates)


def band(question: dict, answer: dict | None) -> str:
    if question["kind"] == "table":
        return "wrong table"
    err = relative_error(question, answer)
    if err is None:
        return "no number"
    return "within 1%" if err <= 0.01 else "1% to 5%" if err <= 0.05 else "over 5%"


_NUMBER = re.compile(r"\d[\d,]*\.?\d*")


def offers_expected(question: dict, answer: dict | None) -> bool:
    """Whether a wrong number's explanation also states the expected one (as "excluding canceled orders
    gives ..."). A match on the number within the scoring tolerance, so it can miss a rounded figure."""
    if question["kind"] != "number" or not answer:
        return False
    expected = float(question["answer"])
    targets = [expected, expected * 100] if question.get("percent") else [expected]
    for found in _NUMBER.findall(answer.get("explanation") or ""):
        try:
            value = float(found.replace(",", "").rstrip("."))
        except ValueError:
            continue
        if any(abs(value - t) <= 0.001 * abs(t) for t in targets if t):
            return True
    return False


def interval(right_by_family: dict[str, list[bool]], seed: int = 0) -> tuple[float, float]:
    """95% percentile interval for accuracy, resampling question families with replacement."""
    families = sorted(right_by_family)
    rng = random.Random(seed)
    scores = []
    for _ in range(RESAMPLES):
        picked = [x for f in rng.choices(families, k=len(families)) for x in right_by_family[f]]
        scores.append(sum(picked) / len(picked))
    scores.sort()
    return scores[int(0.025 * RESAMPLES)], scores[int(0.975 * RESAMPLES) - 1]


def _groups(episodes: Iterable[dict], gold: dict[str, dict]) -> dict[tuple[str, str], list[dict]]:
    """Answerable episodes of the first run of each model and reported arm."""
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for ep in episodes:
        q = gold.get(ep["question_id"])
        if q and q["kind"] != "none" and ep.get("run", 1) == 1:
            groups[(ep["model"], arm_label(ep))].append(ep)
    return dict(sorted(groups.items()))


def answerable(episodes: Iterable[dict], gold: dict[str, dict]) -> list[dict]:
    rows = []
    for (model, arm), eps in _groups(episodes, gold).items():
        grades = Counter(grade(gold[e["question_id"]], e["answer"]) for e in eps)
        by_family: dict[str, list[bool]] = defaultdict(list)
        for e in eps:
            by_family[family(e["question_id"])].append(grade(gold[e["question_id"]], e["answer"]) == "correct")
        low, high = interval(by_family)
        answered = len(eps) - grades["no_answer"]
        rows.append({
            "model": model, "arm": arm, "questions": len(eps), "correct": grades["correct"],
            "wrong": grades["wrong"], "abstained": grades["abstained"], "no_answer": grades["no_answer"],
            "accuracy": grades["correct"] / len(eps), "low": low, "high": high,
            "accuracy_when_answered": grades["correct"] / answered if answered else None,
        })
    return rows


def misses(episodes: Iterable[dict], gold: dict[str, dict]) -> list[dict]:
    rows = []
    for (model, arm), eps in _groups(episodes, gold).items():
        wrong = [e for e in eps if grade(gold[e["question_id"]], e["answer"]) == "wrong"]
        counts = Counter(band(gold[e["question_id"]], e["answer"]) for e in wrong)
        rows.append({"model": model, "arm": arm, "wrong": len(wrong), **{b: counts[b] for b in BANDS},
                     "wrong_numbers": sum(gold[e["question_id"]]["kind"] == "number" for e in wrong),
                     "offers_expected": sum(offers_expected(gold[e["question_id"]], e["answer"]) for e in wrong)})
    return rows


def tolerance(episodes: Iterable[dict], gold: dict[str, dict]) -> list[dict]:
    """Accuracy per arm at each tolerance, over all models and the answerable questions."""
    by_arm: dict[str, list[dict]] = defaultdict(list)
    for (_, arm), eps in _groups(episodes, gold).items():
        by_arm[arm].extend(eps)
    return [{"arm": arm, "questions": len(eps),
             **{f"{t:g}": sum(grade(gold[e["question_id"]], e["answer"], t) == "correct" for e in eps) / len(eps)
                for t in TOLERANCES}}
            for arm, eps in sorted(by_arm.items())]


def agreement(episodes: Iterable[dict], gold: dict[str, dict]) -> list[dict]:
    """Per arm: on how many questions the models were all right, all wrong, or split."""
    by_arm: dict[str, dict[str, dict[str, bool]]] = defaultdict(dict)
    left_out: dict[str, list[str]] = defaultdict(list)
    for (model, arm), eps in _groups(episodes, gold).items():
        grades = {e["question_id"]: grade(gold[e["question_id"]], e["answer"]) for e in eps}
        if sum(g == "no_answer" for g in grades.values()) / len(grades) > MAX_UNANSWERED:
            left_out[arm].append(model)
            continue
        by_arm[arm][model] = {q: g == "correct" for q, g in grades.items()}
    rows = []
    for arm, models in sorted(by_arm.items()):
        shared = set.intersection(*(set(m) for m in models.values()))
        verdicts = [[models[m][q] for m in sorted(models)] for q in sorted(shared)]
        rows.append({"arm": arm, "models": sorted(models), "left_out": left_out.get(arm, []),
                     "questions": len(shared), "all_right": sum(all(v) for v in verdicts),
                     "all_wrong": sum(not any(v) for v in verdicts),
                     "split": sum(any(v) and not all(v) for v in verdicts)})
    return rows


def analyze(episodes: list[dict], gold_list: list[dict]) -> dict[str, Any]:
    gold = {q["id"]: q for q in gold_list}
    return {"answerable": answerable(episodes, gold), "misses": misses(episodes, gold),
            "tolerance": tolerance(episodes, gold), "agreement": agreement(episodes, gold)}


def _pct(x: float | None) -> str:
    return "–" if x is None else f"{x:.1%}"


def text(result: dict[str, Any]) -> str:
    out = ["Answerable questions (95% interval over question families)",
           f"{'model':20s} {'arm':13s} {'right':>7s} {'accuracy':>9s} {'interval':>15s} {'no answer':>10s} {'when answered':>14s}"]
    for r in result["answerable"]:
        out.append(f"{r['model']:20s} {r['arm']:13s} {r['correct']:3d}/{r['questions']:<3d} {_pct(r['accuracy']):>9s} "
                   f"{_pct(r['low']) + ' – ' + _pct(r['high']):>15s} {r['no_answer']:10d} "
                   f"{_pct(r['accuracy_when_answered']):>14s}")
    out += ["", "Wrong answers, by distance from the expected number",
            f"{'model':20s} {'arm':13s} {'wrong':>6s} " + " ".join(f"{b:>12s}" for b in BANDS)
            + "  explanation also gives the expected number"]
    for r in result["misses"]:
        out.append(f"{r['model']:20s} {r['arm']:13s} {r['wrong']:6d} " + " ".join(f"{r[b]:12d}" for b in BANDS)
                   + f"  {r['offers_expected']} of {r['wrong_numbers']} wrong numbers")
    out += ["", "Accuracy by tolerance (all models, answerable questions)"]
    for r in result["tolerance"]:
        out.append(f"  {r['arm']:13s} " + "  ".join(f"{float(t):.1%}: {_pct(r[f'{t:g}'])}" for t in TOLERANCES))
    out += ["", "Do the models miss the same questions?"]
    for r in result["agreement"]:
        note = f"  (left out, too many unanswered: {', '.join(r['left_out'])})" if r["left_out"] else ""
        out.append(f"  {r['arm']:13s} {len(r['models'])} models, {r['questions']} questions: all right {r['all_right']}, "
                   f"all wrong {r['all_wrong']}, split {r['split']}{note}")
    return "\n".join(out)
