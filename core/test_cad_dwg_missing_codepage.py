import json
import shutil
import struct
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
from .test_cad_dwg_xrecord_text import _mif
from .test_oda_review import find_oda

try:
    import ezdxf
except ImportError:  # pragma: no cover - verification dependency only
    ezdxf = None

FIXTURES = Path(settings.BASE_DIR) / "core" / "test_fixtures" / "cad"
# ezdxf + ODA drawings (layer TC: one LINE, TEXT "ABC") whose file-header code
# page index was set to 45, which neither ACadSharp nor ODA lists.  Real AC1015
# and AC1032 drawings carry it; ACadSharp reads their strings as UTF-8 and left
# Header.CodePage null, so opening (DXF) and saving failed with a
# NullReferenceException.
MISSING_CODE_PAGE = [FIXTURES / "codepage45_ac1015.dwg", FIXTURES / "codepage45_ac1032.dwg"]
ODA = find_oda()


@skipUnless(EXECUTABLE is not None, "ACadSharp runtime is required")
class CadMissingCodePageSaveTests(SimpleTestCase):
    """A drawing whose code page index is unknown opens and saves with the Korean code page."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cbl-missing-codepage-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def save(self, source, ops):
        ops = core_views._cbl_normalize_free_dwg_ops_v1(core_views._cbl_free_dwg_save_local_json_v1(source, None), ops)
        ops_path = self.tmp / "ops.json"
        ops_path.write_text(json.dumps({"ops": ops}, ensure_ascii=False), encoding="utf-8")
        output = self.tmp / (source.stem + "-saved.dwg")
        report = _run_writer([source, output, "AC1018", ops_path])
        core_views._cbl_free_dwg_save_local_validate_v1(source, output, None, ops, report)
        return output, report

    def test_fixtures_carry_code_page_index_45(self):
        for source in MISSING_CODE_PAGE:
            self.assertEqual(struct.unpack("<H", source.read_bytes()[0x13:0x15])[0], 45, source.name)

    def test_open_api_converts_the_drawing(self):
        for source in MISSING_CODE_PAGE:
            with self.subTest(source=source.name):
                request = RequestFactory().post(
                    "/api/cblcad/free-dwg-to-dxf/?format=acadsharp-dxf&mode=free-dwg",
                    {"file": SimpleUploadedFile(source.name, source.read_bytes(), content_type="application/acad")})
                with patch.object(core_views, "_cbl_is_free_dwg_request", return_value=True):
                    response = core_views.cblcad_free_dwg_local_api(request)
                self.assertEqual(response.status_code, 200, response.content[:400])
                data = json.loads(response.content)
                self.assertIn("ABC", data["dxf"])
                self.assertIn("TC", [l["name"] for l in data["source_layer_manifest"]["layers"]])

    def test_unedited_save_succeeds(self):
        for source in MISSING_CODE_PAGE:
            with self.subTest(source=source.name):
                output, report = self.save(source, [])
                self.assertEqual(str(report["rereadHeaderCodePage"]).lower(), "kcs5601")
                meta = core_views._cbl_free_dwg_acadsharp_metadata_v1(output)
                self.assertEqual([e.get("text") for e in meta["entities"] if e.get("text")], ["ABC"])

    def test_korean_layer_and_text_are_kept(self):
        for source in MISSING_CODE_PAGE:
            with self.subTest(source=source.name):
                output, _ = self.save(source, [
                    {"type": "create_layer", "name": "한글레이어", "color": 3},
                    {"type": "add_text", "layer": "한글레이어", "text": "검증 한글", "insert": [0, 20, 0], "height": 5},
                ])
                meta = core_views._cbl_free_dwg_acadsharp_metadata_v1(output)
                self.assertIn("한글레이어", [l["name"] for l in meta["layers"]])
                self.assertEqual(sorted(e.get("text") for e in meta["entities"] if e.get("text")), ["ABC", "검증 한글"])

    @skipUnless(ODA is not None and ezdxf is not None, "ODA File Converter is only used as an independent local check")
    def test_oda_reads_the_saved_drawing(self):
        output, _ = self.save(MISSING_CODE_PAGE[1], [
            {"type": "add_text", "layer": "TC", "text": "검증 한글", "insert": [0, 20, 0], "height": 5},
        ])
        src, out = self.tmp / "oda-in", self.tmp / "oda-out"
        src.mkdir()
        out.mkdir()
        shutil.copy(output, src / "saved.dwg")
        subprocess.run([ODA, str(src), str(out), "ACAD2018", "DXF", "0", "0", "*.DWG"], capture_output=True, timeout=300)
        doc = ezdxf.readfile(str(out / "saved.dxf"))
        # The writer stores KS C 5601 as index 25 (as for every save and new drawing).
        self.assertEqual(doc.header.get("$DWGCODEPAGE"), "KSC5601")
        self.assertEqual(sorted(_mif(e.dxf.text) for e in doc.modelspace() if e.dxftype() == "TEXT"), ["ABC", "검증 한글"])
