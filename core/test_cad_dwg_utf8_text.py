import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from unittest import skipUnless
from unittest.mock import patch

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, SimpleTestCase

from . import views as core_views
from .test_cad_dwg_save_integrity import _html
from .test_cad_dwg_text_validation import EXECUTABLE, _run_writer

FIXTURES = Path(settings.BASE_DIR) / "core" / "test_fixtures" / "cad"
# plan_ansi949_r2004.dxf plus a TEXT "900×400", written as AC1018 with the
# Korean code page in the header but UTF-8 string bytes, as an older ChickenBananaCAD pipeline saved
# S-501 (2차보완)-29..32.  Made with a throwaway fork copy whose writer
# encoding was forced to UTF-8 (~/chickenbanana-work/_build/utf8fix).
UTF8_BYTES = FIXTURES / "korean_utf8_bytes_ac1018.dwg"
KOREAN = FIXTURES / "korean_xrecord_ac1018.dwg"


def _model_texts(meta):
    return sorted(e["text"] for e in meta["entities"] if e.get("space") == "modelspace" and "text" in e)


@skipUnless(EXECUTABLE is not None, "ACadSharp runtime is required")
class CadDwgMisdeclaredUtf8Tests(SimpleTestCase):
    """Strings stored as UTF-8 under the KS C 5601 code page are read as UTF-8.

    Decoded with the code page they were garbage ("湲곗큹 F1"), and every save
    turned part of that garbage into "?".
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cbl-utf8-text-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_texts_and_names_read_as_korean(self):
        meta = core_views._cbl_free_dwg_acadsharp_metadata_v1(UTF8_BYTES)
        # "×" alone is valid in both encodings (UTF-8 C3 97 is "횞" in KS C 5601);
        # the drawing's Hangul strings show that it holds UTF-8.
        self.assertEqual(_model_texts(meta), ["900×400", "기초 F1", "슬래브 두께 200"])
        self.assertIn("슬래브", [layer["name"] for layer in meta["layers"]])
        self.assertEqual({e["block"]["name"] for e in meta["entities"] if e.get("space") == "modelspace" and e["type"] == "INSERT"}, {"기둥"})
        self.assertGreater(core_views._cbl_free_dwg_misdeclared_utf8_v1(meta["notifications"]), 0)

    def test_drawings_in_their_own_code_page_are_unchanged(self):
        for fixture in (KOREAN, FIXTURES / "western_ansi_ac1018.dwg", FIXTURES / "arc_bulge_ac1018.dwg"):
            meta = core_views._cbl_free_dwg_acadsharp_metadata_v1(fixture)
            self.assertEqual(core_views._cbl_free_dwg_misdeclared_utf8_v1(meta["notifications"]), 0, fixture.name)

    def test_a_save_writes_them_in_the_code_page(self):
        ops = self.tmp / "ops.json"
        ops.write_text('{"ops": []}', encoding="utf-8")
        output = self.tmp / "saved.dwg"
        report = _run_writer([UTF8_BYTES, output, "AC1018", ops])
        core_views._cbl_free_dwg_save_local_validate_v1(UTF8_BYTES, output, None, [], report)
        meta = core_views._cbl_free_dwg_acadsharp_metadata_v1(output)
        self.assertEqual(_model_texts(meta), ["900×400", "기초 F1", "슬래브 두께 200"])
        self.assertIn("슬래브", [layer["name"] for layer in meta["layers"]])
        # Repaired: nothing left to read as UTF-8.
        self.assertEqual(core_views._cbl_free_dwg_misdeclared_utf8_v1(meta["notifications"]), 0)

    def test_open_api_reports_the_repaired_texts(self):
        request = RequestFactory().post(
            "/api/cblcad/free-dwg-to-dxf/?format=acadsharp-dxf&mode=free-dwg",
            {"file": SimpleUploadedFile("old.dwg", UTF8_BYTES.read_bytes(), content_type="application/acad")})
        with patch.object(core_views, "_cbl_is_free_dwg_request", return_value=True):
            response = core_views.cblcad_free_dwg_local_api(request)
        self.assertEqual(response.status_code, 200)
        result = json.loads(response.content)
        self.assertGreater(result["misdeclared_utf8_texts"], 0)
        self.assertIn("기초 F1", result["dxf"])
        self.assertIn("슬래브", result["dxf"])

    def test_notice_count(self):
        notes = [{"Message": core_views._CBL_MISDECLARED_UTF8_PREFIX_V1 + " 12"}, {"Message": "Could not read TEXT"}, "junk"]
        self.assertEqual(core_views._cbl_free_dwg_misdeclared_utf8_v1(notes), 12)
        self.assertEqual(core_views._cbl_free_dwg_misdeclared_utf8_v1(None), 0)


@skipUnless(shutil.which("node"), "node is required to execute the CAD editor helpers")
class CadDwgMisdeclaredUtf8EditorTests(SimpleTestCase):
    def test_open_message(self):
        html = _html()
        start = html.index("  function cblMisdeclaredUtf8MessageV1(count){")
        end = html.index("\n  }\n", start) + 4
        script = html[start:end] + """
process.stdout.write(JSON.stringify([cblMisdeclaredUtf8MessageV1(103), cblMisdeclaredUtf8MessageV1(0), cblMisdeclaredUtf8MessageV1(undefined)]));"""
        run = subprocess.run([shutil.which("node"), "-e", script], capture_output=True, text=True, timeout=60)
        self.assertEqual(run.returncode, 0, run.stderr)
        message, zero, missing = json.loads(run.stdout)
        self.assertIn("103곳", message)
        self.assertIn("한 번 저장하면 AutoCAD에서도", message)
        self.assertEqual((zero, missing), ("", ""))

    def test_open_shows_it_after_the_other_notices(self):
        html = _html()
        body = html[html.index("async function cblFreeDwgOpenFileObjectV1(file,handle){"):]
        body = body[:body.index("function cblNativeFilePickerSupportedV1(){")]
        notice = body[body.index("var openNotice="):]
        notice = notice[:notice.index(";")]
        self.assertLess(notice.index("cblLegacyTextLengthsMessageV1"), notice.index("cblMisdeclaredUtf8MessageV1(result.misdeclared_utf8_texts)"))
