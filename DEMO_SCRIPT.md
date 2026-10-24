# Demo video script (~3 minutes)

Record your screen with the terminal open in the project folder. Speak in your own words; this is just the order.

**0:00 – Intro (15s)**
"I built one connected system: a CSS parser, and an agent that answers questions using the parser as its tools."

**0:15 – Milestone 1 (60s)**
1. Open `samples/sample.css`. Point out the traps: braces inside a comment, `"}{;"` inside a string, a `;` inside `url(data:...)`, a circular variable, nested `@media`, an unclosed block at the end.
2. Run: `python -m cssagent parse samples/sample.css`
3. Say: "It returns structure, not text: palette, fonts, variables, media queries, specificity. It didn't choke on the traps, and it reported the unclosed block as a warning."
4. Run the tests: `python -m unittest discover -s tests -t .`  ("38 tests, including fuzzing")

**1:15 – Milestone 2 (75s)**
Run `python -m cssagent demo samples/sample.css` and narrate three moments:
- "What colors are used?" → note the line `tools: get_color_palette` — it called a tool.
- "What is --accent?" / "text-shadow?" → "The data isn't in the file, so it says so instead of guessing."
- "Capital of France?" → "Out of scope, no tool called, honest refusal."

**2:30 – Anti-hallucination + repeatability (40s)**
- "Even if a model lied, a grounding check compares every colour/number/selector in the answer to the tool output. I test this with a deliberately lying brain." (show `tests/test_agent.py::GroundingVerifier`)
- Run `python -m cssagent check samples/sample.css "What colors are used?" --runs 3` → "same tools, same facts, same text."

**3:10 – Close (10s)**
"The README has the write-ups and the limitations. Thank you."

Tip: if you have an Anthropic API key, add one `--llm` run to show the real model calling tools.
