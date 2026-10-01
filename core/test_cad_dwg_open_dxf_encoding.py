import json
import shutil
import tempfile
from pathlib import Path
from unittest import skipUnless
from unittest.mock import patch

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, SimpleTestCase

from . import views as core_views
from .test_cad_dwg_text_validation import EXECUTABLE, _run_writer, _text_op

FIXTURES = Path(settings.BASE_DIR) / "core" / "test_fixtures" / "cad"
# ezdxf + ODA ANSI_1252 drawing: TEXT "Ø25 ±0.5 50°C café" on layer 0.
WESTERN = FIXTURES / "western_ansi_ac1018.dwg"


def _open(path):
    request = RequestFactory().post(
        "/api/cblcad/free-dwg-to-dxf/?format=acadsharp-dxf&mode=free-dwg",
        {"file": SimpleUploadedFile(path.name, path.read_bytes(), content_type="application/acad")})
    with patch.object(core_views, "_cbl_is_free_dwg_request", return_value=True):
        response = core_views.cblcad_free_dwg_local_api(request)
    assert response.status_code == 200, response.content[:400]
    return json.loads(response.content)["dxf"]


@skipUnless(EXECUTABLE is not None, "ACadSharp runtime is required")
class CadOpenDxfEncodingTests(SimpleTestCase):
    """The DXF sent to the editor is decoded with the drawing's code page, not only as UTF-8."""

    def test_western_characters_reach_the_editor(self):
        dxf = _open(WESTERN)
        self.assertIn("\nØ25 ±0.5 50°C café\n", dxf.replace("\r\n", "\n"))
        self.assertNotIn("�", dxf)

    def test_edited_western_text_is_saved(self):
        tmp = Path(tempfile.mkdtemp(prefix="cbl-western-"))
        try:
            ops = [_text_op("update", "Ø30 ±1.0 60°C crème", 0, 20, handle="2F", sourceHandle="2F")]
            ops = core_views._cbl_normalize_free_dwg_ops_v1(core_views._cbl_free_dwg_save_local_json_v1(WESTERN, None), ops)
            ops_path = tmp / "ops.json"
            ops_path.write_text(json.dumps({"ops": ops}, ensure_ascii=False), encoding="utf-8")
            output = tmp / "saved.dwg"
            report = _run_writer([WESTERN, output, "AC1018", ops_path])
            core_views._cbl_free_dwg_save_local_validate_v1(WESTERN, output, None, ops, report)
            self.assertIn("\nØ30 ±1.0 60°C crème\n", _open(output).replace("\r\n", "\n"))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_korean_code_page_strings_reach_the_editor(self):
        dxf = _open(FIXTURES / "korean_xrecord_ac1018.dwg")
        self.assertIn("\n한글 확장데이터 ABC\n", dxf.replace("\r\n", "\n"))
        self.assertNotIn("�", dxf)


class CadDxfCodePageCodecTests(SimpleTestCase):
    def test_runtime_code_page_names(self):
        codec = core_views._cbl_dxf_codepage_codec_v1
        self.assertEqual([codec(x) for x in ("kcs5601", "ANSI_949", "ansi_1252", "ansi1250", "dos850", "dos950",
                                             "gb2312", "big5", "iso8859-1", "iso88592", "johab", "", None, "nonsense")],
                         ["cp949", "cp949", "cp1252", "cp1250", "cp850", "cp950",
                          "gbk", "cp950", "iso8859-1", "iso8859-2", "johab", None, None, None])

    def test_version_picks_utf8_or_code_page(self):
        text = core_views._cbl_free_dwg_dxf_text_v1
        head = "  0\nSECTION\n  2\nHEADER\n  9\n$ACADVER\n  1\n%s\n  9\n$DWGCODEPAGE\n  3\n%s\n"
        self.assertEqual(text((head % ("AC1018", "ansi_1252")).encode("ascii") + "café".encode("cp1252")).splitlines()[-1], "café")
        self.assertEqual(text((head % ("AC1018", "kcs5601")).encode("ascii") + "한글".encode("cp949")).splitlines()[-1], "한글")
        self.assertEqual(text((head % ("AC1032", "ansi_1252")).encode("ascii") + "café 한글".encode("utf-8")).splitlines()[-1], "café 한글")
        # Unknown code page: UTF-8 as before, invalid bytes replaced.
        self.assertEqual(text((head % ("AC1018", "")).encode("ascii") + b"caf\xe9").splitlines()[-1], "caf�")
