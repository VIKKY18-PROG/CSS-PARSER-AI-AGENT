"""Grounding verifier: the safety net against made-up answers.

Idea: every concrete fact in a final answer (colour, selector, --variable, number) must be
traceable to a tool result (or to the user's own question). If something in the answer can't
be traced, or the answer used no tool at all, it is rejected and replaced with an honest refusal.

This is a heuristic (it checks *presence*, not meaning) - see README "Limitations".
"""
from __future__ import annotations

import json
import re

REFUSAL = ("I don't have that information: the CSS file and my tools don't contain data to answer it, "
           "so I won't guess.")

_FACT_PATTERNS = [
    re.compile(r"#[0-9A-Za-z][\w-]*"),                    # hex colours and #id selectors
    re.compile(r"--[A-Za-z][\w-]*"),                      # custom properties
    re.compile(r"(?<![\w)])\.[A-Za-z_][\w-]*"),           # .class selectors
    re.compile(r"(?<![\w#.-])\d+(?:\.\d+)?"),             # numbers
]


def extract_facts(text: str) -> set[str]:
    facts: set[str] = set()
    for pat in _FACT_PATTERNS:
        for m in pat.finditer(text):
            token = m.group(0).rstrip(".").lower()
            if token.startswith("#") and re.fullmatch(r"#[0-9a-f]{3,8}", token):
                token = _expand_hex(token)
            facts.add(token)
    return facts


def _expand_hex(h: str) -> str:
    if len(h) in (4, 5):
        return "#" + "".join(c * 2 for c in h[1:])
    return h


def is_refusal(answer: str) -> bool:
    return answer.strip().startswith(("I don't have", "I do not have", "I can't answer", "I cannot answer"))


def verify(answer: str, question: str, trace: list[dict]) -> tuple[bool, list[str]]:
    """Returns (grounded, problems)."""
    if is_refusal(answer):
        return True, []
    problems: list[str] = []
    if not trace:
        problems.append("no tool was called, so the answer can only come from the model's own knowledge")
        return False, problems
    evidence = json.dumps([t["result"] for t in trace], sort_keys=True).lower()
    evidence_facts = extract_facts(evidence) | {f for f in extract_facts(question)}
    # also accept hex written in either short or long form, and raw substrings of evidence
    for fact in sorted(extract_facts(answer)):
        is_number = re.fullmatch(r"\d+(?:\.\d+)?", fact) is not None
        # numbers must match a whole token ("7" must not pass because "768" contains it)
        if fact in evidence_facts or (not is_number and fact in evidence):
            continue
        problems.append(f"'{fact}' does not appear in any tool result")
    return (not problems), problems
