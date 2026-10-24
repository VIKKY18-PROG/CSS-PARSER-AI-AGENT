"""CSS parser (Milestone 1).

Reads a CSS file and returns *structured* information, not raw text:
rules (with selectors, declarations, line numbers, media context), custom
properties (CSS variables) with resolution, @font-face, @keyframes, @import,
media queries and a list of warnings for anything malformed.

Design choice: instead of splitting on "{", "}" and ";" with regexes (which breaks
on `content: "}"`, `url(data:...;base64,...)`, comments containing braces, nested
@media, ...), the parser scans character by character and tracks strings and
parentheses. Real-world CSS is messy, so it never raises: it recovers and records
a warning.
"""
from __future__ import annotations

import bisect
import re
from dataclasses import dataclass, field

MAX_DEPTH = 32
CONDITIONAL_AT_RULES = {"media", "supports", "container", "layer", "scope", "document"}


# --------------------------------------------------------------------------- data
@dataclass
class Declaration:
    prop: str
    value: str
    important: bool = False
    line: int = 0


@dataclass
class Rule:
    selectors: list[str]
    declarations: list[Declaration] = field(default_factory=list)
    line: int = 0
    media: str | None = None  # e.g. "@media (max-width: 768px)"


@dataclass
class Variable:
    name: str
    value: str
    selector: str
    media: str | None
    line: int


@dataclass
class Stylesheet:
    rules: list[Rule] = field(default_factory=list)
    variable_defs: list[Variable] = field(default_factory=list)
    font_faces: list[dict] = field(default_factory=list)
    keyframes: list[str] = field(default_factory=list)
    imports: list[str] = field(default_factory=list)
    charset: str | None = None
    media_queries: list[str] = field(default_factory=list)  # each distinct context, in order
    other_at_rules: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    # ---- variables ------------------------------------------------------------
    def root_variables(self) -> dict[str, str]:
        """Variables defined globally (:root / html, outside any @media). Last one wins."""
        out: dict[str, str] = {}
        for v in self.variable_defs:
            if v.media is None and v.selector in (":root", "html"):
                out[v.name] = v.value
        return out

    def resolve_variable(self, name: str) -> dict:
        """Follow var() chains with cycle detection. Never raises."""
        root = self.root_variables()
        chain: list[str] = []
        current = name
        while True:
            if current in chain:
                return {"name": name, "status": "circular", "chain": chain + [current], "value": None}
            chain.append(current)
            if current not in root:
                status = "undefined"
                if current == name:
                    # maybe scoped (non-:root) definition
                    scoped = [v for v in self.variable_defs if v.name == name]
                    if scoped:
                        return {"name": name, "status": "scoped_only", "chain": chain, "value": None,
                                "defined_in": sorted({v.selector for v in scoped})}
                return {"name": name, "status": status, "chain": chain, "value": None}
            raw = root[current]
            m = re.fullmatch(r"var\(\s*(--[\w-]+)\s*\)", raw.strip())
            if m:
                current = m.group(1)
                continue
            resolved, issues = self.resolve_value(raw)
            status = "ok" if not issues else "partially_resolved"
            return {"name": name, "status": status, "chain": chain, "value": resolved, "raw": raw}

    def resolve_value(self, value: str, _depth: int = 0) -> tuple[str, list[str]]:
        """Replace every var(--x, fallback) in `value`. Returns (text, issues)."""
        issues: list[str] = []
        if _depth > 10 or "var(" not in value:
            return value, issues
        out, i = [], 0
        while i < len(value):
            j = value.find("var(", i)
            if j == -1:
                out.append(value[i:])
                break
            out.append(value[i:j])
            end = _match_paren(value, j + 3)
            if end == -1:
                out.append(value[j:])
                issues.append("unbalanced var()")
                break
            inner = value[j + 4:end]
            comma = _find_top(inner, 0, len(inner), ",")
            name = (inner if comma == -1 else inner[:comma]).strip()
            fallback = None if comma == -1 else inner[comma + 1:].strip()
            info = self.resolve_variable(name)
            if info["status"] == "ok" or info["status"] == "partially_resolved":
                text, sub = self.resolve_value(info["value"], _depth + 1)
                issues += sub
                out.append(text)
            elif fallback is not None:
                text, sub = self.resolve_value(fallback, _depth + 1)
                issues += sub
                out.append(text)
            else:
                issues.append(f"{name}: {info['status']}")
                out.append(value[j:end + 1])
            i = end + 1
        return "".join(out), issues


# ------------------------------------------------------------------ scanning helpers
def _blank_comments(text: str) -> tuple[str, list[str]]:
    """Replace comments by spaces (newlines kept) so offsets/line numbers stay valid.
    String-aware: '/*' inside quotes is not a comment."""
    out: list[str] = []
    warnings: list[str] = []
    i, n, quote = 0, len(text), None
    while i < n:
        c = text[i]
        if quote:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if c == quote or c == "\n":  # CSS: an unterminated string ends at newline
                quote = None
            i += 1
            continue
        if c in "\"'":
            quote = c
            out.append(c)
            i += 1
            continue
        if c == "/" and text.startswith("/*", i):
            end = text.find("*/", i + 2)
            if end == -1:
                end = n
                warnings.append(f"unterminated comment starting at line {text.count(chr(10), 0, i) + 1}")
            else:
                end += 2
            out.append("".join(ch if ch == "\n" else " " for ch in text[i:end]))
            i = end
            continue
        out.append(c)
        i += 1
    return "".join(out), warnings


def _find_top(text: str, start: int, end: int, targets: str) -> int:
    """Index of the first char in `targets` that is outside strings and (), []. -1 if none."""
    depth, quote, i = 0, None, start
    while i < end:
        c = text[i]
        if quote:
            if c == "\\":
                i += 2
                continue
            if c == quote or c == "\n":
                quote = None
        elif c in "\"'":
            quote = c
        elif c in "([":
            depth += 1
        elif c in ")]":
            depth = max(0, depth - 1)
        elif depth == 0 and c in targets:
            return i
        i += 1
    return -1


def _match_brace(text: str, open_idx: int, end: int) -> int:
    """Index of the '}' matching the '{' at open_idx (string/paren aware). -1 if unclosed."""
    depth, paren, quote, i = 0, 0, None, open_idx
    while i < end:
        c = text[i]
        if quote:
            if c == "\\":
                i += 2
                continue
            if c == quote or c == "\n":
                quote = None
        elif c in "\"'":
            quote = c
        elif c == "(":
            paren += 1
        elif c == ")":
            paren = max(0, paren - 1)
        elif paren == 0:
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return i
        i += 1
    return -1


def _match_paren(text: str, open_idx: int) -> int:
    depth, quote, i = 0, None, open_idx
    while i < len(text):
        c = text[i]
        if quote:
            if c == quote:
                quote = None
        elif c in "\"'":
            quote = c
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def _split_commas(s: str) -> list[str]:
    parts, start = [], 0
    while True:
        idx = _find_top(s, start, len(s), ",")
        if idx == -1:
            parts.append(s[start:])
            break
        parts.append(s[start:idx])
        start = idx + 1
    return [_norm(p) for p in parts if p.strip()]


# ------------------------------------------------------------------------- parser
class _Parser:
    def __init__(self, raw: str):
        self.text, warnings = _blank_comments(raw)
        self.sheet = Stylesheet(warnings=warnings)
        self.line_starts = [0] + [m.end() for m in re.finditer("\n", self.text)]
        self.depth = 0

    def line(self, offset: int) -> int:
        return bisect.bisect_right(self.line_starts, offset)

    def run(self) -> Stylesheet:
        self.block(0, len(self.text), None, None)
        self._collect_variables()
        return self.sheet

    # one "level" of CSS: either the top level, an @media body, or a rule body
    def block(self, start: int, end: int, context: str | None, parent: Rule | None) -> None:
        if self.depth >= MAX_DEPTH:  # protects against pathological input like "a{a{a{a{..."
            msg = f"line {self.line(start)}: nesting deeper than {MAX_DEPTH} levels; the rest is ignored"
            if msg not in self.sheet.warnings:
                self.sheet.warnings.append(msg)
            return
        self.depth += 1
        try:
            self._block(start, end, context, parent)
        finally:
            self.depth -= 1

    def _block(self, start: int, end: int, context: str | None, parent: Rule | None) -> None:
        text, pos = self.text, start
        while pos < end:
            while pos < end and text[pos].isspace():
                pos += 1
            if pos >= end:
                break
            stop = _find_top(text, pos, end, "{;}")
            if stop == -1:
                stop = end
            ch = text[stop] if stop < end else ""
            if ch == "}":
                self.sheet.warnings.append(f"line {self.line(stop)}: stray '}}' ignored")
                pos = stop + 1
                continue
            if ch == "{":
                close = _match_brace(text, stop, end)
                if close == -1:
                    self.sheet.warnings.append(
                        f"line {self.line(pos)}: block '{_norm(text[pos:stop])[:40]}' is never closed; "
                        "treated as closed at end of file")
                    close, nxt = end, end
                else:
                    nxt = close + 1
                self.handle_block(_norm(text[pos:stop]), stop + 1, close, context, parent, self.line(pos))
                pos = nxt
            else:  # ';' or end of text -> a statement
                stmt = text[pos:stop]
                if parent is None:
                    self.handle_statement(_norm(stmt), self.line(pos))
                else:
                    decl = self.parse_declaration(stmt, self.line(pos))
                    if decl:
                        parent.declarations.append(decl)
                pos = stop + 1

    def handle_statement(self, stmt: str, line: int) -> None:
        if not stmt:
            return
        low = stmt.lower()
        if low.startswith("@import"):
            m = re.search(r"url\(\s*[\"']?([^\"')]+)|[\"']([^\"']+)[\"']", stmt)
            self.sheet.imports.append((m.group(1) or m.group(2)).strip() if m else stmt[7:].strip())
        elif low.startswith("@charset"):
            m = re.search(r"[\"']([^\"']+)[\"']", stmt)
            self.sheet.charset = m.group(1) if m else None
        else:
            self.sheet.warnings.append(f"line {line}: unexpected top-level text ignored: {stmt[:40]!r}")

    def parse_declaration(self, stmt: str, line: int) -> Declaration | None:
        stmt = stmt.strip()
        if not stmt:
            return None
        idx = _find_top(stmt, 0, len(stmt), ":")
        if idx <= 0:
            self.sheet.warnings.append(f"line {line}: malformed declaration ignored: {_norm(stmt)[:40]!r}")
            return None
        prop = stmt[:idx].strip()
        if not prop.startswith("--"):
            prop = prop.lower()
        value = _norm(stmt[idx + 1:])
        important = bool(re.search(r"!\s*important\s*$", value, re.I))
        if important:
            value = re.sub(r"\s*!\s*important\s*$", "", value, flags=re.I)
        if value == "" and not prop.startswith("--"):
            self.sheet.warnings.append(f"line {line}: property '{prop}' has an empty value")
        return Declaration(prop, value, important, line)

    def handle_block(self, prelude: str, start: int, end: int, context: str | None,
                     parent: Rule | None, line: int) -> None:
        sheet = self.sheet
        if prelude.startswith("@"):
            m = re.match(r"@([\w-]+)\s*(.*)$", prelude, re.S)
            if not m:
                sheet.warnings.append(f"line {line}: at-rule without a name ignored")
                return
            name =re.sub(r"^-\w+-", "", m.group(1).lower())  # strip vendor prefix
            rest = _norm(m.group(2))
            if name in CONDITIONAL_AT_RULES:
                ctx = f"@{name} {rest}".strip()
                full = ctx if context is None else f"{context} > {ctx}"
                if name == "media" and full not in sheet.media_queries:
                    sheet.media_queries.append(full)
                if parent is not None:  # nested conditional inside a rule (CSS nesting)
                    inner = Rule(list(parent.selectors), [], line, full)
                    sheet.rules.append(inner)
                    self.block(start, end, full, inner)
                else:
                    self.block(start, end, full, None)
            elif name == "font-face":
                holder = Rule([], [], line)
                self.block(start, end, context, holder)
                sheet.font_faces.append({d.prop: d.value for d in holder.declarations})
            elif name == "keyframes":
                sheet.keyframes.append(rest)
            else:
                sheet.other_at_rules.append(f"@{name}")
            return
        # ordinary style rule
        selectors = _split_commas(prelude)
        if parent is not None and parent.selectors:  # nested rule: combine with parent
            combined = []
            for child in selectors:
                for p in parent.selectors:
                    combined.append(child.replace("&", p) if "&" in child else f"{p} {child}")
            selectors = combined
        if not selectors:
            sheet.warnings.append(f"line {line}: rule with empty selector ignored")
            return
        rule = Rule(selectors, [], line, context)
        sheet.rules.append(rule)  # appended before parsing the body -> parent precedes nested rules
        self.block(start, end, context, rule)

    def _collect_variables(self) -> None:
        for rule in self.sheet.rules:
            for d in rule.declarations:
                if d.prop.startswith("--"):
                    for sel in rule.selectors:
                        self.sheet.variable_defs.append(Variable(d.prop, d.value, sel, rule.media, d.line))


def parse_css(text: str) -> Stylesheet:
    """Parse CSS source text. Never raises on malformed input; see Stylesheet.warnings."""
    return _Parser(text).run()


def parse_css_file(path: str) -> Stylesheet:
    with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
        return parse_css(fh.read())
