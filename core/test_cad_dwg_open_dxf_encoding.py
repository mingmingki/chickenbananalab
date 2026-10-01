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


@skipUnless(EXECUTABLE is not None, "ACadSharp runtime is required")
class CadOpenDxfExportTests(SimpleTestCase):
    def test_dxf_export_does_not_read_its_output_back(self):
        # Reading the DXF back only fed a report nothing uses, and took most of
        # the open time on large drawings (9 of 15 s on a 6.4 MB plan).  The
        # open API checks the file itself and the editor parses it.
        tmp = Path(tempfile.mkdtemp(prefix="cbl-dxf-export-"))
        try:
            import subprocess
            run = subprocess.run([str(EXECUTABLE), "--dxf", str(WESTERN), str(tmp / "out.dxf")], capture_output=True, timeout=300)
            self.assertEqual(run.returncode, 0, run.stderr[-400:])
            report = json.loads(run.stdout.decode("utf-8", "replace"), strict=False)
            self.assertEqual(report["status"], "dxf_written")
            self.assertNotIn("reread", report)
            self.assertTrue(core_views._cbl_free_dwg_local_valid_dxf_v1(tmp / "out.dxf"))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_dxf_export_can_write_the_metadata_from_the_same_read(self):
        # One DWG read instead of two for the open API (2.3 of 7 s on a 6.4 MB plan).
        import subprocess
        tmp = Path(tempfile.mkdtemp(prefix="cbl-dxf-meta-"))
        try:
            for source in (WESTERN, FIXTURES / "korean_xrecord_ac1018.dwg", FIXTURES / "codepage45_ac1032.dwg"):
                run = subprocess.run([str(EXECUTABLE), "--dxf", str(source), str(tmp / "out.dxf"), str(tmp / "meta.json")],
                                     capture_output=True, timeout=300)
                self.assertEqual(run.returncode, 0, run.stderr[-400:])
                combined = json.loads((tmp / "meta.json").read_text(encoding="utf-8"), strict=False)
                self.assertEqual(combined, core_views._cbl_free_dwg_acadsharp_metadata_v1(source), source.name)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


@skipUnless(EXECUTABLE is not None, "ACadSharp runtime is required")
class CadOpenApiSpeedTests(SimpleTestCase):
    def test_open_api_reads_the_drawing_once(self):
        def separate_read(path):
            raise AssertionError("the open API ran a second metadata read")
        with patch.object(core_views, "_cbl_free_dwg_acadsharp_metadata_v1", side_effect=separate_read):
            dxf = _open(WESTERN)
        self.assertIn("Ø25 ±0.5 50°C café", dxf)

    def test_open_api_response_is_gzipped(self):
        # nginx compresses only text/html; the DXF JSON (30 MB on a large plan)
        # shrinks about 18x.
        import gzip
        request = RequestFactory().post(
            "/api/cblcad/free-dwg-to-dxf/?format=acadsharp-dxf&mode=free-dwg",
            {"file": SimpleUploadedFile(WESTERN.name, WESTERN.read_bytes(), content_type="application/acad")},
            HTTP_ACCEPT_ENCODING="gzip, deflate, br")
        with patch.object(core_views, "_cbl_is_free_dwg_request", return_value=True):
            response = core_views.cblcad_free_dwg_local_api(request)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get("Content-Encoding"), "gzip")
        data = json.loads(gzip.decompress(response.content))
        self.assertIn("Ø25 ±0.5 50°C café", data["dxf"])
        self.assertTrue(getattr(core_views.cblcad_free_dwg_local_api, "csrf_exempt", False))

