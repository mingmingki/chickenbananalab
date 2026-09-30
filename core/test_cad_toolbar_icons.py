import json
import re
import shutil
import subprocess
from pathlib import Path
from unittest import skipUnless

from django.conf import settings
from django.test import SimpleTestCase

CAD_HTML = Path(settings.BASE_DIR) / "core" / "static" / "core" / "tools" / "CBLCAD_VER2.html"
NODE = shutil.which("node")

# Minimal browser globals: enough for the icon scripts to load and for addBtn()
# to build a ribbon button, with timers disabled so nothing runs after the test.
NODE_PRELUDE = """
globalThis.window = globalThis;
globalThis.setTimeout = function(){};
globalThis.setInterval = function(){};
const created = [];
const rtHome = {appendChild(el){ created.push(el); }};
globalThis.document = {
  readyState: 'complete',
  addEventListener(){},
  querySelectorAll(){ return []; },
  querySelector(){ return null; },
  getElementById(id){ return id === 'rt-home' ? rtHome : null; },
  createElement(tag){ return {tagName: tag, attrs: {}, innerHTML: '', setAttribute(k, v){ this.attrs[k] = String(v); }}; },
};
"""


def _script(html, script_id):
    start = html.index('<script id="%s">' % script_id)
    start = html.index(">", start) + 1
    return html[start:html.index("</script>", start)]


def _function(block, name, next_name):
    start = block.index("function %s(" % name)
    return block[start:block.index("function %s(" % next_name, start)]


@skipUnless(NODE, "node is required to execute the CAD icon scripts")
class CadRibbonIconTests(SimpleTestCase):
    """Two icon scripts (V1 and V2) repaint the same ribbon buttons; each repaint must draw a real symbol."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.html = CAD_HTML.read_text(encoding="utf-8")
        cls.symbols = set(re.findall(r'<symbol id="cbl-icon-([a-z0-9-]+)"', cls.html))
        # V1 is defined first but repaints at DOMContentLoaded and on tab clicks, after V2 exists.
        cls.icon_scripts = (_script(cls.html, "CBL_CAD_ICON_MANIFEST_V2_SCRIPT") + "\n"
                            + _script(cls.html, "CBL_DARK_MONO_SVG_ICON_SYSTEM_V1_SCRIPT"))

    def run_node(self, body):
        run = subprocess.run([NODE, "-e", NODE_PRELUDE + self.icon_scripts + "\n" + body],
                             capture_output=True, text=True, timeout=60)
        self.assertEqual(run.returncode, 0, run.stderr)
        return json.loads(run.stdout)

    def test_v1_repaint_draws_the_same_symbol_as_v2(self):
        result = self.run_node(
            "const M = window.CBL_CAD_ICON_MANIFEST_V2, out = {};\n"
            "for (const k of Object.keys(M)) {\n"
            "  const m = window.cblSvgIconMarkupV1(k).match(/href=\"#cbl-icon-([^\"]+)\"/);\n"
            "  out[k] = {v1: m && m[1], v2: M[k]};\n"
            "}\n"
            "process.stdout.write(JSON.stringify(out));")
        self.assertIn("mirror", result)
        self.assertIn("delete", result)
        mismatched = {k: v for k, v in result.items() if v["v1"] != v["v2"]}
        self.assertEqual(mismatched, {})
        missing = sorted(k for k, v in result.items() if v["v2"] not in self.symbols)
        self.assertEqual(missing, [])

    def test_trim_and_extend_buttons_are_created_with_svg_icons(self):
        block = self.html[self.html.index("<!-- CBL_TRIM_EXTEND_AUTOCAD_V3_QUICK_MODE_START -->"):
                          self.html.index("<!-- CBL_TRIM_EXTEND_AUTOCAD_V3_QUICK_MODE_END -->")]
        result = self.run_node(
            _function(block, "addBtn", "installButtons") + "\n"
            "addBtn('trim','자르기','TR','&#9986;'); addBtn('extend','연장','EX','&#8594;|');\n"
            "process.stdout.write(JSON.stringify(created.map(b => b.innerHTML)));")
        self.assertEqual(len(result), 2)
        for markup, symbol in zip(result, ("trim", "extend")):
            self.assertIn('href="#cbl-icon-%s"' % symbol, markup)
            self.assertIn('data-cbl-command-key="%s"' % symbol, markup)
            self.assertIn(symbol, self.symbols)


class CadBottomActionBoxStartupTests(SimpleTestCase):
    """The legacy bottom action box stays in the DOM for its handlers but must never flash on load."""

    def test_box_is_hidden_before_it_can_be_painted(self):
        html = CAD_HTML.read_text(encoding="utf-8")
        tag = re.search(r'<div[^>]*id="cblBottomTools"[^>]*>', html)
        self.assertIsNotNone(tag)
        self.assertIn("cbl-bottom-action-box-hidden-v1", tag.group(0))
        # The hiding rule must already be parsed when the box markup is reached;
        # otherwise it shows until the DOMContentLoaded script hides it.
        rule = re.search(r"\.cbl-bottom-action-box-hidden-v1\s*\{[^}]*left:\s*-99999px", html)
        self.assertIsNotNone(rule)
        self.assertLess(rule.start(), tag.start())
