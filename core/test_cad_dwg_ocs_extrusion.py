import io
import json
import math
import shutil
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
# Made with ezdxf (R2018 DXF) and the runtime's own --dwg-from-dxf.  Each
# object has its own layer: ARC_REF / CIRCLE_REF / ELL_REF / INS_REF are plain;
# ARC_FLIP, ARC_FLIP_WRAP, CIRCLE_FLIP, PL_FLIP (one bulge), ELL_FLIP (a
# quarter), TEXT_FLIP, INS_FLIP, INS_FLIP_ROT (30 degrees), INS_FLIP_MIRROR,
# HATCH_FLIP and SOLID_FLIP have the normal (0, 0, -1), so their points are
# stored with x the other way round; INS_CHILD_FLIP is a plain block holding a
# flipped arc;
# ARC_TILT lies in a tilted plane (normal 0, 0.6, 0.8); INS_MIRROR is a
# plain block reference with x scale -1.
OCS = FIXTURES / "ocs_extrusion_ac1018.dwg"
MOVE = [1, 0, 0, 1, 100, 40]
TURN = [0, 1, -1, 0, 500, -200]      # 90 degrees about the origin, then a move
MIRROR = [-1, 0, 0, 1, 0, 0]         # about the Y axis


def _apply(matrix, p):
    a, b, c, d, e, f = matrix
    return (a * p[0] + c * p[1] + e, b * p[0] + d * p[1] + f)


def _segment_distance(p, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    length = dx * dx + dy * dy
    t = 0 if length == 0 else max(0, min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / length))
    return math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy)


def _distance(lines_a, lines_b):
    """The largest distance from a point of one set of polylines to the other set, both ways."""
    def one_way(a, b):
        segments = [(l[i], l[i + 1]) for l in b for i in range(len(l) - 1)] + [(l[0], l[0]) for l in b if len(l) == 1]
        return max(min(_segment_distance(p, s, t) for s, t in segments) for l in a for p in l)
    return max(one_way(lines_a, lines_b), one_way(lines_b, lines_a))


def _world_lines(entity):
    """The entity as AutoCAD draws it in plan: world points along each of its curves."""
    from ezdxf import path

    if entity.dxftype() == "INSERT":
        return [line for child in entity.virtual_entities() for line in _world_lines(child)]
    if entity.dxftype() in ("TEXT", "ATTRIB"):
        p = entity.ocs().to_wcs(entity.dxf.insert)
        return [[(p.x, p.y)]]
    paths = path.from_hatch(entity) if entity.dxftype() == "HATCH" else [path.make_path(entity)]
    return [[(v.x, v.y) for v in p.flattening(0.01, segments=64)] for p in paths]


def _wcs_arc(arc):
    """Centre, radius and counter-clockwise start/end angles of an ARC as seen from above."""
    ocs = arc.ocs()
    centre = ocs.to_wcs(arc.dxf.center)
    ends = [ocs.to_wcs(arc.dxf.center + (arc.dxf.radius * math.cos(math.radians(a)), arc.dxf.radius * math.sin(math.radians(a)), 0))
            for a in (arc.dxf.start_angle, arc.dxf.end_angle)]
    if arc.dxf.extrusion.z < 0:
        ends.reverse()
    angles = [math.atan2(p.y - centre.y, p.x - centre.x) for p in ends]
    return (centre.x, centre.y), arc.dxf.radius, angles[0], angles[1]


@skipUnless(EXECUTABLE is not None, "ACadSharp runtime is required")
class CadDwgOcsExtrusionTests(SimpleTestCase):
    """Objects whose normal points down (AutoCAD's 3D mirror) save where the editor shows them.

    An ARC, CIRCLE, LWPOLYLINE, TEXT, INSERT, HATCH or SOLID keeps its points
    in its own coordinate system (OCS); with the normal (0, 0, -1) x runs the
    other way.  The editor shows and edits world coordinates, and the writer
    put them straight into the OCS fields: an arc moved right was saved moved
    left (사무동1층.dwg, ARC 88BB), while the save said 완료.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cbl-ocs-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.before = self.read(OCS.read_bytes())

    def read(self, data):
        import ezdxf

        path = self.tmp / "read.dwg"
        path.write_bytes(data)
        return ezdxf.read(io.StringIO(core_views._cbl_free_dwg_to_dxf_text_v1(path)[0]))

    def on(self, layer, doc=None):
        found = [e for e in (doc or self.before).modelspace() if e.dxf.layer == layer]
        self.assertEqual(len(found), 1, layer)
        return found[0]

    def save(self, ops):
        fields = {"ops": json.dumps({"ops": ops}), "target_version": "AC1018", "filename": "ocs.dwg", "delivery": "binary",
                  "original_dwg": SimpleUploadedFile("ocs.dwg", OCS.read_bytes(), content_type="application/acad")}
        request = RequestFactory().post("/api/cblcad/free-dwg-save/?mode=free-dwg", fields)
        with patch.object(core_views, "_cbl_is_free_dwg_request", return_value=True), \
                patch.object(core_views, "_cbl_free_dwg_local_find_dwgread_v1", return_value=None):
            return core_views.cblcad_free_dwg_save_local_api(request)

    def saved(self, ops):
        response = self.save(ops)
        self.assertEqual(response.status_code, 200, response.content[:600])
        self.assertEqual(response["X-CBL-FREE-DWG-SAVE-VALIDATED"], "1")
        return self.read(response.content), json.loads(response["X-CBL-FREE-DWG-OUTPUT-HANDLES"])

    def assertDrawnAs(self, entity, expected, label):
        self.assertLess(_distance(_world_lines(entity), expected), 0.05, label)

    def moved(self, layer, matrix):
        return [[_apply(matrix, p) for p in line] for line in _world_lines(self.on(layer))]

    def test_edits_of_flipped_objects_save_where_the_editor_shows_them(self):
        arc = self.on("ARC_FLIP")
        (cx, cy), r, a1, a2 = _wcs_arc(arc)
        circle = self.on("CIRCLE_FLIP").ocs().to_wcs(self.on("CIRCLE_FLIP").dxf.center)
        poly = self.on("PL_FLIP")
        points = [poly.ocs().to_wcs((x, y, poly.dxf.elevation)) for x, y, *_ in poly.get_points("xyseb")]
        bulges = [-b for *_, b in poly.get_points("xyseb")]
        text = self.on("TEXT_FLIP")
        at = text.ocs().to_wcs(text.dxf.insert)
        insert = self.on("INS_FLIP_ROT")
        place = insert.ocs().to_wcs(insert.dxf.insert)
        dx, dy = MOVE[4], MOVE[5]
        ops = [
            {"type": "update", "entity": "ARC", "handle": arc.dxf.handle, "sourceHandle": arc.dxf.handle, "layer": "ARC_FLIP",
             "center": [cx + dx, cy + dy, 0], "radius": r, "startAngle": a1, "endAngle": a2},
            {"type": "update", "entity": "CIRCLE", "handle": self.on("CIRCLE_FLIP").dxf.handle, "layer": "CIRCLE_FLIP",
             "center": [circle.x + dx, circle.y + dy, 0], "radius": self.on("CIRCLE_FLIP").dxf.radius},
            {"type": "update", "entity": "LWPOLYLINE", "handle": poly.dxf.handle, "layer": "PL_FLIP",
             "points": [[p.x + dx, p.y + dy] for p in points], "bulges": bulges, "closed": False},
            {"type": "update", "entity": "TEXT", "handle": text.dxf.handle, "layer": "TEXT_FLIP",
             "insert": [at.x + dx, at.y + dy, 0], "rotation": math.pi - math.radians(text.dxf.rotation)},
            # Turned 90 degrees as the editor shows it: a flipped block at 30 degrees
            # is the block mirrored in x and turned -30 degrees.
            {"type": "update", "entity": "INSERT", "handle": insert.dxf.handle, "layer": "INS_FLIP_ROT", "blockName": "BK",
             "insert": [*_apply(TURN, (place.x, place.y)), 0], "rotation": math.radians(90 - insert.dxf.rotation),
             "scale": [-insert.dxf.xscale, insert.dxf.yscale, insert.dxf.zscale]},
        ]
        after, _ = self.saved(ops)
        for layer, matrix in (("ARC_FLIP", MOVE), ("CIRCLE_FLIP", MOVE), ("PL_FLIP", MOVE), ("TEXT_FLIP", MOVE), ("INS_FLIP_ROT", TURN)):
            with self.subTest(layer):
                self.assertDrawnAs(self.on(layer, after), self.moved(layer, matrix), layer)
        # Still flipped: only the edit changed.
        self.assertEqual(tuple(self.on("ARC_FLIP", after).dxf.extrusion), (0, 0, -1))

    def test_moves_turns_and_copies_of_flipped_objects(self):
        handle = lambda layer: self.on(layer).dxf.handle
        ops = [
            {"type": "move", "handle": handle("ARC_FLIP"), "entity": "ARC", "delta": [MOVE[4], MOVE[5], 0]},
            {"type": "transform", "handle": handle("ARC_FLIP_WRAP"), "entity": "ARC", "matrix": TURN},
            {"type": "transform", "handle": handle("ELL_FLIP"), "entity": "ELLIPSE", "matrix": MIRROR},
            {"type": "move", "handle": handle("HATCH_FLIP"), "entity": "HATCH", "delta": [MOVE[4], MOVE[5], 0]},
            {"type": "transform", "handle": handle("SOLID_FLIP"), "entity": "SOLID", "matrix": TURN},
            {"type": "transform", "handle": handle("PL_FLIP"), "entity": "LWPOLYLINE", "matrix": MIRROR},
        ]
        copies = [("CIRCLE_FLIP", MOVE), ("INS_FLIP", MIRROR), ("ARC_FLIP_WRAP", MIRROR)]
        ops = [{"type": "add_copy", "copyOf": handle(layer), "matrix": matrix, "clientShapeId": "c%d" % i}
               for i, (layer, matrix) in enumerate(copies)] + ops
        after, handles = self.saved(ops)
        for layer, matrix in (("ARC_FLIP", MOVE), ("ARC_FLIP_WRAP", TURN), ("ELL_FLIP", MIRROR), ("HATCH_FLIP", MOVE),
                              ("SOLID_FLIP", TURN), ("PL_FLIP", MIRROR)):
            with self.subTest(layer):
                self.assertDrawnAs(after.entitydb[handle(layer)], self.moved(layer, matrix), layer)
        for index, (layer, matrix) in enumerate(copies):
            with self.subTest("copy " + layer):
                copy = after.entitydb[handles[str(index)]]
                self.assertDrawnAs(copy, self.moved(layer, matrix), "copy " + layer)

    def test_tilted_objects_move_but_other_edits_are_refused(self):
        arc = self.on("ARC_TILT")
        after, handles = self.saved([{"type": "add_copy", "copyOf": arc.dxf.handle, "matrix": MOVE, "clientShapeId": "c0"}])
        self.assertDrawnAs(after.entitydb[handles["0"]], self.moved("ARC_TILT", MOVE), "moved copy")
        for op in ({"type": "update", "entity": "ARC", "handle": arc.dxf.handle, "layer": "ARC_TILT", "center": [0, 0, 0], "radius": 100,
                    "startAngle": 0, "endAngle": 1},
                   {"type": "transform", "handle": arc.dxf.handle, "entity": "ARC", "matrix": TURN}):
            with self.subTest(op["type"]):
                response = self.save([op])
                self.assertNotEqual(response.status_code, 200)
                self.assertIn("3D로 기울어진", json.loads(response.content)["error"])

    def test_plain_objects_are_unchanged_by_the_conversion(self):
        arc = self.on("ARC_REF")
        insert = self.on("INS_MIRROR")
        ops = [{"type": "update", "entity": "ARC", "handle": arc.dxf.handle, "layer": "ARC_REF",
                "center": [arc.dxf.center.x + MOVE[4], arc.dxf.center.y + MOVE[5], 0], "radius": arc.dxf.radius,
                "startAngle": math.radians(arc.dxf.start_angle), "endAngle": math.radians(arc.dxf.end_angle)},
               {"type": "update", "entity": "INSERT", "handle": insert.dxf.handle, "layer": "INS_MIRROR", "blockName": "BK",
                "insert": [insert.dxf.insert.x + MOVE[4], insert.dxf.insert.y + MOVE[5], 0], "rotation": 0,
                "scale": [insert.dxf.xscale, insert.dxf.yscale, insert.dxf.zscale]}]
        after, _ = self.saved(ops)
        for layer in ("ARC_REF", "INS_MIRROR"):
            with self.subTest(layer):
                self.assertDrawnAs(self.on(layer, after), self.moved(layer, MOVE), layer)
