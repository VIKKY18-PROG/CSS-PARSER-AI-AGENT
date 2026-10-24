"""Turns a parsed Stylesheet into *meaningful* facts: palette, fonts, specificity, stats."""
from __future__ import annotations

import re
from collections import Counter, defaultdict

from .parser import Stylesheet, _split_commas, _find_top, _match_paren

_NAMED = set("""aliceblue antiquewhite aqua aquamarine azure beige bisque black blanchedalmond blue blueviolet brown
burlywood cadetblue chartreuse chocolate coral cornflowerblue cornsilk crimson cyan darkblue darkcyan darkgoldenrod
darkgray darkgreen darkgrey darkkhaki darkmagenta darkolivegreen darkorange darkorchid darkred darksalmon
darkseagreen darkslateblue darkslategray darkslategrey darkturquoise darkviolet deeppink deepskyblue dimgray dimgrey
dodgerblue firebrick floralwhite forestgreen fuchsia gainsboro ghostwhite gold goldenrod gray green greenyellow grey
honeydew hotpink indianred indigo ivory khaki lavender lavenderblush lawngreen lemonchiffon lightblue lightcoral
lightcyan lightgoldenrodyellow lightgray lightgreen lightgrey lightpink lightsalmon lightseagreen lightskyblue
lightslategray lightslategrey lightsteelblue lightyellow lime limegreen linen magenta maroon mediumaquamarine
mediumblue mediumorchid mediumpurple mediumseagreen mediumslateblue mediumspringgreen mediumturquoise
mediumvioletred midnightblue mintcream mistyrose moccasin navajowhite navy oldlace olive olivedrab orange orangered
orchid palegoldenrod palegreen paleturquoise palevioletred papayawhip peachpuff peru pink plum powderblue purple
rebeccapurple red rosybrown royalblue saddlebrown salmon sandybrown seagreen seashell sienna silver skyblue
slateblue slategray slategrey snow springgreen steelblue tan teal thistle tomato turquoise violet wheat white
whitesmoke yellow yellowgreen""".split())

# named colours are only trusted in properties that take colours (avoids false positives)
_COLOR_PROP = re.compile(
    r"^(color|background(-color|-image)?|border(-(top|right|bottom|left))?(-color)?|outline(-color)?|fill|stroke|"
    r"box-shadow|text-shadow|text-decoration(-color)?|caret-color|accent-color|column-rule(-color)?)$")

_HEX = re.compile(r"#(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{4}|[0-9a-fA-F]{3})(?![0-9a-zA-Z_-])")
_FUNC = re.compile(r"\b(?:rgba?|hsla?|hwb|lab|lch|oklab|oklch)\([^()]*\)", re.I)
GENERIC_FONTS = {"serif", "sans-serif", "monospace", "cursive", "fantasy", "system-ui", "ui-serif", "ui-sans-serif",
                 "ui-monospace", "ui-rounded", "emoji", "math", "fangsong", "inherit", "initial", "unset",
                 "-apple-system", "blinkmacsystemfont"}


def _strip_noise(value: str) -> str:
    value = re.sub(r"url\([^)]*\)", " ", value, flags=re.I)
    return re.sub(r"\"[^\"]*\"|'[^']*'", " ", value)


def normalize_hex(h: str) -> str:
    h = h.lower()
    if len(h) in (4, 5):
        h = "#" + "".join(ch * 2 for ch in h[1:])
    return h


def extract_colors(prop: str, value: str) -> list[str]:
    v = _strip_noise(value)
    found = [_norm_func(m.group(0)) for m in _FUNC.finditer(v)]
    v2 = _FUNC.sub(" ", v)
    found += [normalize_hex(m.group(0)) for m in _HEX.finditer(v2)]
    if _COLOR_PROP.match(prop):
        found += [w.lower() for w in re.findall(r"[A-Za-z]+", v2) if w.lower() in _NAMED]
    return found


def _norm_func(s: str) -> str:
    return re.sub(r"\s+", " ", s.lower())


def color_palette(sheet: Stylesheet) -> dict:
    usage: Counter = Counter()
    props: dict[str, set] = defaultdict(set)
    var_defs: dict[str, set] = defaultdict(set)
    for v in sheet.variable_defs:  # colours *defined* as variables
        for c in extract_colors(v.name, v.value):
            var_defs[c].add(v.name)
    for rule in sheet.rules:
        for d in rule.declarations:
            if d.prop.startswith("--"):
                continue
            resolved, _ = sheet.resolve_value(d.value)  # follow var() so palette reflects real colours
            for c in extract_colors(d.prop, resolved):
                usage[c] += 1
                props[c].add(d.prop)
    colors = sorted(set(usage) | set(var_defs), key=lambda c: (-usage[c], c))
    return {
        "found": bool(colors),
        "unique_colors": len(colors),
        "colors": [{"color": c, "used_count": usage[c], "properties": sorted(props[c]),
                    "defined_as_variables": sorted(var_defs[c])} for c in colors],
    }


def extract_fonts(prop: str, value: str) -> list[str]:
    if prop == "font-family":
        fam = value
    elif prop == "font":
        m = re.search(r"(?:^|\s)(?:\d*\.?\d+(?:px|em|rem|%|pt|vh|vw|ch|ex)|x{1,2}-small|x{0,2}-?large|small|medium|"
                      r"smaller|larger)(?:\s*/\s*\S+)?\s+(.+)$", value)
        if not m:
            return []
        fam = m.group(1)
    else:
        return []
    return [f.strip().strip("\"'").strip() for f in _split_commas(fam) if f.strip()]


def font_report(sheet: Stylesheet) -> dict:
    used: Counter = Counter()
    primary: Counter = Counter()
    for rule in sheet.rules:
        for d in rule.declarations:
            if d.prop.startswith("--"):
                continue
            resolved, _ = sheet.resolve_value(d.value)
            fams = extract_fonts(d.prop, resolved)
            for f in fams:
                used[f] += 1
            if fams:
                primary[fams[0]] += 1
    declared = sorted({ff.get("font-family", "").strip("\"'") for ff in sheet.font_faces} - {""})
    families = sorted(used, key=lambda f: (-used[f], f))
    return {
        "found": bool(families or declared),
        "families": [{"family": f, "used_count": used[f], "generic": f.lower() in GENERIC_FONTS,
                      "declared_with_font_face": f in declared} for f in families],
        "primary_fonts": [f for f in sorted(primary, key=lambda f: (-primary[f], f))],
        "font_face_declared": declared,
        "declared_but_unused": [f for f in declared if f not in used],
        "custom_fonts_not_declared": [f for f in families if f.lower() not in GENERIC_FONTS and f not in declared],
    }


# ------------------------------------------------------------------- specificity
def specificity(selector: str) -> tuple[int, int, int]:
    """(ids, classes/attributes/pseudo-classes, elements/pseudo-elements). :where()=0; :not/:is/:has = their argument."""
    s = re.sub(r"\"[^\"]*\"|'[^']*'", '""', selector)
    a = b = c = 0
    while True:  # functional pseudo-classes that take selector lists
        m = re.search(r":(not|is|has|where|matches)\(", s)
        if not m:
            break
        close = _match_paren(s, m.end() - 1)
        if close == -1:
            s = s[:m.start()] + s[m.end():]
            continue
        if m.group(1) != "where":
            best = max((specificity(p) for p in _split_commas(s[m.end():close])), default=(0, 0, 0))
            a, b, c = a + best[0], b + best[1], c + best[2]
        s = s[:m.start()] + " " + s[close + 1:]
    b += len(re.findall(r"\[[^\]]*\]", s))
    s = re.sub(r"\[[^\]]*\]", " ", s)
    pe = re.findall(r"::[\w-]+|:(?:before|after|first-line|first-letter)\b", s)
    c += len(pe)
    s = re.sub(r"::[\w-]+|:(?:before|after|first-line|first-letter)\b", " ", s)
    s = re.sub(r"(:[\w-]+)\([^)]*\)", r"\1", s)  # :nth-child(2n+1) -> :nth-child
    b += len(re.findall(r":[\w-]+", s))
    s = re.sub(r":[\w-]+", " ", s)
    a += len(re.findall(r"#[\w-]+", s))
    s = re.sub(r"#[\w-]+", " ", s)
    b += len(re.findall(r"\.[\w-]+", s))
    s = re.sub(r"\.[\w-]+", " ", s)
    c += len(re.findall(r"(?<![\w-])[a-zA-Z][\w-]*", s))
    return a, b, c


def top_specific(sheet: Stylesheet, n: int = 5) -> dict:
    seen: dict[str, tuple] = {}
    for r in sheet.rules:
        for sel in r.selectors:
            seen.setdefault(sel, specificity(sel))
    ranked = sorted(seen.items(), key=lambda kv: (kv[1], kv[0]), reverse=True)[:max(1, n)]
    return {"found": bool(ranked),
            "selectors": [{"selector": s, "specificity": list(sp)} for s, sp in ranked],
            "note": "specificity is [ids, classes/attributes/pseudo-classes, elements]"}


# ----------------------------------------------------------------------- summary
def overview(sheet: Stylesheet) -> dict:
    decls = [d for r in sheet.rules for d in r.declarations]
    selectors = {s for r in sheet.rules for s in r.selectors}
    props = Counter(d.prop for d in decls if not d.prop.startswith("--"))
    dupes = []
    for r in sheet.rules:
        seen: dict[str, list[str]] = defaultdict(list)
        for d in r.declarations:
            seen[d.prop].append(d.value)
        for p, vals in seen.items():
            if len(vals) > 1 and len(set(vals)) > 1:
                dupes.append({"selector": ", ".join(r.selectors), "property": p, "values": vals, "line": r.line})
    return {
        "found": True,
        "rule_count": len(sheet.rules),
        "unique_selector_count": len(selectors),
        "declaration_count": len(decls),
        "important_count": sum(d.important for d in decls),
        "id_selector_count": sum(1 for s in selectors if specificity(s)[0] > 0),
        "unique_property_count": len(props),
        "top_properties": [{"property": p, "count": n} for p, n in sorted(props.items(), key=lambda kv: (-kv[1], kv[0]))[:5]],
        "variable_count": len({v.name for v in sheet.variable_defs}),
        "media_query_count": len(sheet.media_queries),
        "font_face_count": len(sheet.font_faces),
        "keyframes": sorted(sheet.keyframes),
        "imports": sheet.imports,
        "charset": sheet.charset,
        "conflicting_duplicate_declarations": dupes,
        "warnings": sheet.warnings,
    }


def media_report(sheet: Stylesheet) -> dict:
    counts = Counter(r.media for r in sheet.rules if r.media)
    return {
        "found": bool(sheet.media_queries),
        "queries": [{"query": q, "rule_count": counts.get(q, 0)} for q in sheet.media_queries],
    }


def variables_report(sheet: Stylesheet) -> dict:
    names = sorted({v.name for v in sheet.variable_defs})
    items = []
    for n in names:
        info = sheet.resolve_variable(n)
        items.append({"name": n, "status": info["status"], "value": info.get("value"),
                      "raw": info.get("raw"), "chain": info["chain"]})
    return {"found": bool(items), "variable_count": len(items), "variables": items}
