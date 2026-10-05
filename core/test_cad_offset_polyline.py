import json
import subprocess
from unittest import skipUnless

from django.test import SimpleTestCase

from .test_cad_dwg_save_integrity import NODE, _editor_function, _html

HARNESS = """
function cloneJ(o){ return JSON.parse(JSON.stringify(o)); }
%(helpers)s
process.stdout.write(JSON.stringify((function(){ %(body)s })()));
"""

HELPERS = [
    "function cblBulgeArcV1(p1,p2,b){",
    "function cblPolylineSegmentsV1(s){",
    "function cblOffsetPolylineV1(s,dist){",
]


def _poly(points, closed=False):
    pts = [{"x": p[0], "y": p[1], **({"bulge": p[2]} if len(p) > 2 else {})} for p in points]
    return {"type": "polyline", "pts": pts, "handle": "2A", **({"closed": True} if closed else {})}


@skipUnless(NODE, "node is required to execute the CAD editor helpers")
class CadOffsetPolylineTests(SimpleTestCase):
    """OFFSET works on polylines, straight and bulged segments alike.

    It used to support only lines, circles and rectangles, so imported
    LWPOLYLINE outlines (walls, slabs) could not be offset at all.
    """

    def offset(self, shape, dist):
        html = _html()
        script = HARNESS % {"helpers": "\n".join(_editor_function(html, sig) for sig in HELPERS),
                            "body": "return cblOffsetPolylineV1(%s, %s);" % (json.dumps(shape), json.dumps(dist))}
        run = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=60)
        self.assertEqual(run.returncode, 0, run.stderr)
        return json.loads(run.stdout)

    def assertVertices(self, shape, want):
        got = [[round(p["x"], 6), round(p["y"], 6), round(p.get("bulge") or 0, 6)] for p in shape["pts"]]
        self.assertEqual(got, [[x, y, b] for x, y, b in want])

    def test_open_polyline_moves_to_the_left_and_meets_at_the_corner(self):
        result = self.offset(_poly([(0, 0), (100, 0), (100, 100)]), 10)
        self.assertVertices(result, [(0, 10, 0), (90, 10, 0), (90, 100, 0)])
        self.assertFalse(result.get("closed"))

    def test_closed_polyline_grows_outward_whichever_way_it_was_drawn(self):
        square = [(0, 0), (100, 0), (100, 100), (0, 100)]
        want = [(-10, -10, 0), (110, -10, 0), (110, 110, 0), (-10, 110, 0)]
        self.assertVertices(self.offset(_poly(square, True), 10), want)
        clockwise = self.offset(_poly(list(reversed(square)), True), 10)
        self.assertEqual(sorted((round(p["x"]), round(p["y"])) for p in clockwise["pts"]), sorted((x, y) for x, y, _ in want))
        inward = self.offset(_poly(square, True), -10)
        self.assertVertices(inward, [(10, 10, 0), (90, 10, 0), (90, 90, 0), (10, 90, 0)])

    def test_bulged_segments_keep_their_centre(self):
        # A stadium: two straight sides joined by half circles (radius 50).
        stadium = _poly([(0, 0), (100, 0, 1), (100, 100), (0, 100, 1)], True)
        result = self.offset(stadium, 10)
        self.assertVertices(result, [(0, -10, 0), (100, -10, 1), (100, 110, 0), (0, 110, 1)])

    def test_the_repeated_closing_vertex_of_imports_is_dropped(self):
        square = _poly([(0, 0), (100, 0), (100, 100), (0, 100), (0, 0)], True)
        self.assertVertices(self.offset(square, 10), [(-10, -10, 0), (110, -10, 0), (110, 110, 0), (-10, 110, 0)])

    def test_an_arc_shrinking_to_nothing_refuses(self):
        stadium = _poly([(0, 0), (100, 0, 1), (100, 100), (0, 100, 1)], True)
        self.assertIsNone(self.offset(stadium, -60))

    def test_the_offset_is_a_new_shape(self):
        result = self.offset(_poly([(0, 0), (100, 0)]), 5)
        self.assertNotIn("handle", result)

    def test_offset_dialog_handles_polylines_and_arcs(self):
        html = _html()
        dialog = html[html.index("function offsetDialog(){"):]
        dialog = dialog[:dialog.index("\n}\n")]
        self.assertIn("cblOffsetPolylineV1(s,dist)", dialog)
        self.assertIn("s.type==='arc'", dialog)
