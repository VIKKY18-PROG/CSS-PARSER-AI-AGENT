"""The agent (Milestone 2).

Question -> choose tools -> read tool results -> answer ONLY from those results -> verify.

Two interchangeable "brains" use the same tools and the same verifier:
  * OfflineBrain  - deterministic keyword router + answer templates. Needs no API key, so the
                    demo and the tests are fully reproducible. It refuses what it can't route.
  * AnthropicBrain - a real LLM tool-use loop (temperature 0) for free-form questions.
Whichever brain is used, the final answer must pass `grounding.verify` or it is replaced by a refusal.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field

from . import grounding
from .tools import CSSToolbox, TOOL_SPECS

COMMON_PROPERTIES = set("""align-items animation background background-color border border-radius box-shadow color
content cursor display filter flex flex-direction font font-family font-size font-style font-weight gap grid height
justify-content letter-spacing line-height margin max-width min-height opacity outline overflow padding position
text-align text-decoration text-transform transform transition width z-index""".split())

SYSTEM_PROMPT = """You answer questions about ONE CSS file using ONLY the provided tools.
Rules:
1. Never answer from your own knowledge. Call a tool first; every fact (colour, selector, number, variable) \
in your answer must come from a tool result.
2. If a tool returns found=false, an error, status undefined/circular, or the data is not there, say plainly \
that the file does not contain it. Do not guess, estimate or fill gaps.
3. Use counts exactly as the tools report them. Do not compute new numbers.
4. If the question is not about this CSS file, reply: "I don't have that information: the CSS file does not cover it."
5. Be brief."""


@dataclass
class AgentResult:
    question: str
    answer: str
    trace: list[dict] = field(default_factory=list)  # [{"tool","args","result"}]
    grounded: bool = True
    problems: list[str] = field(default_factory=list)
    brain: str = "offline"
    raw_answer: str | None = None  # what the brain said before verification replaced it


# ------------------------------------------------------------------ offline brain
class OfflineBrain:
    name = "offline"

    def run(self, question: str, tb: CSSToolbox) -> tuple[str, list[dict]]:
        calls = self.route(question, tb)
        if not calls:
            return grounding.REFUSAL, []
        trace = [{"tool": t, "args": a, "result": tb.call(t, a)} for t, a in calls]
        return "\n".join(self.render(x) for x in trace), trace

    # -- routing -------------------------------------------------------------
    def route(self, question: str, tb: CSSToolbox) -> list[tuple[str, dict]]:
        q = question.lower()
        words = set(re.findall(r"[a-z][a-z-]*", q))
        known_props = COMMON_PROPERTIES | {d.prop for r in tb.sheet.rules for d in r.declarations if not d.prop.startswith("--")}
        var = re.search(r"--[\w-]+", q)
        asks_where = bool(re.search(r"\b(which|what|where|find|list|show)\b", q)) and \
            bool(re.search(r"\b(select\w*|rules?|elements?|class\w*|use[sd]?|using|set|declare\w*)\b", q))
        if var:
            return [("resolve_variable", {"name": var.group(0)})]
        if "important" in q:
            return [("find_rules", {"important_only": True})]
        if re.search(r"\bspecific", q):
            return [("top_specific_selectors", {"n": 5})]
        if re.search(r"\b(media|breakpoints?|responsive)\b", q):
            return [("get_media_queries", {})]
        props = [w for w in re.findall(r"[a-z]+(?:-[a-z]+)*", q) if w in known_props and w not in ("color", "font", "content", "filter")]
        # any hyphenated word ("text-shadow") is treated as a property name and looked up in the file;
        # if the file doesn't have it the tool says found=false and we report "not present" (not a guess)
        hyphen_props = [w for w in re.findall(r"(?<![\w-])[a-z]+(?:-[a-z]+)+(?![\w-])", q)]
        props = props + [h for h in hyphen_props if h not in props]
        if (asks_where or hyphen_props) and (props or "grid" in words or "flex" in words):
            if hyphen_props or props:
                return [("find_rules", {"property": (hyphen_props or props)[0]})]
        if "grid" in words or "flexbox" in words or "flex" in words:
            kw = "grid" if "grid" in words else "flex"
            return [("find_rules", {"property": "display", "value_contains": kw})]
        if re.search(r"\b(variables?|custom propert\w+)\b", q):
            return [("get_variables", {})]
        if re.search(r"\b(colou?rs?|palette|hex|rgb)\b", q):
            return [("get_color_palette", {})]
        if re.search(r"\b(fonts?|typefaces?|typography|font-famil\w+)\b", q):
            return [("get_fonts", {})]
        if re.search(r"\b(how many|count|summary|summari[sz]e|overview|stats|statistics|warnings?|keyframes?|animations?|imports?)\b", q):
            return [("get_overview", {})]
        if props:
            return [("find_rules", {"property": props[0]})]
        return []

    # -- rendering: every number/selector printed here is copied from a tool result ----
    def render(self, step: dict) -> str:
        t, a, r = step["tool"], step["args"], step["result"]
        if r.get("error"):
            return f"The tool reported an error: {r['error']}"
        if t == "get_overview":
            lines = [f"The file has {r['rule_count']} rules, {r['unique_selector_count']} unique selectors and "
                     f"{r['declaration_count']} declarations ({r['important_count']} marked !important, "
                     f"{r['variable_count']} custom properties, {r['media_query_count']} media queries)."]
            if r["keyframes"]:
                lines.append("Keyframes: " + ", ".join(r["keyframes"]) + ".")
            if r["imports"]:
                lines.append("Imports: " + ", ".join(r["imports"]) + ".")
            for d in r["conflicting_duplicate_declarations"]:
                lines.append(f"- {d['selector']} (line {d['line']}) declares {d['property']} more than once: "
                             + " then ".join(d["values"]))
            for w in r["warnings"]:
                lines.append(f"- warning: {w}")
            return "\n".join(lines)
        if t == "find_rules":
            if not r["found"]:
                return "I found no matching declaration in this file, so there is nothing to report."
            lines = [f"{r['match_count']} matching declaration(s):"]
            for m in r["matches"]:
                where = f" in {m['media']}" if m.get("media") else ""
                imp = " !important" if m["important"] else ""
                lines.append(f"- {m['selector']} (line {m['line']}){where}: {m['property']}: {m['value']}{imp}")
            if r["truncated"]:
                lines.append(f"(showing {r['returned']} of {r['match_count']})")
            return "\n".join(lines)
        if t == "get_color_palette":
            if not r["found"]:
                return "I found no colour values in this file."
            lines = [f"{r['unique_colors']} unique colours:"]
            for c in r["colors"]:
                extra = f", defined as {', '.join(c['defined_as_variables'])}" if c["defined_as_variables"] else ""
                lines.append(f"- {c['color']}: used {c['used_count']} time(s){extra}")
            return "\n".join(lines)
        if t == "get_fonts":
            if not r["found"]:
                return "I found no font declarations in this file."
            lines = ["Primary fonts: " + (", ".join(r["primary_fonts"]) or "none") + "."]
            for f in r["families"]:
                tag = "generic" if f["generic"] else ("@font-face" if f["declared_with_font_face"] else "not declared in file")
                lines.append(f"- {f['family']}: used {f['used_count']} time(s) ({tag})")
            return "\n".join(lines)
        if t == "get_variables":
            if not r["found"]:
                return "I found no custom properties (CSS variables) in this file."
            lines = [f"{r['variable_count']} custom properties:"]
            for v in r["variables"]:
                val = v["value"] if v["value"] is not None else f"({v['status']})"
                lines.append(f"- {v['name']}: {val}")
            return "\n".join(lines)
        if t == "resolve_variable":
            if r["status"] == "undefined":
                return f"{r['name']} is not defined in this file."
            if r["status"] == "circular":
                return f"{r['name']} is defined circularly ({' -> '.join(r['chain'])}), so it has no usable value."
            if r["status"] == "scoped_only":
                return f"{r['name']} is only defined inside: {', '.join(r['defined_in'])} (not globally)."
            via = f" (via {' -> '.join(r['chain'])})" if len(r["chain"]) > 1 else ""
            return f"{r['name']} resolves to {r['value']}{via}."
        if t == "get_media_queries":
            if not r["found"]:
                return "This file has no media queries."
            return "\n".join(["Media queries:"] + [f"- {q['query']}: {q['rule_count']} rule(s)" for q in r["queries"]])
        if t == "top_specific_selectors":
            lines = ["Most specific selectors ([ids, classes, elements]):"]
            lines += [f"- {s['selector']}: {s['specificity']}" for s in r["selectors"]]
            return "\n".join(lines)
        return json.dumps(r)


# ---------------------------------------------------------------- LLM brain
class AnthropicBrain:
    name = "llm"

    def __init__(self, model: str | None = None, client=None, max_steps: int = 6):
        if client is None:
            try:
                import anthropic
            except ImportError as e:  # pragma: no cover
                raise RuntimeError("pip install anthropic to use --llm") from e
            if not os.environ.get("ANTHROPIC_API_KEY"):
                raise RuntimeError("Set ANTHROPIC_API_KEY to use --llm")
            client = anthropic.Anthropic()
        self.client = client
        self.model = model or os.environ.get("CSSAGENT_MODEL", "claude-sonnet-5-5")
        self.max_steps = max_steps

    def run(self, question: str, tb: CSSToolbox, feedback: str | None = None) -> tuple[str, list[dict]]:
        messages = [{"role": "user", "content": question}]
        trace: list[dict] = []
        for _ in range(self.max_steps):
            resp = self.client.messages.create(model=self.model, max_tokens=1024, temperature=0,
                                               system=SYSTEM_PROMPT + (f"\n\nCorrection: {feedback}" if feedback else ""),
                                               tools=TOOL_SPECS, messages=messages)
            uses = [b for b in resp.content if getattr(b, "type", None) == "tool_use"]
            if not uses:
                return "".join(getattr(b, "text", "") for b in resp.content).strip(), trace
            messages.append({"role": "assistant", "content": resp.content})
            results = []
            for u in uses:
                out = tb.call(u.name, dict(u.input))
                trace.append({"tool": u.name, "args": dict(u.input), "result": out})
                results.append({"type": "tool_result", "tool_use_id": u.id, "content": json.dumps(out, sort_keys=True)})
            messages.append({"role": "user", "content": results})
        return grounding.REFUSAL, trace


# ---------------------------------------------------------------------- agent
class CSSAgent:
    def __init__(self, toolbox: CSSToolbox, brain=None):
        self.tb = toolbox
        self.brain = brain or OfflineBrain()

    def ask(self, question: str) -> AgentResult:
        answer, trace = self.brain.run(question, self.tb)
        ok, problems = grounding.verify(answer, question, trace)
        if not ok and isinstance(self.brain, AnthropicBrain):  # one self-correction attempt
            answer2, trace2 = self.brain.run(question, self.tb, feedback="; ".join(problems))
            ok2, problems2 = grounding.verify(answer2, question, trace2)
            if ok2:
                return AgentResult(question, answer2, trace2, True, [], self.brain.name)
        if ok:
            return AgentResult(question, answer, trace, True, [], self.brain.name)
        return AgentResult(question, grounding.REFUSAL, trace, False, problems, self.brain.name, raw_answer=answer)


# ----------------------------------------------------------- consistency check
def check_consistency(agent: CSSAgent, question: str, runs: int = 2) -> dict:
    """Ask the same question `runs` times and compare. Three levels, strictest last:
    same tool calls -> same set of facts -> byte-identical text."""
    results = [agent.ask(question) for _ in range(runs)]
    sigs = [json.dumps([(t["tool"], t["args"]) for t in r.trace], sort_keys=True) for r in results]
    facts = [grounding.extract_facts(r.answer) for r in results]
    same_tools = len(set(sigs)) == 1
    same_facts = all(f == facts[0] for f in facts)
    same_text = len({r.answer for r in results}) == 1
    return {"question": question, "runs": runs, "same_tool_calls": same_tools, "same_facts": same_facts,
            "identical_text": same_text, "consistent": same_tools and same_facts, "answers": [r.answer for r in results]}
