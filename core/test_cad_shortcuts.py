from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

CAD_HTML = Path(settings.BASE_DIR) / "core" / "static" / "core" / "tools" / "CBLCAD_VER2.html"


class CadModifierShortcutTests(SimpleTestCase):
    """On macOS, Cmd+Shift+<letter> reports a lowercase e.key, so Shift must be read from e.shiftKey."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        html = CAD_HTML.read_text(encoding="utf-8")
        start = html.index("// 글로벌 키 → 커맨드라인으로 버퍼링")
        block_start = html.index("if(e.ctrlKey||e.metaKey){", start)
        cls.block = html[block_start:html.index("return;", block_start)]

    def test_shift_z_redoes_and_plain_z_undoes(self):
        self.assertIn("if(e.shiftKey)doRedo();else doUndo();", self.block)
        self.assertNotIn("if(e.key==='z'){e.preventDefault();doUndo();}", self.block)

    def test_shift_g_ungroups_and_plain_g_groups(self):
        self.assertIn("if(e.shiftKey)ungroupSel();else groupSel();", self.block)
        self.assertNotIn("e.key==='G'", self.block)

    def test_ctrl_y_still_redoes(self):
        self.assertIn("e.key==='y'){e.preventDefault();doRedo();}", self.block)
