import json
import math
import shutil
import subprocess
import tempfile
from pathlib import Path
from unittest import skipUnless

from django.conf import settings
from django.test import SimpleTestCase

from . import views as core_views

try:
    import ezdxf
except ImportError:  # pragma: no cover - verification dependency only
    ezdxf = None

EXECUTABLE = core_views._cbl_free_dwg_save_local_executable_v1()
FIXTURE = Path(settings.BASE_DIR) / "core" / "test_fixtures" / "cad" / "arc_bulge_ac1018.dwg"
# Handles in the fixture: LINE 2F, ARC 30 (c=50,50 r=20 30°..150°),
# LWPOLYLINE 31 [(0,100),(40,100,bulge 1),(80,100),(80,140)].


@skipUnless(EXECUTABLE is not None and ezdxf is not None, "ACadSharp runtime and ezdxf are required")
class CadDwgArcAndBulgeSaveTests(SimpleTestCase):
    """The DWG writer must add/update ARC entities and keep LWPOLYLINE bulges."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cbl-arc-bulge-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def save(self, ops):
        ops_path = self.tmp / "ops.json"
        ops_path.write_text(json.dumps({"ops": ops}), encoding="utf-8")
        output = self.tmp / "out.dwg"
        run = subprocess.run([str(EXECUTABLE), str(FIXTURE), str(output), "AC1018", str(ops_path)],
                             capture_output=True, timeout=300)
        self.assertEqual(run.returncode, 0, run.stderr.decode("utf-8", "replace")[-800:])
        dxf = self.tmp / "out.dxf"
        run = subprocess.run([str(EXECUTABLE), "--dxf", str(output), str(dxf)], capture_output=True, timeout=300)
        self.assertEqual(run.returncode, 0, run.stderr.decode("utf-8", "replace")[-800:])
        return ezdxf.readfile(str(dxf)).modelspace()

    def arcs(self, msp):
        return sorted(((round(a.dxf.center.x, 6), round(a.dxf.center.y, 6), round(a.dxf.radius, 6),
                        round(a.dxf.start_angle, 6), round(a.dxf.end_angle, 6)) for a in msp.query("ARC")))

    def test_add_arc_writes_center_radius_and_angles(self):
        msp = self.save([{"type": "add_arc", "layer": "0", "center": [200, 50, 0], "radius": 10,
                          "startAngle": math.pi / 2, "endAngle": math.pi}])
        self.assertEqual(self.arcs(msp), [(50.0, 50.0, 20.0, 30.0, 150.0), (200.0, 50.0, 10.0, 90.0, 180.0)])

    def test_update_arc_changes_geometry_and_angles(self):
        msp = self.save([{"type": "update", "handle": "30", "entity": "ARC", "layer": "0",
                          "center": [60, 70, 0], "radius": 25, "startAngle": 0, "endAngle": math.pi / 3}])
        self.assertEqual(self.arcs(msp), [(60.0, 70.0, 25.0, 0.0, 60.0)])

    def test_move_arc_keeps_angles(self):
        msp = self.save([{"type": "move", "handle": "30", "delta": [5, -5, 0]}])
        self.assertEqual(self.arcs(msp), [(55.0, 45.0, 20.0, 30.0, 150.0)])

    def test_add_lwpolyline_with_bulges(self):
        msp = self.save([{"type": "add_lwpolyline", "layer": "0", "closed": False,
                          "points": [[0, 0, 0], [10, 0, 0], [20, 0, 0]], "bulges": [0.5, -1, 0]}])
        added = [p for p in msp.query("LWPOLYLINE") if p.dxf.handle != "31"]
        self.assertEqual(len(added), 1)
        self.assertEqual([round(b, 6) for *_, b in added[0].get_points("xyb")], [0.5, -1.0, 0.0])

    def test_update_lwpolyline_with_bulges(self):
        msp = self.save([{"type": "update", "handle": "31", "entity": "LWPOLYLINE", "layer": "0", "closed": False,
                          "points": [[10, 100, 0], [50, 100, 0], [90, 100, 0], [90, 140, 0]],
                          "bulges": [0, 1, 0, 0]}])
        poly = msp.query("LWPOLYLINE")[0]
        self.assertEqual([tuple(round(v, 6) for v in pt) for pt in poly.get_points("xyb")],
                         [(10.0, 100.0, 0.0), (50.0, 100.0, 1.0), (90.0, 100.0, 0.0), (90.0, 140.0, 0.0)])

    def test_update_without_bulges_keeps_existing_arcs_of_the_polyline(self):
        # Older clients send only points; a same-length update must not flatten arc segments.
        msp = self.save([{"type": "update", "handle": "31", "entity": "LWPOLYLINE", "layer": "0", "closed": False,
                          "points": [[10, 100, 0], [50, 100, 0], [90, 100, 0], [90, 140, 0]]}])
        poly = msp.query("LWPOLYLINE")[0]
        self.assertEqual([round(b, 6) for *_, b in poly.get_points("xyb")], [0.0, 1.0, 0.0, 0.0])

    def test_move_lwpolyline_keeps_bulges(self):
        msp = self.save([{"type": "move", "handle": "31", "delta": [0, 10, 0]}])
        poly = msp.query("LWPOLYLINE")[0]
        self.assertEqual([round(b, 6) for *_, b in poly.get_points("xyb")], [0.0, 1.0, 0.0, 0.0])
        self.assertEqual(round(poly.get_points("xy")[0][1], 6), 110.0)

    def test_new_drawing_with_only_an_arc_is_not_rejected_as_empty(self):
        ops_path = self.tmp / "ops.json"
        ops_path.write_text(json.dumps({"ops": [{"type": "add_arc", "layer": "0", "center": [0, 0, 0],
                                                 "radius": 5, "startAngle": 0, "endAngle": math.pi}]}))
        output = self.tmp / "new.dwg"
        run = subprocess.run([str(EXECUTABLE), "--create", str(output), "AC1018", str(ops_path)],
                             capture_output=True, timeout=300)
        self.assertEqual(run.returncode, 0, run.stderr.decode("utf-8", "replace")[-800:])
        meta = core_views._cbl_free_dwg_acadsharp_metadata_v1(output)
        self.assertEqual([e["type"] for e in meta["entities"]], ["ARC"])
