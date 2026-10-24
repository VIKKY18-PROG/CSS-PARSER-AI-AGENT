import unittest
from cssagent.parser import parse_css
from cssagent.analysis import specificity, color_palette, font_report, extract_colors


class ParserEdgeCases(unittest.TestCase):
    def test_braces_and_semicolons_inside_strings_and_url(self):
        s = parse_css('.a::after{content:"}{;";color:red}.b{background:url(data:image/png;base64,AA;BB) no-repeat}')
        self.assertEqual(len(s.rules), 2)
        self.assertEqual(s.rules[0].declarations[0].value, '"}{;"')
        self.assertEqual(s.rules[1].declarations[0].prop, "background")
        self.assertEqual(len(s.rules[1].declarations), 1)

    def test_comments_are_ignored_even_with_braces(self):
        s = parse_css("/* .x { color: red } */ .y { color: blue } /* trailing")
        self.assertEqual([r.selectors for r in s.rules], [[".y"]])
        self.assertTrue(any("unterminated comment" in w for w in s.warnings))

    def test_comment_marker_inside_string_is_not_a_comment(self):
        s = parse_css('.a { content: "/* not a comment */"; color: red }')
        self.assertEqual(len(s.rules[0].declarations), 2)

    def test_missing_semicolon_and_important(self):
        s = parse_css(".a { color: red !important; margin: 0 }")
        d = s.rules[0].declarations
        self.assertTrue(d[0].important)
        self.assertEqual(d[0].value, "red")
        self.assertEqual(d[1].value, "0")

    def test_unclosed_block_recovers_with_warning(self):
        s = parse_css(".a { color: red; } .b { color: blue;")
        self.assertEqual(len(s.rules), 2)
        self.assertTrue(any("never closed" in w for w in s.warnings))

    def test_stray_close_brace_and_garbage(self):
        s = parse_css("} .a { color: red } ??? ; .b { color }")
        self.assertEqual(s.rules[0].selectors, [".a"])
        self.assertTrue(s.warnings)

    def test_nested_media_and_line_numbers(self):
        s = parse_css("@media (a) {\n .x { color: red }\n @media (b) {\n .y { color: blue }\n }\n}")
        self.assertEqual(s.rules[0].media, "@media (a)")
        self.assertEqual(s.rules[1].media, "@media (a) > @media (b)")
        self.assertEqual(s.rules[1].line, 4)

    def test_css_nesting(self):
        s = parse_css(".card { color: red; & .t { color: blue } .u { color: green } }")
        self.assertEqual([r.selectors for r in s.rules], [[".card"], [".card .t"], [".card .u"]])

    def test_selector_list_and_at_rules(self):
        s = parse_css('@charset "UTF-8"; @import url("a.css"); h1,h2 , h3{margin:0} @keyframes k{from{top:0}to{top:1px}}'
                      ' @font-face{font-family:"F";src:url(f.woff)}')
        self.assertEqual(s.rules[0].selectors, ["h1", "h2", "h3"])
        self.assertEqual((s.charset, s.imports, s.keyframes), ("UTF-8", ["a.css"], ["k"]))
        self.assertEqual(s.font_faces[0]["font-family"], '"F"')
        self.assertEqual(len(s.rules), 1)  # keyframe steps are not style rules

    def test_empty_and_garbage_input_never_raises(self):
        for text in ["", "   ", "{", "}", "{{{{", "@media", "a{", "a{b", "a{b:", "/*", '"', "a{b:'}"]:
            parse_css(text)


class Robustness(unittest.TestCase):
    """Regressions found by fuzzing."""
    def test_at_sign_without_name(self):
        s = parse_css("@ { color: red } .ok { color: blue }")
        self.assertEqual([r.selectors for r in s.rules], [[".ok"]])
        self.assertTrue(any("without a name" in w for w in s.warnings))

    def test_pathological_nesting_does_not_blow_the_stack(self):
        s = parse_css("a{" * 20000)
        self.assertTrue(s.warnings)

    def test_random_garbage_never_raises(self):
        import random
        rnd = random.Random(7)
        alpha = list("{}();:,/*\"'@-#.&!ab \n") + ["var(", "--x", "url(", "@media ", "/*", "@font-face"]
        for _ in range(2000):
            parse_css("".join(rnd.choice(alpha) for _ in range(rnd.randint(0, 80))))


class Variables(unittest.TestCase):
    def test_chain_fallback_cycle_undefined(self):
        s = parse_css(":root{--a:#fff;--b:var(--a);--c:var(--d);--d:var(--c)} .x{color:var(--b);margin:var(--zz, 4px)}")
        self.assertEqual(s.resolve_variable("--b")["value"], "#fff")
        self.assertEqual(s.resolve_variable("--c")["status"], "circular")
        self.assertEqual(s.resolve_variable("--zz")["status"], "undefined")
        self.assertEqual(s.resolve_value("var(--zz, 4px)")[0], "4px")
        self.assertEqual(s.resolve_value("var(--zz, var(--a))")[0], "#fff")

    def test_scoped_variable_is_not_global(self):
        s = parse_css(".dark{--bg:#000}")
        self.assertEqual(s.resolve_variable("--bg")["status"], "scoped_only")


class Analysis(unittest.TestCase):
    def test_specificity(self):
        self.assertEqual(specificity("#a .b"), (1, 1, 0))
        self.assertEqual(specificity("ul li:first-child a::after"), (0, 1, 4))
        self.assertEqual(specificity(":not(#x)"), (1, 0, 0))
        self.assertEqual(specificity(":where(#x) p"), (0, 0, 1))
        self.assertEqual(specificity('a[href="#x.y"]'), (0, 1, 1))
        self.assertEqual(specificity("*"), (0, 0, 0))

    def test_colors_normalised_and_var_resolved(self):
        s = parse_css(":root{--p:#ABC} a{color:var(--p)} b{color:#aabbcc} i{background:url(#fff.png)}")
        pal = {c["color"]: c["used_count"] for c in color_palette(s)["colors"]}
        self.assertEqual(pal, {"#aabbcc": 2})

    def test_named_color_only_in_color_props(self):
        self.assertEqual(extract_colors("color", "red"), ["red"])
        self.assertEqual(extract_colors("font-family", "red, serif"), [])
        self.assertEqual(extract_colors("content", '"red"'), [])

    def test_fonts_shorthand_and_family(self):
        s = parse_css('a{font:700 2rem/1.2 Georgia, serif} b{font-family:"Open Sans",sans-serif}')
        fams = {f["family"] for f in font_report(s)["families"]}
        self.assertEqual(fams, {"Georgia", "serif", "Open Sans", "sans-serif"})


if __name__ == "__main__":
    unittest.main()
