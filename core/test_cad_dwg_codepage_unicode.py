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
from .test_cad_dwg_text_validation import EXECUTABLE, _run_writer

try:
    import ezdxf
except ImportError:  # pragma: no cover - verification dependency only
    ezdxf = None

# ANSI_1252 drawing (ezdxf + ODA): Korean has no byte in its code page.
ANSI_FIXTURE = Path(settings.BASE_DIR) / "core" / "test_fixtures" / "cad" / "truecolor_oda_ac1018.dwg"
ODA = core_views._cbl_v29_find_oda()
LAYER = "한글레이어"
TEXT = "검증 한글 ABC"


def _names(meta):
    return ([l["name"] for l in meta["layers"]], [e.get("text") for e in meta["entities"] if e.get("text")])


def _runtime_dxf_bytes(dwg, tmp):
    # DWG sections are compressed; the runtime's DXF uses the same code page
    # encoding, so it shows how strings are stored.
    dxf = tmp / (dwg.stem + ".dxf")
    run = subprocess.run([str(EXECUTABLE), "--dxf", str(dwg), str(dxf)], capture_output=True, timeout=300)
    if run.returncode != 0:
        raise AssertionError(run.stderr.decode("utf-8", "replace")[-800:])
    return dxf.read_bytes()


@skipUnless(EXECUTABLE is not None, "ACadSharp runtime is required")
class CadNonKoreanCodePageTests(SimpleTestCase):
    """A drawing whose code page has no Korean stores Korean as \\U+XXXX, like AutoCAD, instead of "??"."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cbl-codepage-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def save_korean(self):
        ops = [{"type": "create_layer", "name": LAYER, "color": 3},
               {"type": "add_line", "layer": LAYER, "start": [0, 60, 0], "end": [100, 60, 0]},
               {"type": "add_text", "layer": "0", "text": TEXT, "insert": [0, 80, 0], "height": 5}]
        ops = core_views._cbl_normalize_free_dwg_ops_v1(core_views._cbl_free_dwg_save_local_json_v1(ANSI_FIXTURE, None), ops)
        ops_path = self.tmp / "ops.json"
        ops_path.write_text(json.dumps({"ops": ops}, ensure_ascii=False), encoding="utf-8")
        output = self.tmp / "saved.dwg"
        report = _run_writer([ANSI_FIXTURE, output, "AC1018", ops_path])
        core_views._cbl_free_dwg_save_local_validate_v1(ANSI_FIXTURE, output, None, ops, report)
        self.assertEqual((report["sourceHeaderCodePage"].lower(), report["rereadHeaderCodePage"].lower()),
                         ("ansi_1252", "ansi_1252"))
        return output

    def test_korean_layer_and_text_save_and_read_back(self):
        output = self.save_korean()
        layers, texts = _names(core_views._cbl_free_dwg_acadsharp_metadata_v1(output))
        self.assertIn(LAYER, layers)
        self.assertEqual(texts, [TEXT])
        raw = _runtime_dxf_bytes(output, self.tmp)
        self.assertIn(b"\\U+D55C\\U+AE00\\U+B808\\U+C774\\U+C5B4", raw)  # 한글레이어
        self.assertNotIn(b"?????", raw)

    def test_open_sends_the_editor_korean_names(self):
        output = self.save_korean()
        request = RequestFactory().post(
            "/api/cblcad/free-dwg-to-dxf/?format=acadsharp-dxf&mode=free-dwg",
            {"file": SimpleUploadedFile("saved.dwg", output.read_bytes(), content_type="application/acad")})
        with patch.object(core_views, "_cbl_is_free_dwg_request", return_value=True):
            response = core_views.cblcad_free_dwg_local_api(request)
        self.assertEqual(response.status_code, 200, response.content[:400])
        data = json.loads(response.content)
        self.assertIn("\n" + LAYER + "\n", data["dxf"].replace("\r\n", "\n"))
        self.assertIn(TEXT, data["dxf"])
        self.assertNotIn("\\U+D55C", data["dxf"])
        self.assertIn(LAYER, [l["name"] for l in data["source_layer_manifest"]["layers"]])

    @skipUnless(ODA is not None and ezdxf is not None, "ODA File Converter is only used as an independent local check")
    def test_oda_reads_the_korean_names(self):
        output = self.save_korean()
        src, out = self.tmp / "oda-in", self.tmp / "oda-out"
        src.mkdir()
        out.mkdir()
        shutil.copy(output, src / "saved.dwg")
        # Audit off: ODA's audit renames non-ASCII layer names by itself.
        subprocess.run([ODA, str(src), str(out), "ACAD2018", "DXF", "0", "0", "*.DWG"], capture_output=True, timeout=300)
        doc = ezdxf.readfile(str(out / "saved.dxf"))
        self.assertIn(LAYER, [l.dxf.name for l in doc.layers])
        self.assertEqual([e.dxf.text for e in doc.modelspace() if e.dxftype() == "TEXT"], [TEXT])
        self.assertEqual([e.dxf.layer for e in doc.modelspace() if e.dxftype() == "LINE"][-1], LAYER)

    def test_korean_code_page_drawing_keeps_plain_korean_bytes(self):
        # Korean is in KS C 5601, so nothing is escaped there.
        create = self.tmp / "create.json"
        create.write_text(json.dumps({"ops": [
            {"type": "create_layer", "name": LAYER, "color": 3},
            {"type": "add_text", "layer": LAYER, "text": TEXT, "insert": [0, 0, 0], "height": 5},
        ]}, ensure_ascii=False), encoding="utf-8")
        output = self.tmp / "korean.dwg"
        _run_writer(["--create", output, "AC1018", create])
        raw = _runtime_dxf_bytes(output, self.tmp)
        self.assertNotIn(b"\\U+", raw)
        self.assertIn(LAYER.encode("cp949"), raw)
        layers, texts = _names(core_views._cbl_free_dwg_acadsharp_metadata_v1(output))
        self.assertIn(LAYER, layers)
        self.assertEqual(texts, [TEXT])


class CadUnicodeEscapeDecodeTests(SimpleTestCase):
    def test_decodes_non_ascii_escapes_only(self):
        decode = core_views._cbl_decode_dxf_unicode_escapes_v1
        self.assertEqual(decode("\\U+D55C\\U+AE00 A"), "한글 A")
        self.assertEqual(decode("a\\U+000Ab\\U+005C\\U+0041"), "a\\U+000Ab\\U+005C\\U+0041")  # never makes DXF syntax
        self.assertEqual(decode("\\U+D83D\\U+DE00"), "\\U+D83D\\U+DE00")  # lone surrogates stay escaped
        self.assertEqual(decode("\\u+ac00 \\U+ac00"), "\\u+ac00 가")
        self.assertEqual(decode("plain"), "plain")
