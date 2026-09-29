from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

CAD_HTML = Path(settings.BASE_DIR) / "core" / "static" / "core" / "tools" / "CBLCAD_VER2.html"


def _function_source(html, name):
    start = html.index("function " + name + "(")
    end = html.index("\nfunction ", start + 1)
    return html[start:end]


class CadBlockNameEscapeTests(SimpleTestCase):
    """Block names come from uploaded DWG/DXF files and must never be parsed as HTML."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.html = CAD_HTML.read_text(encoding="utf-8")

    def test_block_list_escapes_names_and_binds_clicks_without_inline_js(self):
        source = _function_source(self.html, "updBlkList")
        self.assertIn("cblEscHtml(n)", source)
        self.assertNotIn('onclick="', source)
        self.assertNotIn(" '+n+' ", source)

    def test_block_grid_escapes_names(self):
        source = _function_source(self.html, "refreshBlkGrid")
        self.assertIn("cblEscHtml(name)", source)
        self.assertNotIn("</span> '+name+'</div>'", source)

    def test_escape_helper_covers_html_metacharacters(self):
        helper = _function_source(self.html, "cblEscHtml")
        for char, entity in (("&", "&amp;"), ("<", "&lt;"), (">", "&gt;"), ('"', "&quot;"), ("'", "&#39;")):
            self.assertIn(entity, helper, char)
