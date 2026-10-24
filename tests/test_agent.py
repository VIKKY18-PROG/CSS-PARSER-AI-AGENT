import json
import os
import unittest
from types import SimpleNamespace as NS

from cssagent import grounding
from cssagent.agent import AnthropicBrain, CSSAgent, OfflineBrain, check_consistency
from cssagent.parser import parse_css, parse_css_file
from cssagent.tools import CSSToolbox

SAMPLE = os.path.join(os.path.dirname(__file__), "..", "samples", "sample.css")


def make_agent(brain=None):
    return CSSAgent(CSSToolbox(parse_css_file(SAMPLE)), brain)


class Tools(unittest.TestCase):
    def setUp(self):
        self.tb = CSSToolbox(parse_css_file(SAMPLE))

    def test_not_found_is_explicit(self):
        r = self.tb.call("find_rules", {"property": "text-shadow"})
        self.assertFalse(r["found"])
        self.assertEqual(r["match_count"], 0)

    def test_bad_tool_and_args_do_not_crash(self):
        self.assertIn("error", self.tb.call("nope"))
        self.assertIn("error", self.tb.call("find_rules", {"bogus": 1}))

    def test_resolve_variable_accepts_name_without_dashes(self):
        self.assertEqual(self.tb.call("resolve_variable", {"name": "brand"})["value"], "#3B82F6")


class OfflineAgent(unittest.TestCase):
    def setUp(self):
        self.agent = make_agent()

    def test_uses_a_tool_and_is_grounded(self):
        r = self.agent.ask("What colors are used?")
        self.assertTrue(r.grounded)
        self.assertEqual(r.trace[0]["tool"], "get_color_palette")
        self.assertIn("#3b82f6", r.answer)

    def test_missing_data_is_reported_not_invented(self):
        for q, must in [("What is --accent?", "not defined"), ("Does it use text-shadow?", "no matching"),
                        ("Which selectors use box-shadow?", "no matching")]:
            a = self.agent.ask(q).answer.lower()
            self.assertIn(must, a, q)

    def test_out_of_scope_refused_without_tools(self):
        r = self.agent.ask("What is the capital of France?")
        self.assertEqual(r.answer, grounding.REFUSAL)
        self.assertEqual(r.trace, [])

    def test_circular_variable_reported(self):
        self.assertIn("circular", self.agent.ask("what is --loop-a").answer)

    def test_counts_match_parser(self):
        sheet = parse_css_file(SAMPLE)
        r = self.agent.ask("How many rules are there?")
        self.assertIn(f"{len(sheet.rules)} rules", r.answer)

    def test_same_answer_twice(self):
        for q in ["What colors are used?", "Which fonts does it use?", "list the media queries", "How many rules?"]:
            rep = check_consistency(self.agent, q, runs=3)
            self.assertTrue(rep["consistent"] and rep["identical_text"], q)


class GroundingVerifier(unittest.TestCase):
    def test_rejects_invented_values(self):
        trace = [{"tool": "get_color_palette", "args": {}, "result": {"colors": [{"color": "#3b82f6", "used_count": 2}]}}]
        ok, problems = grounding.verify("The colours are #3b82f6 and #ff0000.", "colours?", trace)
        self.assertFalse(ok)
        self.assertTrue(any("#ff0000" in p for p in problems))

    def test_rejects_invented_numbers_but_not_substrings(self):
        trace = [{"tool": "get_overview", "args": {}, "result": {"rule_count": 768}}]
        self.assertTrue(grounding.verify("There are 768 rules.", "q", trace)[0])
        self.assertFalse(grounding.verify("There are 7 rules.", "q", trace)[0])

    def test_rejects_answer_with_no_tool_call(self):
        ok, problems = grounding.verify("The main colour is blue.", "colour?", [])
        self.assertFalse(ok)

    def test_short_and_long_hex_match(self):
        trace = [{"tool": "t", "args": {}, "result": {"c": "#aabbcc"}}]
        self.assertTrue(grounding.verify("It is #abc.", "q", trace)[0])

    def test_hallucinating_brain_is_blocked(self):
        class Liar:
            name = "liar"
            def run(self, q, tb):
                r = tb.call("get_color_palette")
                return "The palette is #3b82f6 and #123456.", [{"tool": "get_color_palette", "args": {}, "result": r}]
        res = make_agent(Liar()).ask("colours?")
        self.assertFalse(res.grounded)
        self.assertEqual(res.answer, grounding.REFUSAL)
        self.assertIn("#123456", res.raw_answer)

    def test_brain_that_skips_tools_is_blocked(self):
        class Lazy:
            name = "lazy"
            def run(self, q, tb):
                return "Probably #ffffff.", []
        self.assertFalse(make_agent(Lazy()).ask("colours?").grounded)


class FakeLLM:
    """Stands in for the Anthropic client so the LLM loop is testable offline."""
    def __init__(self, script):
        self.script, self.calls = list(script), 0
        self.messages = NS(create=self._create)

    def _create(self, **kw):
        self.calls += 1
        return self.script.pop(0)


def tool_use(name, args, id="t1"):
    return NS(content=[NS(type="tool_use", name=name, input=args, id=id)])


def text(t):
    return NS(content=[NS(type="text", text=t)])


class LLMBrain(unittest.TestCase):
    def test_tool_loop_and_grounded_answer(self):
        llm = FakeLLM([tool_use("resolve_variable", {"name": "--primary"}), text("--primary is #3B82F6.")])
        res = make_agent(AnthropicBrain(client=llm)).ask("what is --primary")
        self.assertTrue(res.grounded)
        self.assertEqual(res.trace[0]["tool"], "resolve_variable")

    def test_hallucination_gets_one_correction_then_passes(self):
        llm = FakeLLM([tool_use("resolve_variable", {"name": "--primary"}), text("--primary is #000000."),
                       tool_use("resolve_variable", {"name": "--primary"}), text("--primary is #3B82F6.")])
        res = make_agent(AnthropicBrain(client=llm)).ask("what is --primary")
        self.assertTrue(res.grounded)
        self.assertIn("#3B82F6", res.answer)

    def test_persistent_hallucination_is_refused(self):
        llm = FakeLLM([tool_use("get_overview", {}), text("It has 999 rules."),
                       tool_use("get_overview", {}), text("It has 999 rules.")])
        res = make_agent(AnthropicBrain(client=llm)).ask("how many rules")
        self.assertFalse(res.grounded)
        self.assertEqual(res.answer, grounding.REFUSAL)

    def test_runaway_tool_loop_stops(self):
        llm = FakeLLM([tool_use("get_overview", {})] * 20)
        res = make_agent(AnthropicBrain(client=llm, max_steps=3)).ask("loop")
        self.assertEqual(res.answer, grounding.REFUSAL)


if __name__ == "__main__":
    unittest.main()
