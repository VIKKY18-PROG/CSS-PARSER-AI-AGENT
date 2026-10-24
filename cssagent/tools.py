"""Tools (plain Python functions) the agent can call. They are the ONLY source of facts.

Every tool returns a JSON-serialisable dict with a "found" flag, so "the data isn't there"
is an explicit, machine-readable result instead of something the model has to guess.
Output ordering is deterministic (sorted), which is what makes repeat runs comparable.
"""
from __future__ import annotations

from . import analysis
from .parser import Stylesheet


class CSSToolbox:
    def __init__(self, sheet: Stylesheet):
        self.sheet = sheet

    # --- tools ---------------------------------------------------------------
    def get_overview(self) -> dict:
        return analysis.overview(self.sheet)

    def find_rules(self, property: str | None = None, value_contains: str | None = None,
                   selector_contains: str | None = None, important_only: bool = False, limit: int = 20) -> dict:
        prop = property.strip().lower() if property else None
        needle_v = value_contains.lower() if value_contains else None
        needle_s = selector_contains.lower() if selector_contains else None
        matches = []
        for rule in self.sheet.rules:
            if needle_s and not any(needle_s in s.lower() for s in rule.selectors):
                continue
            for d in rule.declarations:
                if prop and d.prop != prop and not (prop.endswith("*") and d.prop.startswith(prop[:-1])):
                    continue
                if needle_v and needle_v not in d.value.lower():
                    continue
                if important_only and not d.important:
                    continue
                m = {"selector": ", ".join(rule.selectors), "property": d.prop, "value": d.value,
                     "line": d.line, "important": d.important}
                if rule.media:
                    m["media"] = rule.media
                matches.append(m)
        limit = max(1, min(int(limit), 100))
        return {"found": bool(matches), "match_count": len(matches), "returned": min(len(matches), limit),
                "truncated": len(matches) > limit, "matches": matches[:limit]}

    def get_color_palette(self) -> dict:
        return analysis.color_palette(self.sheet)

    def get_fonts(self) -> dict:
        return analysis.font_report(self.sheet)

    def get_variables(self) -> dict:
        return analysis.variables_report(self.sheet)

    def resolve_variable(self, name: str) -> dict:
        name = name.strip()
        if not name.startswith("--"):
            name = "--" + name
        info = self.sheet.resolve_variable(name)
        info["found"] = info["status"] not in ("undefined",)
        return info

    def get_media_queries(self) -> dict:
        return analysis.media_report(self.sheet)

    def top_specific_selectors(self, n: int = 5) -> dict:
        return analysis.top_specific(self.sheet, int(n))

    # --- dispatch ---------------------------------------------------------------
    def call(self, name: str, args: dict | None = None) -> dict:
        args = args or {}
        fn = self._REGISTRY.get(name)
        if fn is None:
            return {"found": False, "error": f"unknown tool '{name}'", "available_tools": sorted(self._REGISTRY)}
        try:
            return getattr(self, fn)(**args)
        except TypeError as e:
            return {"found": False, "error": f"bad arguments for {name}: {e}"}

    _REGISTRY = {
        "get_overview": "get_overview",
        "find_rules": "find_rules",
        "get_color_palette": "get_color_palette",
        "get_fonts": "get_fonts",
        "get_variables": "get_variables",
        "resolve_variable": "resolve_variable",
        "get_media_queries": "get_media_queries",
        "top_specific_selectors": "top_specific_selectors",
    }


_NONE = {"type": "object", "properties": {}}

TOOL_SPECS = [
    {"name": "get_overview",
     "description": "Counts and summary of the stylesheet: rules, selectors, declarations, !important uses, "
                    "variables, keyframes, imports, conflicting duplicate declarations and parser warnings.",
     "input_schema": _NONE},
    {"name": "find_rules",
     "description": "Find declarations by CSS property (exact name, e.g. 'font-weight'), value substring, selector "
                    "substring, or !important only. Returns selector, value and line number for each match. "
                    "found=false means the file has no such declaration.",
     "input_schema": {"type": "object", "properties": {
         "property": {"type": "string"}, "value_contains": {"type": "string"},
         "selector_contains": {"type": "string"}, "important_only": {"type": "boolean"},
         "limit": {"type": "integer"}}}},
    {"name": "get_color_palette",
     "description": "All colours used in the file (normalised; var() resolved) with usage counts and properties.",
     "input_schema": _NONE},
    {"name": "get_fonts",
     "description": "Font families used, which are generic, which are declared via @font-face, primary fonts.",
     "input_schema": _NONE},
    {"name": "get_variables",
     "description": "All CSS custom properties (--name) with resolved values and status (ok/undefined/circular).",
     "input_schema": _NONE},
    {"name": "resolve_variable",
     "description": "Resolve one CSS custom property, following var() chains. status: ok, circular, undefined, scoped_only.",
     "input_schema": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}},
    {"name": "get_media_queries",
     "description": "Media queries in the file and how many rules sit in each.",
     "input_schema": _NONE},
    {"name": "top_specific_selectors",
     "description": "The n most specific selectors in the file with [ids, classes, elements] specificity.",
     "input_schema": {"type": "object", "properties": {"n": {"type": "integer"}}}},
]
