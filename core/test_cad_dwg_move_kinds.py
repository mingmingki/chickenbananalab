import io
import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from unittest import skipUnless

from django.conf import settings
from django.test import SimpleTestCase

from . import views as core_views
from .test_cad_dwg_text_validation import EXECUTABLE, _run_writer
from .test_oda_review import find_oda

FIXTURES = Path(settings.BASE_DIR) / "core" / "test_fixtures" / "cad"
# One of each kind the writer could not move: two SPLINEs (control and fit
# points), ELLIPSE, SOLID, 3DFACE, POINT, LEADER, a patterned HATCH (polyline
# and edge paths), two DIMENSIONs and an MTEXT MULTILEADER, next to a LINE.
# ezdxf R2018 DXF converted to DWG AC1032 by ODA.
KINDS = FIXTURES / "move_kinds_ac1032.dwg"
DELTA = (100.0, 50.0)


def _shift(point, delta=DELTA):
    return (round(point[0] + delta[0], 6), round(point[1] + delta[1], 6))


def _r(point):
    return (round(point[0], 6), round(point[1], 6))


@skipUnless(EXECUTABLE is not None, "ACadSharp runtime is required")
class CadDwgMoveKindsTests(SimpleTestCase):
    """MOVE works for every kind the editor draws from a DWG.

    The writer moved only LINE, CIRCLE/ARC, LWPOLYLINE, TEXT, MTEXT and INSERT;
    moving anything else failed the whole save ("Move is not supported for
    Spline").  A move is a pure translation: directions, sizes and the
    dimension's own block move with the points.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cbl-move-kinds-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.meta = core_views._cbl_free_dwg_acadsharp_metadata_v1(KINDS)

    def handles(self, kind):
        return [e["handle"] for e in self.meta["entities"] if e.get("space") == "modelspace" and e["type"] == kind]

    def read(self, dwg):
        import ezdxf
        out = self.tmp / (dwg.stem + ".dxf")
        run = subprocess.run([str(EXECUTABLE), "--dxf", str(dwg), str(out)], capture_output=True, timeout=300)
        self.assertEqual(run.returncode, 0, run.stderr[-800:])
        return ezdxf.read(io.StringIO(out.read_bytes().decode("cp949", errors="replace")))

    def move(self, handles):
        ops = [{"type": "move", "handle": h, "delta": [DELTA[0], DELTA[1], 0]} for h in handles]
        ops_path = self.tmp / "ops.json"
        ops_path.write_text(json.dumps({"ops": ops}), encoding="utf-8")
        output = self.tmp / "moved.dwg"
        report = _run_writer([KINDS, output, "AC1018", ops_path])
        core_views._cbl_free_dwg_save_local_validate_v1(KINDS, output, None, ops, report)
        return self.read(KINDS).entitydb, self.read(output).entitydb

    def test_spline_points_move_and_tangents_stay(self):
        handles = self.handles("SPLINE")
        before, after = self.move(handles)
        for h in handles:
            a, b = before[h], after[h]
            self.assertEqual([_r(p) for p in b.control_points], [_shift(p) for p in a.control_points])
            self.assertEqual([_r(p) for p in b.fit_points], [_shift(p) for p in a.fit_points])
            self.assertEqual(b.dxf.get("start_tangent"), a.dxf.get("start_tangent"))

    def test_ellipse_centre_moves_and_shape_stays(self):
        [h] = self.handles("ELLIPSE")
        before, after = self.move([h])
        a, b = before[h], after[h]
        self.assertEqual(_r(b.dxf.center), _shift(a.dxf.center))
        self.assertEqual(_r(b.dxf.major_axis), _r(a.dxf.major_axis))
        self.assertAlmostEqual(b.dxf.ratio, a.dxf.ratio)

    def test_solid_face_point_and_leader_move(self):
        handles = self.handles("SOLID") + self.handles("FACE3D") + self.handles("POINT") + self.handles("LEADER")
        before, after = self.move(handles)
        for h in handles:
            a, b = before[h], after[h]
            if a.dxftype() == "POINT":
                self.assertEqual(_r(b.dxf.location), _shift(a.dxf.location))
            elif a.dxftype() == "LEADER":
                self.assertEqual([_r(v) for v in b.vertices], [_shift(v) for v in a.vertices])
            else:
                self.assertEqual([_r(v) for v in b.wcs_vertices()], [_shift(v) for v in a.wcs_vertices()])

    def test_hatch_boundary_seeds_and_pattern_move(self):
        [h] = self.handles("HATCH")
        before, after = self.move([h])
        a, b = before[h], after[h]

        def points(hatch, delta=(0.0, 0.0)):
            out = []
            for path in hatch.paths:
                if hasattr(path, "vertices"):
                    out += [(_shift(v[:2], delta), round(v[2] if len(v) > 2 else 0, 6)) for v in path.vertices]
                else:
                    for edge in path.edges:
                        for attr in ("start", "end", "center"):
                            if hasattr(edge, attr):
                                out.append((attr, _shift(getattr(edge, attr), delta)))
            return out

        self.assertTrue(points(a))
        self.assertEqual(points(b), points(a, DELTA))
        self.assertEqual([_r(s) for s in b.seeds], [_shift(s) for s in a.seeds])
        self.assertEqual([_r(line.base_point) for line in b.pattern.lines], [_shift(line.base_point) for line in a.pattern.lines])
        self.assertAlmostEqual(b.dxf.pattern_angle, a.dxf.pattern_angle)

    def test_dimension_points_and_block_move(self):
        handles = self.handles("DIMENSIONLINEAR")
        before, after = self.move(handles)
        for h in handles:
            a, b = before[h], after[h]
            for attr in ("defpoint", "defpoint2", "defpoint3", "text_midpoint"):
                self.assertEqual(_r(b.dxf.get(attr)), _shift(a.dxf.get(attr)), attr)

            def geometry(dim):
                pts = []
                for e in dim.get_geometry_block():
                    if e.dxftype() == "LINE":
                        pts += [_r(e.dxf.start), _r(e.dxf.end)]
                    elif e.dxftype() in ("MTEXT", "TEXT", "INSERT", "POINT"):
                        pts.append(_r(e.dxf.insert if e.dxftype() != "POINT" else e.dxf.location))
                return pts

            self.assertTrue(geometry(a))
            self.assertEqual(geometry(b), [_shift(p) for p in geometry(a)])

    def test_multileader_moves(self):
        [h] = self.handles("MULTILEADER")
        before, after = self.move([h])
        a, b = before[h].context, after[h].context
        self.assertEqual(_r(b.base_point), _shift(a.base_point))
        self.assertEqual(_r(b.mtext.insert), _shift(a.mtext.insert))
        for la, lb in zip(a.leaders, b.leaders):
            self.assertEqual(_r(lb.last_leader_point), _shift(la.last_leader_point))
            for va, vb in zip(la.lines, lb.lines):
                self.assertEqual([_r(v) for v in vb.vertices], [_shift(v) for v in va.vertices])

    def test_each_kind_can_be_deleted(self):
        # The validator counted SPLINE, ELLIPSE, 3DFACE, LEADER and MULTILEADER
        # as "unsupported" kinds whose number may never go down, so deleting
        # one was refused as a changed DWG structure.
        for entity in self.meta["entities"]:
            if entity.get("space") != "modelspace" or entity["type"] == "LINE":
                continue
            with self.subTest(entity["type"]):
                ops = [{"type": "delete", "handle": entity["handle"], "entity": entity["type"]}]
                ops_path = self.tmp / "ops.json"
                ops_path.write_text(json.dumps({"ops": ops}), encoding="utf-8")
                output = self.tmp / "deleted.dwg"
                report = _run_writer([KINDS, output, "AC1018", ops_path])
                core_views._cbl_free_dwg_save_local_validate_v1(KINDS, output, None, ops, report)
                self.assertNotIn(entity["handle"], [e["handle"] for e in core_views._cbl_free_dwg_acadsharp_metadata_v1(output)["entities"]])

    @skipUnless(find_oda(), "ODA File Converter is only used for local review")
    def test_another_reader_opens_the_moved_drawing(self):
        import ezdxf

        handles = [e["handle"] for e in self.meta["entities"] if e.get("space") == "modelspace" and e["type"] != "LINE"]
        self.move(handles)
        source, target = self.tmp / "oda-in", self.tmp / "oda-out"
        source.mkdir()
        target.mkdir()
        shutil.copy(self.tmp / "moved.dwg", source / "moved.dwg")
        subprocess.run([find_oda(), str(source), str(target), "ACAD2018", "DXF", "0", "1"], capture_output=True, timeout=300)
        self.assertFalse(list(target.glob("*.err")), [p.read_text(errors="replace")[:300] for p in target.glob("*.err")])
        moved = ezdxf.readfile(target / "moved.dxf").modelspace()
        self.assertEqual(len(moved.query("MULTILEADER")), 1)
        self.assertEqual(len(moved.query("HATCH")), 1)


class CadWriterRefusalMessageTests(SimpleTestCase):
    """An edit the writer cannot apply is explained in Korean, without the .NET stack."""

    def message(self, error):
        detail = json.dumps({"status": "failed", "error": error + "\n   at CblAcadSharpPoc.Program.MoveEntity(Entity entity, XYZ delta)"})
        return core_views._cbl_free_dwg_writer_error_message_v1(detail)

    def test_unsupported_move_and_update(self):
        self.assertEqual(self.message("System.NotSupportedException: Update is not supported for Spline"),
                         "스플라인 수정은 아직 DWG로 저장할 수 없어 저장을 멈췄습니다. 원본 파일은 바뀌지 않았습니다. 그 편집을 되돌린 뒤 다시 저장해 주세요.")
        self.assertEqual(self.message("System.NotSupportedException: Move is not supported for Wipeout"),
                         "가림막(WIPEOUT) 이동은 아직 DWG로 저장할 수 없어 저장을 멈췄습니다. 원본 파일은 바뀌지 않았습니다. 그 편집을 되돌린 뒤 다시 저장해 주세요.")
        other = self.message("System.NotSupportedException: Move is not supported for a dimension whose block other dimensions share")
        self.assertIn("아직 DWG로 저장할 수 없어", other)
        self.assertNotIn("CblAcadSharpPoc", other)
        self.assertNotIn("System.", other)
