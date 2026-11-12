"""Grading an answer against the expected one.

Numbers count as right within 0.1% (the smallest trap in the benchmark moves the answer by 0.6%).
Rates and percentages are accepted as a fraction or as a percent. Table labels are compared loosely
(case, spaces and punctuation; state names or codes; any way of writing a month or quarter).
"""

from __future__ import annotations

import math
import re
import unicodedata
from typing import Any

REL_TOL = 0.001

STATES = {
    "acre": "ac", "alagoas": "al", "amapa": "ap", "amazonas": "am", "bahia": "ba", "ceara": "ce",
    "distritofederal": "df", "espiritosanto": "es", "goias": "go", "maranhao": "ma", "matogrosso": "mt",
    "matogrossodosul": "ms", "minasgerais": "mg", "para": "pa", "paraiba": "pb", "parana": "pr",
    "pernambuco": "pe", "piaui": "pi", "riodejaneiro": "rj", "riograndedonorte": "rn", "riograndedosul": "rs",
    "rondonia": "ro", "roraima": "rr", "santacatarina": "sc", "saopaulo": "sp", "sergipe": "se",
    "tocantins": "to",
}
MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def key(label: Any) -> str:
    """A label reduced to letters and digits, with states, months and quarters in one canonical form."""
    s = unicodedata.normalize("NFKD", str(label)).encode("ascii", "ignore").decode().lower().strip()
    if m := re.fullmatch(r"(\d{4})\D?q([1-4])|q([1-4])\D*(\d{4})", s):
        return f"{m[1] or m[4]}q{m[2] or m[3]}"
    if m := re.fullmatch(r"(\d{4})-(\d{2})(-\d{2})?(t.*)?", s):
        return f"{m[1]}{m[2]}"
    if m := re.fullmatch(r"([a-z]{3})[a-z]*\.?,?\s+(\d{4})", s):
        if m[1] in MONTHS:
            return f"{m[2]}{MONTHS[m[1]]:02d}"
    s = re.sub(r"[^a-z0-9]", "", s.replace("&", "and"))
    return STATES.get(s, s)


def quarter(label: Any) -> str:
    """For questions within one year: Q1, 2017-Q1 and 2017-01-01 (the quarter's first day) are all q1."""
    s = str(label).lower()
    if m := re.search(r"q([1-4])", s):
        return f"q{m[1]}"
    if m := re.fullmatch(r"\d{4}-(\d{2})(-\d{2})?(t.*)?", s.strip()):
        return f"q{(int(m[1]) - 1) // 3 + 1}"
    return key(label)


def same_number(got: Any, expected: float, percent: bool = False, rel_tol: float = REL_TOL) -> bool:
    if got is None or isinstance(got, bool):
        return False
    try:
        got = float(got)
    except (TypeError, ValueError):
        return False
    candidates = [got, got / 100] if percent else [got]
    return any(math.isclose(c, expected, rel_tol=rel_tol, abs_tol=1e-9) for c in candidates)


def grade(question: dict, answer: dict | None, rel_tol: float = REL_TOL) -> str:
    """correct, wrong, abstained (said it cannot answer an answerable question),
    invented (answered an unanswerable one) or no_answer.

    `rel_tol` is only changed by the analysis, to show how much the score depends on the tolerance."""
    if answer is None:
        return "no_answer"
    kind, expected, percent = question["kind"], question["answer"], question.get("percent", False)
    said_cannot = answer.get("answer_type") == "cannot_answer"
    if kind == "none":
        return "correct" if said_cannot else "invented"
    if said_cannot:
        return "abstained"
    if kind == "number":
        number = answer.get("number")
        if number is None and len(answer.get("table") or []) == 1:
            number = answer["table"][0]["value"]
        return "correct" if same_number(number, expected, percent, rel_tol) else "wrong"
    label = quarter if question.get("key") == "quarter" else key
    rows = [(label(r.get("label")), r.get("value")) for r in answer.get("table") or []]
    want = [(label(k), v) for k, v in expected]
    if question.get("ordered") and len(want) == 1:
        rows = rows[:1]  # "which one is highest": a full ranking with the right one first also answers it
    if len(rows) != len(want):
        return "wrong"
    if not question.get("ordered"):
        rows, want = sorted(rows, key=lambda r: r[0]), sorted(want, key=lambda r: r[0])
    ok = all(a[0] == b[0] and same_number(a[1], b[1], percent, rel_tol) for a, b in zip(rows, want))
    return "correct" if ok else "wrong"
