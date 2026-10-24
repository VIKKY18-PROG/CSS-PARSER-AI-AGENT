# CSS Parser + Grounded Tool-Using Agent

Two milestones, one connected system: **Milestone 1** parses a CSS file into structured data;
**Milestone 2** is an AI agent that answers questions about that file by calling the parser's
analysis as *tools* — never from the model's own knowledge.

```
CSS file ──► parser.py ──► analysis.py ──► tools.py ──► agent ──► grounding check ──► answer
 (M1)         structure      palette, fonts,  8 tools     (M2)       rejects anything
                             specificity…     found=bool              not in tool output
```

No dependencies for the core (Python 3.10+). The optional LLM mode needs `pip install anthropic`.

## Run it

```bash
python -m cssagent demo  samples/sample.css                  # full scripted walkthrough
python -m cssagent parse samples/sample.css [--json]         # Milestone 1 report
python -m cssagent ask   samples/sample.css "What colors are used?"
python -m cssagent chat  samples/sample.css                  # interactive
python -m cssagent check samples/sample.css "What colors are used?" --runs 3   # consistency test
python -m unittest discover -s tests -t .                    # 38 tests

export ANTHROPIC_API_KEY=...  &&  python -m cssagent ask samples/sample.css "..." --llm   # real LLM brain
```

---

## Milestone 1 — Parser: what I extract and why

**What / why (3–4 lines).**
I extract *meaning*, not text: rules with selectors, declarations and line numbers; the colour palette
(normalised, with `var()` resolved so `#FFF` and `#ffffff` count as one colour); fonts (including the
`font:` shorthand, flagged generic vs `@font-face`); CSS variables with their resolved values; media
queries; and selector specificity. These are the things a developer actually asks about a stylesheet
(design tokens, consistency, overrides, responsiveness), and they're far more useful than a token dump.
Anything malformed is recovered from and reported in `warnings` instead of crashing.

**Edge case I thought about (the main one).**
`}`, `{` and `;` appear *inside* strings, comments and `url(data:image/svg+xml;base64,…)`. A regex/`split(";")`
parser silently corrupts these (`content: "}"` ends the rule early; the `;` in a data-URI truncates the
declaration). So the parser scans character by character, tracking strings, parentheses and comments.
Other cases handled and tested: unclosed blocks, stray `}`, missing last `;`, `!important`, nested
`@media`, CSS nesting (`&`), circular `var()` chains (`--a: var(--b); --b: var(--a)`), undefined variables,
and pathological input (fuzzed with 15,000 random strings; deep nesting is capped). Fuzzing found one real
crash (`@ {}`) which is fixed and covered by a regression test.

---

## Milestone 2 — Agent: tools, grounding, repeatability

**Design.** The agent has 8 tools (`get_overview`, `find_rules`, `get_color_palette`, `get_fonts`,
`get_variables`, `resolve_variable`, `get_media_queries`, `top_specific_selectors`). Every tool returns
`found: true/false`, so "the data isn't there" is an explicit value, not something the model has to infer.
Two interchangeable brains share the same tools: an **offline** deterministic router (no API key; used for the demo
and tests) and an **LLM** tool-use loop (`--llm`, temperature 0).

**1. How it avoids making up an answer (3–4 lines).**
Four layers. (a) The system prompt forbids answering from memory and says what to do on `found=false`.
(b) Tools return explicit "not found / undefined / circular" results, so absence is data. (c) A **grounding verifier**
checks every concrete fact in the final answer (colours, selectors, `--vars`, numbers) against the tool results;
an answer that invents a value, or that used *no* tool at all, is discarded and replaced with an honest "I don't have that
information". (d) In LLM mode the verifier's complaint is fed back for one correction attempt before refusing.
Tested with a deliberately lying brain, a lazy brain and a scripted fake LLM.

**2. How I check it gives the same answer twice (3–4 lines).**
`check` runs the same question N times and compares three levels: same tool calls → same set of facts → byte-identical
text. Tools return sorted, deterministic output and the LLM runs at temperature 0, so the facts should not change; but LLM
wording can legitimately vary, so "consistent" is judged on **tools + facts**, and identical text is reported separately
rather than demanded. Offline mode is identical at all three levels (tested on several questions, 3 runs each).

## Limitations (honest)

- The grounding verifier checks that facts are *present* in tool output, not that they're *used correctly* (it would not
  catch swapping two real colours). It is a safety net, not a proof.
- Offline router is keyword-based and intentionally narrow: it refuses what it can't route. Use `--llm` for free-form questions.
- Not implemented: `@import` following, `@layer` cascade ordering, `calc()` evaluation, selector matching against HTML.
- Named colours are only recognised in colour-taking properties; relative colour syntax (`rgb(from …)`) is not evaluated.

## Layout

```
cssagent/parser.py     M1 parser (string/paren-aware scanner, variable resolution)
cssagent/analysis.py   palette, fonts, specificity, overview
cssagent/tools.py      the 8 tools + JSON schemas for LLM tool-use
cssagent/agent.py      brains, agent loop, consistency check
cssagent/grounding.py  anti-hallucination verifier
tests/                 38 unit tests (parser edge cases, tools, agent, verifier, fake-LLM loop)
samples/sample.css     a stylesheet full of deliberate traps
```
