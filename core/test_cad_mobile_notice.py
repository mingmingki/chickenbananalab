from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

CAD_HTML = Path(settings.BASE_DIR) / "core" / "static" / "core" / "tools" / "CBLCAD_VER2.html"


class CadMobilePcNoticeTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        html = CAD_HTML.read_text(encoding="utf-8")
        start = html.index("<!-- CBL_MOBILE_PC_NOTICE_V1_START -->")
        cls.block = html[start:html.index("<!-- CBL_MOBILE_PC_NOTICE_V1_END -->", start)]

    def test_notice_only_targets_touch_phones(self):
        self.assertIn("(pointer: coarse)", self.block)
        self.assertIn("Math.min(screen.width||0,screen.height||0)<600", self.block)
        self.assertIn("box.style.display='none'", self.block)

    def test_notice_can_be_dismissed_to_keep_using_cad(self):
        self.assertIn("그래도 계속하기", self.block)
        self.assertIn("sessionStorage", self.block)
