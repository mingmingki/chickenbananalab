import io
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
from .test_cad_dwg_text_validation import EXECUTABLE, _run_writer
from .test_oda_review import find_oda

FIXTURES = Path(settings.BASE_DIR) / "core" / "test_fixtures" / "cad"
KINDS = FIXTURES / "move_kinds_ac1032.dwg"
ROTATE = [0.0, 1.0, -1.0, 0.0, 1000.0, 0.0]   # 90 degrees about the origin, then +1000 in x
SCALE = [2.0, 0.0, 0.0, 2.0, 0.0, 0.0]
MIRROR = [-1.0, 0.0, 0.0, 1.0, 0.0, 0.0]      # about the Y axis


def _apply(matrix, point):
    a, b, c, d, e, f = matrix
    return (round(a * point[0] + c * point[1] + e, 4), round(b * point[0] + d * point[1] + f, 4))


def _r(point):
    return (round(point[0], 4), round(point[1], 4))


def _samples(entity, segments=64):
    """Points along an entity's outline, for comparing curves however they are parameterized."""
    from ezdxf import path

    if entity.dxftype() == "HATCH":
        paths = path.from_hatch(entity)
    else:
        paths = [path.make_path(entity)]
    return sorted({_r(v) for p in paths for v in p.flattening(0.01, segments=segments)})


@skipUnless(EXECUTABLE is not None, "ACadSharp runtime is required")
class CadDwgTransformKindsTests(SimpleTestCase):
    """Rotate, scale, mirror and copy keep each kind's geometry and type.

    The writer could only move SPLINE, ELLIPSE, SOLID, 3DFACE, POINT, LEADER,
    HATCH, DIMENSION and MULTILEADER; rotating, scaling or mirroring them was
    refused, and a copy was saved as a polyline (a hatch lost its fill).
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cbl-transform-kinds-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.meta = core_views._cbl_free_dwg_acadsharp_metadata_v1(KINDS)

    def handles(self, *kinds):
        return [e["handle"] for e in self.meta["entities"] if e.get("space") == "modelspace" and e["type"] in kinds]

    def read(self, dwg):
        import ezdxf
        out = self.tmp / (dwg.stem + ".dxf")
        run = subprocess.run([str(EXECUTABLE), "--dxf", str(dwg), str(out)], capture_output=True, timeout=300)
        self.assertEqual(run.returncode, 0, run.stderr[-800:])
        return ezdxf.read(io.StringIO(out.read_bytes().decode("cp949", errors="replace")))

    def save(self, ops):
        ops_path = self.tmp / "ops.json"
        ops_path.write_text(json.dumps({"ops": ops}), encoding="utf-8")
        output = self.tmp / "out.dwg"
        report = _run_writer([KINDS, output, "AC1018", ops_path])
        core_views._cbl_free_dwg_save_local_validate_v1(KINDS, output, None, ops, report)
        return self.read(KINDS).entitydb, self.read(output).entitydb, report

    def check(self, matrix, before, after):
        kind = before.dxftype()
        if kind == "SPLINE":
            self.assertEqual([_r(p) for p in after.control_points], [_apply(matrix, p) for p in before.control_points])
            self.assertEqual([_r(p) for p in after.fit_points], [_apply(matrix, p) for p in before.fit_points])
        elif kind in ("ELLIPSE", "HATCH"):
            expected = sorted({_apply(matrix, p) for p in _samples(before)})
            got = _samples(after)
            self.assertEqual(len(got), len(expected), kind)
            for p, q in zip(got, expected):
                self.assertLess(math.dist(p, q), 0.02, (kind, p, q))
        elif kind in ("SOLID", "3DFACE"):
            self.assertEqual([_r(v) for v in after.wcs_vertices()], [_apply(matrix, v) for v in before.wcs_vertices()])
        elif kind == "POINT":
            self.assertEqual(_r(after.dxf.location), _apply(matrix, before.dxf.location))
        elif kind == "LEADER":
            self.assertEqual([_r(v) for v in after.vertices], [_apply(matrix, v) for v in before.vertices])
        elif kind == "DIMENSION":
            for attr in ("defpoint", "defpoint2", "defpoint3", "text_midpoint"):
                self.assertEqual(_r(after.dxf.get(attr)), _apply(matrix, before.dxf.get(attr)), attr)
            lines = lambda dim: sorted((_r(e.dxf.start), _r(e.dxf.end)) for e in dim.get_geometry_block() if e.dxftype() == "LINE")
            moved = sorted((_apply(matrix, a), _apply(matrix, b)) for a, b in lines(before))
            self.assertEqual(lines(after), moved)
        elif kind == "MULTILEADER":
            a, b = before.context, after.context
            self.assertEqual(_r(b.base_point), _apply(matrix, a.base_point))
            self.assertEqual(_r(b.mtext.insert), _apply(matrix, a.mtext.insert))
            for la, lb in zip(a.leaders, b.leaders):
                for va, vb in zip(la.lines, lb.lines):
                    self.assertEqual([_r(v) for v in vb.vertices], [_apply(matrix, v) for v in va.vertices])
        else:
            self.fail(kind)

    def test_rotate_every_kind(self):
        handles = self.handles("SPLINE", "ELLIPSE", "SOLID", "FACE3D", "POINT", "LEADER", "HATCH", "DIMENSIONLINEAR", "MULTILEADER")
        before, after, _ = self.save([{"type": "transform", "handle": h, "matrix": ROTATE} for h in handles])
        for h in handles:
            with self.subTest(before[h].dxftype()):
                self.check(ROTATE, before[h], after[h])

    def test_scale_and_mirror(self):
        scaled = self.handles("SPLINE", "ELLIPSE", "SOLID", "FACE3D", "POINT", "LEADER", "HATCH", "MULTILEADER")
        before, after, _ = self.save([{"type": "transform", "handle": h, "matrix": SCALE} for h in scaled])
        for h in scaled:
            with self.subTest("scale " + before[h].dxftype()):
                self.check(SCALE, before[h], after[h])
        mirrored = self.handles("SPLINE", "ELLIPSE", "SOLID", "FACE3D", "POINT", "LEADER")
        before, after, _ = self.save([{"type": "transform", "handle": h, "matrix": MIRROR} for h in mirrored])
        for h in mirrored:
            with self.subTest("mirror " + before[h].dxftype()):
                self.check(MIRROR, before[h], after[h])

    def test_changes_that_would_show_wrong_values_are_refused(self):
        # The message names the edit that cannot be saved (a mirror is not a "rotation").
        cases = [(self.handles("DIMENSIONLINEAR")[0], SCALE, "치수 크기 변경·대칭은"),
                 (self.handles("HATCH")[0], MIRROR, "해치 대칭(무늬 해치)은"),
                 (self.handles("MULTILEADER")[0], MIRROR, "다중 지시선 대칭(또는 블록 내용)은")]
        for handle, matrix, message in cases:
            with self.subTest(message):
                ops_path = self.tmp / "ops.json"
                ops_path.write_text(json.dumps({"ops": [{"type": "transform", "handle": handle, "matrix": matrix}]}))
                run = subprocess.run([str(EXECUTABLE), str(KINDS), str(self.tmp / "x.dwg"), "AC1018", str(ops_path)], capture_output=True, timeout=300)
                self.assertNotEqual(run.returncode, 0)
                self.assertIn(message, core_views._cbl_free_dwg_writer_error_message_v1(run.stderr.decode("utf-8", "replace")))

    def test_copies_keep_their_kind(self):
        sources = self.handles("SPLINE", "ELLIPSE", "SOLID", "FACE3D", "POINT", "LEADER", "HATCH", "DIMENSIONLINEAR", "MULTILEADER")
        ops = [{"type": "add_copy", "copyOf": h, "matrix": ROTATE, "clientShapeId": "copy-%d" % i} for i, h in enumerate(sources)]
        before, after, report = self.save(ops)
        handles = core_views._cbl_free_dwg_output_handles_v1(report, ops)
        self.assertEqual(len(handles), len(sources))
        for index, source in enumerate(sources):
            copy = after[handles[str(index)]]
            with self.subTest(before[source].dxftype()):
                self.assertEqual(copy.dxftype(), before[source].dxftype())
                self.check(ROTATE, before[source], copy)
                self.check([1, 0, 0, 1, 0, 0], before[source], after[source])

    @skipUnless(find_oda(), "ODA File Converter is only used for local review")
    def test_another_reader_opens_rotated_and_copied_kinds(self):
        import ezdxf

        sources = self.handles("SPLINE", "ELLIPSE", "SOLID", "FACE3D", "POINT", "LEADER", "HATCH", "DIMENSIONLINEAR", "MULTILEADER")
        ops = [{"type": "add_copy", "copyOf": h, "matrix": ROTATE, "clientShapeId": "c%d" % i} for i, h in enumerate(sources)]
        ops += [{"type": "transform", "handle": h, "matrix": SCALE} for h in self.handles("HATCH", "MULTILEADER", "SPLINE")]
        self.save(ops)
        source, target = self.tmp / "oda-in", self.tmp / "oda-out"
        source.mkdir()
        target.mkdir()
        shutil.copy(self.tmp / "out.dwg", source / "out.dwg")
        subprocess.run([find_oda(), str(source), str(target), "ACAD2018", "DXF", "0", "1"], capture_output=True, timeout=300)
        self.assertFalse(list(target.glob("*.err")), [p.read_text(errors="replace")[:300] for p in target.glob("*.err")])
        msp = ezdxf.readfile(target / "out.dxf").modelspace()
        self.assertEqual(len(msp.query("DIMENSION")), 4)
        self.assertEqual(len(msp.query("MULTILEADER")), 2)
        self.assertEqual(len(msp.query("HATCH")), 2)
