"""CLI:  python -m cssagent <command> FILE.css [...]

  parse FILE [--json]            Milestone 1: structured report (or JSON)
  ask   FILE "question" [--llm]  Milestone 2: one grounded answer + the tools it used
  chat  FILE [--llm]             interactive Q&A
  check FILE "question" [--runs N] [--llm]   same question N times, compare answers
  demo  FILE                     scripted walkthrough (good for the demo video)
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import sys

from . import analysis
from .agent import AnthropicBrain, CSSAgent, OfflineBrain, check_consistency
from .parser import parse_css_file
from .tools import CSSToolbox

DEMO_QUESTIONS = [
    "What colors are used?",
    "Which fonts does it use?",
    "What is --brand?",
    "What is --loop-a?",
    "What is --accent?",            # not defined in the file  -> must say so
    "Does it use text-shadow?",     # not in the file          -> must say so
    "How many rules are there?",
    "What is the capital of France?",  # outside the data       -> must refuse
]


def _agent(sheet, use_llm: bool) -> CSSAgent:
    return CSSAgent(CSSToolbox(sheet), AnthropicBrain() if use_llm else OfflineBrain())


def cmd_parse(args) -> int:
    sheet = parse_css_file(args.file)
    report = {
        "overview": analysis.overview(sheet),
        "colors": analysis.color_palette(sheet),
        "fonts": analysis.font_report(sheet),
        "variables": analysis.variables_report(sheet),
        "media_queries": analysis.media_report(sheet),
        "top_specific_selectors": analysis.top_specific(sheet),
        "rules": [dataclasses.asdict(r) for r in sheet.rules],
    }
    if args.json:
        print(json.dumps(report, indent=2))
        return 0
    tb = CSSToolbox(sheet)
    brain = OfflineBrain()
    for t in ("get_overview", "get_color_palette", "get_fonts", "get_variables", "get_media_queries",
              "top_specific_selectors"):
        print(f"== {t} ==")
        print(brain.render({"tool": t, "args": {}, "result": tb.call(t)}), "\n")
    return 0


def _print_result(r) -> None:
    print(f"Q: {r.question}")
    print("   tools:", ", ".join(f"{t['tool']}({json.dumps(t['args'])})" for t in r.trace) or "(none)")
    print("A:", r.answer.replace("\n", "\n   "))
    if not r.grounded:
        print("   [blocked by grounding check]", "; ".join(r.problems))
    print()


def cmd_ask(args) -> int:
    _print_result(_agent(parse_css_file(args.file), args.llm).ask(args.question))
    return 0


def cmd_chat(args) -> int:
    agent = _agent(parse_css_file(args.file), args.llm)
    print("Ask about the CSS file (empty line to quit).")
    while True:
        try:
            q = input("> ").strip()
        except EOFError:
            break
        if not q:
            break
        _print_result(agent.ask(q))
    return 0


def cmd_check(args) -> int:
    rep = check_consistency(_agent(parse_css_file(args.file), args.llm), args.question, args.runs)
    print(f"Q: {rep['question']}  ({rep['runs']} runs)")
    for k in ("same_tool_calls", "same_facts", "identical_text"):
        print(f"  {k:16}: {'YES' if rep[k] else 'NO'}")
    print("  verdict         :", "CONSISTENT" if rep["consistent"] else "INCONSISTENT")
    return 0 if rep["consistent"] else 1


def cmd_demo(args) -> int:
    sheet = parse_css_file(args.file)
    agent = _agent(sheet, False)
    print("=== Milestone 1: parsed structure ===")
    ov = analysis.overview(sheet)
    print(f"{ov['rule_count']} rules, {ov['declaration_count']} declarations, {ov['variable_count']} variables, "
          f"{len(ov['warnings'])} warning(s)\n")
    print("=== Milestone 2: grounded agent ===\n")
    for q in DEMO_QUESTIONS:
        _print_result(agent.ask(q))
    print("=== Same question twice ===")
    cmd_check(argparse.Namespace(file=args.file, question="What colors are used?", runs=2, llm=False))
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="cssagent", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("parse"); a.add_argument("file"); a.add_argument("--json", action="store_true"); a.set_defaults(fn=cmd_parse)
    a = sub.add_parser("ask"); a.add_argument("file"); a.add_argument("question"); a.add_argument("--llm", action="store_true"); a.set_defaults(fn=cmd_ask)
    a = sub.add_parser("chat"); a.add_argument("file"); a.add_argument("--llm", action="store_true"); a.set_defaults(fn=cmd_chat)
    a = sub.add_parser("check"); a.add_argument("file"); a.add_argument("question"); a.add_argument("--runs", type=int, default=2)
    a.add_argument("--llm", action="store_true"); a.set_defaults(fn=cmd_check)
    a = sub.add_parser("demo"); a.add_argument("file"); a.set_defaults(fn=cmd_demo)
    args = p.parse_args(argv)
    try:
        return args.fn(args)
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
