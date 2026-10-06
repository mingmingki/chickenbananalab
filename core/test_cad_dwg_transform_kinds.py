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
# ezdxf + ODA: LINE 8A, ordinate DIMENSION 8B ("12000", the style multiplies
# lengths by 100) and angular DIMENSION 96.
ORDINATE = FIXTURES / "dim_ordinate_ac1032.dwg"
# ezdxf + ODA: HATCH 2F (ANSI31, counter-clockwise arc edge), 30 (solid,
# clockwise arc edge) and 31 (solid, elliptic edge).
HATCH_EDGES = FIXTURES / "hatch_edges_ac1032.dwg"
ROTATE = [0.0, 1.0, -1.0, 0.0, 1000.0, 0.0]   # 90 degrees about the origin, then +1000 in x
SCALE = [2.0, 0.0, 0.0, 2.0, 0.0, 0.0]
MIRROR = [-1.0, 0.0, 0.0, 1.0, 0.0, 0.0]      # about the Y axis
FLIP = [1.0, 0.0, 0.0, -1.0, 0.0, 0.0]        # about the X axis


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

    def save(self, ops, source=KINDS):
        ops_path = self.tmp / "ops.json"
        ops_path.write_text(json.dumps({"ops": ops}), encoding="utf-8")
        output = self.tmp / "out.dwg"
        report = _run_writer([source, output, "AC1018", ops_path])
        core_views._cbl_free_dwg_save_local_validate_v1(source, output, None, ops, report)
        return self.read(source).entitydb, self.read(output).entitydb, report

    @staticmethod
    def dim_text(dimension):
        return [e.plain_text() if e.dxftype() == "MTEXT" else e.dxf.text for e in dimension.get_geometry_block() if e.dxftype() in ("MTEXT", "TEXT")]

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
            self.assertEqual(len(lines(after)), len(moved))
            for (a, b), (c, d) in zip(lines(after), moved):
                self.assertLess(max(math.dist(a, c), math.dist(b, d)), 1e-3, (a, b, c, d))
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
        cases = [(self.handles("DIMENSIONLINEAR")[0], MIRROR, "치수 대칭은"),
                 # Upside down the text would sit on the other side of its landing
                 # than in the editor; AutoCAD rebuilds it from the landing.
                 (self.handles("MULTILEADER")[0], FLIP, "다중 지시선 대칭(위아래·기울어진 축)은")]
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

    def test_scaled_dimensions_show_their_new_length(self):
        # As AutoCAD regenerates a scaled dimension: the number is the new
        # length in the style's format (two decimals, trailing zeros hidden:
        # 11661.90 showed as 11661.9).
        dims = self.handles("DIMENSIONLINEAR")
        # The editor sends a copy before the edits of its source.
        ops = [{"type": "add_copy", "copyOf": dims[0], "matrix": [2, 0, 0, 2, 0, 500], "clientShapeId": "c1"}]
        ops += [{"type": "transform", "handle": h, "matrix": SCALE} for h in dims]
        before, after, report = self.save(ops)
        self.assertEqual([self.dim_text(before[h]) for h in dims], [["20000"], ["11661.9"]])
        self.assertEqual([self.dim_text(after[h]) for h in dims], [["40000"], ["23323.81"]])
        for h in dims:
            self.check(SCALE, before[h], after[h])
        copy = after[core_views._cbl_free_dwg_output_handles_v1(report, ops)["0"]]
        self.assertEqual(self.dim_text(copy), ["40000"])

    def test_ordinate_and_angular_dimensions(self):
        before, after, _ = self.save([{"type": "transform", "handle": "8B", "matrix": SCALE},
                                      {"type": "transform", "handle": "96", "matrix": SCALE}], source=ORDINATE)
        self.assertEqual(self.dim_text(before["8B"]), ["12000"])
        self.assertEqual(self.dim_text(after["8B"]), ["24000"])
        # An angle does not change with the size.  (Its degree sign is lost in
        # any AC1018 save of this ANSI_1252 drawing; a separate issue.)
        self.assertEqual([t[:3] for t in self.dim_text(after["96"])], ["315"])
        self.assertEqual([t[:3] for t in self.dim_text(before["96"])], ["315"])
        self.assertEqual(_r(after["96"].dxf.defpoint), _apply(SCALE, before["96"].dxf.defpoint))
        # An ordinate dimension measures along the drawing's axes: turned, it
        # would show a wrong value, so the save refuses.
        ops_path = self.tmp / "ops.json"
        ops_path.write_text(json.dumps({"ops": [{"type": "transform", "handle": "8B", "matrix": ROTATE}]}))
        run = subprocess.run([str(EXECUTABLE), str(ORDINATE), str(self.tmp / "x.dwg"), "AC1018", str(ops_path)], capture_output=True, timeout=300)
        self.assertNotEqual(run.returncode, 0)
        self.assertIn("치수 회전·대칭(좌표 치수)은", core_views._cbl_free_dwg_writer_error_message_v1(run.stderr.decode("utf-8", "replace")))

    def test_mirrored_pattern_hatch_and_multileader(self):
        hatch, mleader = self.handles("HATCH")[0], self.handles("MULTILEADER")[0]
        ops = [{"type": "add_copy", "copyOf": h, "matrix": [-1, 0, 0, 1, 3000, 0], "clientShapeId": "c%d" % i} for i, h in enumerate((hatch, mleader))]
        ops += [{"type": "transform", "handle": h, "matrix": MIRROR} for h in (hatch, mleader)]
        before, after, report = self.save(ops)
        copies = core_views._cbl_free_dwg_output_handles_v1(report, ops)
        for matrix, h, copy in ((MIRROR, hatch, after[hatch]), ([-1, 0, 0, 1, 3000, 0], hatch, after[copies["0"]])):
            self.check(matrix, before[h], copy)
            # The pattern is mirrored too: its lines turn the other way.
            for a, b in zip(before[h].pattern.lines, copy.pattern.lines):
                self.assertAlmostEqual((180 - a.angle) % 360, b.angle % 360, places=6)
                self.assertEqual(_r(b.base_point), _apply(matrix, a.base_point))
                self.assertEqual(_r(b.offset), _r((-a.offset[0], a.offset[1])))
        for matrix, copy in ((MIRROR, after[mleader]), ([-1, 0, 0, 1, 3000, 0], after[copies["1"]])):
            a, b = before[mleader].context, copy.context
            self.assertEqual(_r(b.mtext.insert), _apply(matrix, a.mtext.insert))
            for la, lb in zip(a.leaders, b.leaders):
                self.assertEqual(_r(lb.last_leader_point), _apply(matrix, la.last_leader_point))
                self.assertEqual(_r(lb.dogleg_vector), _r((-la.dogleg_vector[0], la.dogleg_vector[1])))
            # The text reads as before, on the other side of the landing.
            self.assertEqual(_r(b.mtext.text_direction), _r(a.mtext.text_direction))
            self.assertEqual((before[mleader].dxf.text_attachment_point, copy.dxf.text_attachment_point), (1, 3))

    def test_arc_and_elliptic_hatch_edges(self):
        # A clockwise edge stores its angles negated: rotating it turned it
        # the wrong way, and a mirrored elliptic edge came out on the other side.
        handles = ["2F", "30", "31"]
        for matrix in (ROTATE, MIRROR, SCALE):
            before, after, _ = self.save([{"type": "transform", "handle": h, "matrix": matrix} for h in handles], source=HATCH_EDGES)
            for h in handles:
                with self.subTest(matrix=matrix, hatch=h):
                    self.check(matrix, before[h], after[h])
        # Mirrored, then rotated in a later save: the mirrored edges run clockwise.
        mirrored = self.tmp / "mirrored.dwg"
        self.save([{"type": "transform", "handle": h, "matrix": MIRROR} for h in handles], source=HATCH_EDGES)
        shutil.copy(self.tmp / "out.dwg", mirrored)
        before = self.read(HATCH_EDGES).entitydb
        _, after, _ = self.save([{"type": "transform", "handle": h, "matrix": ROTATE} for h in handles], source=mirrored)
        both = [0.0, -1.0, -1.0, 0.0, 1000.0, 0.0]   # ROTATE after MIRROR
        for h in handles:
            with self.subTest(hatch=h):
                self.check(both, before[h], after[h])
