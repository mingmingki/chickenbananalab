import io
import json
from pathlib import Path
from unittest import skipUnless
from unittest.mock import patch

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, SimpleTestCase

from . import views as core_views
from .test_cad_dwg_save_integrity import _html
from .test_cad_dwg_text_validation import EXECUTABLE

FIXTURES = Path(settings.BASE_DIR) / "core" / "test_fixtures" / "cad"
# Slab outlines, a wall line, two INSERTs of block "C1", Korean TEXT/MTEXT and
# a linear dimension (ezdxf drawing written by ACadSharp, KS C 5601).
PLAN = FIXTURES / "quantity_plan_ac1018.dwg"


def _read_dxf(data):
    import ezdxf
    return ezdxf.read(io.StringIO(data.decode("cp949")))


@skipUnless(EXECUTABLE is not None, "ACadSharp runtime is required")
class CadDxfSaveApiTests(SimpleTestCase):
    """"DXF 저장" goes through the DWG save and converts the validated result.

    The editor's own DXF writer flattened blocks and dimensions and wrote UTF-8
    under an ANSI_949 header.
    """

    def save(self, ops, original=PLAN, name="plan.dwg"):
        fields = {"ops": json.dumps({"ops": ops}), "target_version": "AC1018", "filename": name,
                  "download_name": name, "delivery": "dxf"}
        if original is not None:
            fields["original_dwg"] = SimpleUploadedFile(name, original.read_bytes(), content_type="application/acad")
        request = RequestFactory().post("/api/cblcad/free-dwg-save/?mode=free-dwg", fields)
        with patch.object(core_views, "_cbl_is_free_dwg_request", return_value=True):
            return core_views.cblcad_free_dwg_save_local_api(request)

    def test_dxf_keeps_blocks_dimensions_and_korean(self):
        response = self.save([])
        self.assertEqual(response.status_code, 200, response.content[:300])
        self.assertEqual(response["Content-Type"], "application/dxf")
        self.assertIn("plan.dxf", response["Content-Disposition"])
        self.assertEqual(response["X-CBL-FREE-DWG-SAVE-VALIDATED"], "1")
        doc = _read_dxf(response.content)
        self.assertEqual(doc.header["$DWGCODEPAGE"], "ANSI_949")
        msp = doc.modelspace()
        self.assertEqual({e.dxf.name for e in msp.query("INSERT")}, {"C1"})
        self.assertEqual(len(doc.blocks.get("C1").query("*")), 1)
        self.assertEqual(len(msp.query("DIMENSION")), 1)
        texts = [e.dxf.text for e in msp.query("TEXT")] + [e.text for e in msp.query("MTEXT")]
        self.assertTrue(any("기초" in t for t in texts), texts)

    def test_edits_are_in_the_dxf(self):
        meta = core_views._cbl_free_dwg_acadsharp_metadata_v1(PLAN)
        line = next(e for e in meta["entities"] if e.get("space") == "modelspace" and e["type"] == "LINE")
        before = _read_dxf(self.save([]).content).entitydb[line["handle"]]
        response = self.save([{"type": "move", "handle": line["handle"], "delta": [0, 250, 0]}])
        self.assertEqual(response.status_code, 200, response.content[:300])
        after = _read_dxf(response.content).entitydb[line["handle"]]
        self.assertAlmostEqual(after.dxf.start.y - before.dxf.start.y, 250)

    def test_objects_the_dxf_cannot_hold_are_reported(self):
        # ACadSharp's DXF writer has no REGION; the DWG keeps it.
        response = self.save([], original=FIXTURES / "region_acds_ac1032.dwg", name="region.dwg")
        self.assertEqual(response.status_code, 200, response.content[:300])
        self.assertEqual(json.loads(response["X-CBL-DXF-SKIPPED"]), {"REGION": 1})
        clean = self.save([])
        self.assertEqual(json.loads(clean["X-CBL-DXF-SKIPPED"]), {})

    def test_a_new_drawing_saves_as_dxf(self):
        ops = [{"type": "add_line", "layer": "기본", "color": 7, "start": [0, 0, 0], "end": [1000, 0, 0], "clientShapeId": "a"}]
        response = self.save(ops, original=None, name="ChickenBananaCAD.dwg")
        self.assertEqual(response.status_code, 200, response.content[:300])
        doc = _read_dxf(response.content)
        self.assertIn("기본", [layer.dxf.name for layer in doc.layers])
        self.assertEqual(len(doc.modelspace().query("LINE")), 1)


class CadDxfSaveEditorTests(SimpleTestCase):
    def test_free_mode_dxf_save_buttons_use_the_server(self):
        html = _html()
        bottom = html[html.index("  window.cblBottomSaveDXF = function(){\n    // Free mode"):]
        self.assertIn("return window.cblFreeDwgExportDxfV1();", bottom[:bottom.index("var pack = makeSavePack();")])
        menu = html[html.index("window.fileSaveDXF=function(){if("):]
        menu = menu[:menu.index("};") + 2]
        self.assertIn("return window.cblFreeDwgExportDxfV1();", menu)
        self.assertIn("return window.cblSimpleDxfSaveV1();", menu)
        # The simple export stays for other modes and as the fallback.
        self.assertIn("window.cblSimpleDxfSaveV1=function(){try{var dxf=buildDXF();", html)

    @skipUnless(__import__("shutil").which("node"), "node is required to execute the CAD editor helpers")
    def test_skipped_objects_message(self):
        import shutil
        import subprocess
        html = _html()
        start = html.index("  function cblDxfSkippedMessageV1(header){")
        end = html.index("\n  }\n", start) + 4
        script = html[start:end] + """
process.stdout.write(JSON.stringify([cblDxfSkippedMessageV1('{"REGION": 54}'), cblDxfSkippedMessageV1('{}'), cblDxfSkippedMessageV1(null), cblDxfSkippedMessageV1('junk')]));"""
        run = subprocess.run([shutil.which("node"), "-e", script], capture_output=True, text=True, timeout=60)
        self.assertEqual(run.returncode, 0, run.stderr)
        message, empty, missing, junk = json.loads(run.stdout)
        self.assertIn("REGION 54개", message)
        self.assertIn("DWG", message)
        self.assertEqual((empty, missing, junk), ("", "", ""))

    def test_dxf_export_leaves_the_drawing_baseline_alone(self):
        html = _html()
        body = html[html.index("window.cblFreeDwgExportDxfV1=async function(){"):]
        body = body[:body.index("\n  };\n")]
        self.assertIn("fd.append('delivery','dxf')", body)
        for forbidden in ("cblCommitSuccessfulDwgSaveV1", "applyOutputHandlesV1", "prepareDwgSaveCommitV1",
                          "CBL_FREE_DWG_LOCAL_FILE_TOKEN"):
            self.assertNotIn(forbidden, body)
