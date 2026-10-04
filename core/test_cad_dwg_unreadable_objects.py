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
from .test_cad_dwg_save_integrity import NODE, _html
from .test_cad_dwg_text_validation import EXECUTABLE, _run_writer
from .test_oda_review import find_oda

try:
    import ezdxf
except ImportError:  # pragma: no cover - verification dependency only
    ezdxf = None

FIXTURES = Path(settings.BASE_DIR) / "core" / "test_fixtures" / "cad"
# ezdxf + ODA drawing whose TEXT "ZERO" and "" were given height 0 by a
# throwaway ACadSharp program (AutoCAD reads 0 as "use the style height";
# ODA reads them).  ACadSharp's Height setter threw on 0, so the reader
# skipped them ("Could not read TEXT") and every save dropped them.
HEIGHT_ZERO = FIXTURES / "text_height_zero_ac1018.dwg"
ODA = find_oda()


def _texts(meta):
    return sorted((e.get("text"), e["insert"]["height"]) for e in meta["entities"] if e["type"] == "TEXTENTITY")


@skipUnless(EXECUTABLE is not None, "ACadSharp runtime is required")
class CadTextHeightZeroTests(SimpleTestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cbl-height-zero-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_text_with_height_zero_is_read(self):
        meta = core_views._cbl_free_dwg_acadsharp_metadata_v1(HEIGHT_ZERO)
        self.assertEqual(_texts(meta), [("", 0), ("NORMAL", 5), ("ZERO", 0)])
        self.assertEqual(core_views._cbl_free_dwg_unreadable_objects_v1(meta["notifications"]), {})

    def test_unedited_save_keeps_text_with_height_zero(self):
        ops_path = self.tmp / "ops.json"
        ops_path.write_text(json.dumps({"ops": []}), encoding="utf-8")
        output = self.tmp / "saved.dwg"
        report = _run_writer([HEIGHT_ZERO, output, "AC1018", ops_path])
        core_views._cbl_free_dwg_save_local_validate_v1(HEIGHT_ZERO, output, None, [], report)
        self.assertEqual(_texts(core_views._cbl_free_dwg_acadsharp_metadata_v1(output)), [("", 0), ("NORMAL", 5), ("ZERO", 0)])
        if ODA and ezdxf:
            src, out = self.tmp / "oda-in", self.tmp / "oda-out"
            src.mkdir()
            out.mkdir()
            shutil.copy(output, src / "saved.dwg")
            subprocess.run([ODA, str(src), str(out), "ACAD2018", "DXF", "0", "0", "*.DWG"], capture_output=True, timeout=300)
            doc = ezdxf.readfile(str(out / "saved.dxf"))
            self.assertEqual(sorted((e.dxf.text, e.dxf.height) for e in doc.modelspace() if e.dxftype() == "TEXT"),
                             [("", 0.0), ("NORMAL", 5.0), ("ZERO", 0.0)])


class CadUnreadableObjectGuardTests(SimpleTestCase):
    """Objects the converter cannot read would vanish on save: refuse the save and say so on open."""

    NOTIFICATIONS = [
        {"phase": "read", "type": "Error", "Message": "Could not read TEXT with handle: 59"},
        {"phase": "read", "type": "Error", "Message": "Could not read TEXT with handle: 60"},
        {"phase": "read", "type": "Error", "Message": "Could not read ACAD_PROXY_ENTITY number 500 with handle: 1A2"},
        {"phase": "read", "type": "Warning", "Message": "Unlisted object with DXF name LSDEFINITION has been read"},
        {"phase": "read", "type": "Error", "Message": "Error when trying to add the entry ACAD_SORTENTS to X"},
    ]

    def test_counts_unreadable_objects_by_type(self):
        self.assertEqual(core_views._cbl_free_dwg_unreadable_objects_v1(self.NOTIFICATIONS),
                         {"TEXT": 2, "ACAD_PROXY_ENTITY": 1})
        self.assertEqual(core_views._cbl_free_dwg_unreadable_objects_v1(None), {})

    @skipUnless(EXECUTABLE is not None, "ACadSharp runtime is required")
    def test_save_is_refused_when_the_source_had_unreadable_objects(self):
        tmp = Path(tempfile.mkdtemp(prefix="cbl-unreadable-"))
        try:
            source = FIXTURES / "arc_bulge_ac1018.dwg"
            ops_path = tmp / "ops.json"
            ops_path.write_text(json.dumps({"ops": []}), encoding="utf-8")
            output = tmp / "saved.dwg"
            report = _run_writer([source, output, "AC1018", ops_path])
            report["notifications"] = list(report.get("notifications") or []) + self.NOTIFICATIONS
            with self.assertRaises(RuntimeError) as caught:
                core_views._cbl_free_dwg_save_local_validate_v1(source, output, None, [], report)
            message = str(caught.exception)
            self.assertIn("읽지 못한 객체", message)
            self.assertIn("TEXT 2개", message)
            self.assertIn("원본 파일은 바뀌지 않았습니다", message)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    @skipUnless(EXECUTABLE is not None, "ACadSharp runtime is required")
    def test_open_api_reports_unreadable_objects(self):
        def open_with(counts):
            request = RequestFactory().post(
                "/api/cblcad/free-dwg-to-dxf/?format=acadsharp-dxf&mode=free-dwg",
                {"file": SimpleUploadedFile("x.dwg", HEIGHT_ZERO.read_bytes(), content_type="application/acad")})
            with patch.object(core_views, "_cbl_is_free_dwg_request", return_value=True), \
                    patch.object(core_views, "_cbl_free_dwg_unreadable_objects_v1", return_value=counts):
                response = core_views.cblcad_free_dwg_local_api(request)
            self.assertEqual(response.status_code, 200)
            return json.loads(response.content)["unreadable_objects"]
        self.assertEqual(open_with({"TEXT": 2}), {"TEXT": 2})
        self.assertEqual(open_with({}), {})


@skipUnless(NODE, "node is required to execute the CAD editor helpers")
class CadUnreadableObjectEditorTests(SimpleTestCase):
    def test_open_message(self):
        html = _html()
        start = html.index("function cblUnreadableObjectsMessageV1(counts){")
        line_start = html.rfind("\n", 0, start) + 1
        indent = html[line_start:start]
        end = html.index("\n" + indent + "}", start) + len(indent) + 2
        script = html[line_start:end] + """
process.stdout.write(JSON.stringify([cblUnreadableObjectsMessageV1({TEXT: 359, MTEXT: 1}),
                                     cblUnreadableObjectsMessageV1({}), cblUnreadableObjectsMessageV1(null)]));"""
        run = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=60)
        self.assertEqual(run.returncode, 0, run.stderr)
        message, empty, missing = json.loads(run.stdout)
        self.assertIn("TEXT 359개", message)
        self.assertIn("MTEXT 1개", message)
        self.assertIn("저장할 수 없습니다", message)
        self.assertEqual((empty, missing), ("", ""))

    def test_open_warns_after_the_drawing_is_shown(self):
        html = _html()
        body = html[html.index("async function cblFreeDwgOpenFileObjectV1(file,handle){"):]
        body = body[:body.index("function cblNativeFilePickerSupportedV1(){")]
        done = body.index("setHint('DWG 열기 완료: '")
        warn = body.index("cblUnreadableObjectsMessageV1(result.unreadable_objects)")
        self.assertLess(done, warn)
