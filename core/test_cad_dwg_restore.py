import io
import json
import math
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
from .test_cad_dwg_text_validation import EXECUTABLE

FIXTURES = Path(settings.BASE_DIR) / "core" / "test_fixtures" / "cad"
KINDS = FIXTURES / "move_kinds_ac1032.dwg"
ATTR = FIXTURES / "insert_attr_ac1018.dwg"
# SPLINE, ELLIPSE, HATCH, LEADER, DIMENSION and MULTILEADER of move_kinds.
RESTORED = ["8B", "8D", "92", "91", "93", "B1"]


def _r(point):
    return (round(point[0], 4), round(point[1], 4))


@skipUnless(EXECUTABLE is not None, "ACadSharp runtime is required")
class CadDwgRestoreTests(SimpleTestCase):
    """An object deleted by an earlier save and brought back by undo keeps its kind.

    The save that deleted it removed it from the DWG, so the writer could
    not clone it: a SPLINE came back as a polyline, a DIMENSION or HATCH
    stopped the save ("새 치수"), a block with attributes was refused.  The
    editor now sends the drawing as it was opened ("restore_dwg") and the
    writer clones the object from there ("fromOpened").
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cbl-restore-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def save(self, original, ops, restore=None):
        fields = {"ops": json.dumps({"ops": ops}), "target_version": "AC1018", "filename": "plan.dwg", "delivery": "binary",
                  "original_dwg": SimpleUploadedFile("plan.dwg", original, content_type="application/acad")}
        if restore is not None:
            fields["restore_dwg"] = SimpleUploadedFile("opened.dwg", restore, content_type="application/acad")
        request = RequestFactory().post("/api/cblcad/free-dwg-save/?mode=free-dwg", fields)
        with patch.object(core_views, "_cbl_is_free_dwg_request", return_value=True), \
                patch.object(core_views, "_cbl_free_dwg_local_find_dwgread_v1", return_value=None):
            return core_views.cblcad_free_dwg_save_local_api(request)

    def read(self, data, name):
        import ezdxf

        dwg, dxf = self.tmp / (name + ".dwg"), self.tmp / (name + ".dxf")
        dwg.write_bytes(data)
        run = subprocess.run([str(EXECUTABLE), "--dxf", str(dwg), str(dxf)], capture_output=True, timeout=300)
        self.assertEqual(run.returncode, 0, run.stderr[-800:])
        return ezdxf.read(io.StringIO(dxf.read_bytes().decode("cp949", errors="replace")))

    def deleted(self, source, handles):
        response = self.save(source.read_bytes(), [{"type": "delete", "handle": h, "sourceHandle": h} for h in handles])
        self.assertEqual(response.status_code, 200, response.content[:400])
        return response.content

    def test_restored_objects_keep_their_kind(self):
        without = self.deleted(KINDS, RESTORED)
        ops = [{"type": "add_copy", "copyOf": h, "fromOpened": True, "matrix": [1, 0, 0, 1, 0, 0], "clientShapeId": "r%d" % i}
               for i, h in enumerate(RESTORED)]
        # One comes back moved (undo, then a move before the save).
        ops[0]["matrix"] = [1, 0, 0, 1, 50, 0]
        response = self.save(without, ops, restore=KINDS.read_bytes())
        self.assertEqual(response.status_code, 200, response.content[:400])
        self.assertEqual(response["X-CBL-FREE-DWG-SAVE-VALIDATED"], "1")
        opened, saved = self.read(KINDS.read_bytes(), "opened").entitydb, self.read(response.content, "saved")
        handles = json.loads(response["X-CBL-FREE-DWG-OUTPUT-HANDLES"])
        db = saved.entitydb
        for index, source in enumerate(RESTORED):
            back = db[handles[str(index)]]
            with self.subTest(opened[source].dxftype()):
                self.assertEqual(back.dxftype(), opened[source].dxftype())
        spline = db[handles["0"]]
        self.assertEqual([_r(p) for p in spline.control_points], [(round(p[0] + 50, 4), round(p[1], 4)) for p in opened["8B"].control_points])
        dimension = db[handles["4"]]
        self.assertEqual([e.plain_text() for e in dimension.get_geometry_block() if e.dxftype() == "MTEXT"], ["20000"])
        self.assertEqual(len(saved.modelspace().query("HATCH")), 1)

    def test_a_restored_block_keeps_its_attribute_values(self):
        without = self.deleted(ATTR, ["34"])
        op = {"type": "add_insert", "entity": "INSERT", "copyOf": "34", "fromOpened": True, "blockName": "BLK", "layer": "0",
              "insert": [100, 100, 0], "rotation": 0, "scale": [1, 1, 1], "clientShapeId": "r0"}
        response = self.save(without, [op], restore=ATTR.read_bytes())
        self.assertEqual(response.status_code, 200, response.content[:400])
        inserts = self.read(response.content, "saved").modelspace().query("INSERT")
        self.assertEqual([(a.dxf.tag, a.dxf.text, _r(a.dxf.insert)) for i in inserts for a in i.attribs], [("TAG", "ROOM-1", (100, 85))])

    def test_without_the_opened_drawing_the_save_is_refused(self):
        without = self.deleted(KINDS, ["8B"])
        response = self.save(without, [{"type": "add_copy", "copyOf": "8B", "fromOpened": True, "matrix": [1, 0, 0, 1, 0, 0], "clientShapeId": "r0"}])
        self.assertEqual(response.status_code, 400)
        self.assertIn("처음 연 도면", json.loads(response.content)["error"])
