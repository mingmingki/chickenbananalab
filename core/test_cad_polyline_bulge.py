import json
import math
import re
import subprocess
from unittest import skipUnless

from django.test import SimpleTestCase

from .test_cad_dwg_save_integrity import NODE, _editor_function, _html

HARNESS = """
var vScale = 1;
function cblIsDisplayableShapeV1(){ return true; }
function cblSelectionTypeV1(s){ return s.type; }
%(helpers)s
process.stdout.write(JSON.stringify((function(){ %(body)s })()));
"""

HELPERS = [
    "function dst(a,b){",
    "function dSeg(p,a,b){",
    "function cblArcBoundsV1(s){",
    "function cblArcDistanceV1(s,p){",
    "function cblBulgeArcV1(p1,p2,b){",
    "function cblPolylineSegmentsV1(s){",
    "function cblPolylinePathV1(c,s){",
    "function cblPolylineBoundsV1(s){",
    "function cblPolylineDistanceV1(s,p){",
    "function cblPolylineTraceV1(s){",
    "function cblSelectionPolylinePointsV1(s){",
    "function getBB(s){",
    "function hitTest(s,p,depth){",
    "function cblSelectionPrimitiveDistanceV1(s,p){",
]

RECORDER = """
var calls = [];
var c = {moveTo:function(x,y){calls.push(['move',x,y]);}, lineTo:function(x,y){calls.push(['line',x,y]);},
         arc:function(x,y,r,a,b,ccw){calls.push(['arc',x,y,r,a,b,!!ccw]);}, closePath:function(){calls.push(['close']);}};
"""


def _poly(points, closed=False):
    pts = [{"x": p[0], "y": p[1], **({"bulge": p[2]} if len(p) > 2 else {})} for p in points]
    return {"type": "polyline", "pts": pts, **({"closed": True} if closed else {})}


@skipUnless(NODE, "node is required to execute the CAD editor helpers")
class CadPolylineBulgeTests(SimpleTestCase):
    """Polyline arc segments (DXF bulge) are drawn, bounded and picked as arcs."""

    def run_js(self, body):
        html = _html()
        script = HARNESS % {"helpers": "\n".join(_editor_function(html, sig) for sig in HELPERS), "body": body}
        run = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=60)
        self.assertEqual(run.returncode, 0, run.stderr)
        return json.loads(run.stdout)

    def assertNear(self, got, want, places=6):
        self.assertEqual(len(got), len(want), got)
        for g, w in zip(got, want):
            if isinstance(w, (int, float)) and not isinstance(w, bool):
                self.assertAlmostEqual(g, w, places=places, msg=repr(got))
            else:
                self.assertEqual(g, w, got)

    # --- geometry of one bulge -------------------------------------------------------------
    def test_positive_bulge_one_is_the_half_circle_right_of_travel(self):
        arc = self.run_js("return cblBulgeArcV1({x:0,y:0},{x:10,y:0},1);")
        self.assertNear([arc["cx"], arc["cy"], arc["r"]], [5, 0, 5])
        self.assertTrue(arc["ccw"])
        # Stored like an ARC: counter-clockwise from 180° to 0° passes 270° (below the chord).
        self.assertNear([math.degrees(arc["a1"]) % 360, math.degrees(arc["a2"]) % 360], [180, 0])

    def test_negative_bulge_turns_clockwise(self):
        arc = self.run_js("return cblBulgeArcV1({x:0,y:0},{x:10,y:0},-1);")
        self.assertFalse(arc["ccw"])
        self.assertNear([math.degrees(arc["a1"]) % 360, math.degrees(arc["a2"]) % 360], [0, 180])

    def test_small_bulge_has_the_expected_sagitta(self):
        box = self.run_js("return cblArcBoundsV1(cblBulgeArcV1({x:0,y:0},{x:10,y:0},0.1));")
        self.assertAlmostEqual(box["y"], -0.5)  # sagitta = bulge * chord / 2
        self.assertAlmostEqual(box["y"] + box["h"], 0)

    def test_zero_bulge_or_zero_length_is_straight(self):
        self.assertEqual(self.run_js("return [cblBulgeArcV1({x:0,y:0},{x:10,y:0},0), cblBulgeArcV1({x:1,y:1},{x:1,y:1},1)];"),
                         [None, None])

    # --- drawing ---------------------------------------------------------------------------
    def path(self, poly):
        return self.run_js(RECORDER + "cblPolylinePathV1(c, %s); return calls;" % json.dumps(poly))

    def test_path_draws_the_bulged_segment_as_an_arc(self):
        calls = self.path(_poly([(0, 0, 1), (10, 0), (10, 10)]))
        self.assertEqual(calls[0], ["move", 0, 0])
        self.assertEqual(calls[1][0], "arc")
        self.assertNear(calls[1][1:4] + [calls[1][6]], [5, 0, 5, False])
        self.assertNear([abs(calls[1][4]), calls[1][5]], [math.pi, 0])
        self.assertEqual(calls[2], ["line", 10, 10])
        self.assertEqual(len(calls), 3)

    def test_path_draws_negative_bulge_anticlockwise_on_canvas(self):
        calls = self.path(_poly([(0, 0, -1), (10, 0)]))
        self.assertEqual(calls[1][0], "arc")
        self.assertTrue(calls[1][6])

    def test_closing_segment_uses_the_last_vertex_bulge(self):
        calls = self.path(_poly([(0, 0), (10, 0, 1)], closed=True))
        self.assertEqual([c[0] for c in calls], ["move", "line", "arc", "close"])
        self.assertNear(calls[2][1:4], [5, 0, 5])

    def test_imported_closed_polyline_with_repeated_first_vertex_is_not_closed_twice(self):
        calls = self.path(_poly([(0, 0, 1), (10, 0, 1), (0, 0)], closed=True))
        self.assertEqual([c[0] for c in calls], ["move", "arc", "arc", "close"])

    def test_plain_polyline_path_is_unchanged(self):
        calls = self.path(_poly([(0, 0), (10, 0), (10, 10)]))
        self.assertEqual(calls, [["move", 0, 0], ["line", 10, 0], ["line", 10, 10]])

    # --- selection box ---------------------------------------------------------------------
    def bb(self, poly):
        return self.run_js("return getBB(%s);" % json.dumps(poly))

    def assertBox(self, box, x, y, w, h):
        self.assertNear([box["x"], box["y"], box["w"], box["h"]], [x, y, w, h])

    def test_box_includes_the_arc(self):
        self.assertBox(self.bb(_poly([(0, 0, 1), (10, 0)])), 0, -5, 10, 5)

    def test_box_of_a_polyline_circle(self):
        self.assertBox(self.bb(_poly([(0, 0, 1), (10, 0, 1), (0, 0)], closed=True)), 0, -5, 10, 10)

    def test_box_of_a_plain_polyline_is_unchanged(self):
        self.assertBox(self.bb(_poly([(0, 0), (10, 0), (10, 20)])), 0, 0, 10, 20)
        self.assertBox(self.bb(_poly([(0, 0), (10, 0)])), 0, 0, 10, 1)

    # --- clicking --------------------------------------------------------------------------
    def test_click_on_the_curve_selects_and_on_the_chord_does_not(self):
        poly = _poly([(0, 0, 1), (100, 0)])
        result = self.run_js("var s=%s; return [[50,-50],[50,-52],[50,0],[50,50]].map(function(p){return hitTest(s,{x:p[0],y:p[1]});});"
                             % json.dumps(poly))
        self.assertEqual(result, [True, True, False, False])

    def test_click_on_the_closing_arc(self):
        poly = _poly([(0, 0), (100, 0, 1)], closed=True)
        self.assertEqual(self.run_js("return hitTest(%s,{x:50,y:50});" % json.dumps(poly)), True)

    def test_nearest_pick_distance_follows_the_curve(self):
        poly = _poly([(0, 0, 1), (100, 0)])
        d = self.run_js("return [cblSelectionPrimitiveDistanceV1(%s,{x:50,y:-53}), cblPolylineDistanceV1(%s,{x:50,y:0})];"
                        % (json.dumps(poly), json.dumps(poly)))
        self.assertNear(d, [3, 50])

    # --- window / crossing selection -------------------------------------------------------
    def test_trace_follows_the_arc_for_window_selection(self):
        pts = self.run_js("return cblPolylineTraceV1(%s);" % json.dumps(_poly([(0, 0, 1), (100, 0), (100, 30)])))
        self.assertEqual(pts[0], {"x": 0, "y": 0})
        self.assertEqual(pts[-1], {"x": 100, "y": 30})
        self.assertAlmostEqual(min(p["y"] for p in pts), -50, places=6)
        for p in pts[:-1]:
            self.assertAlmostEqual(math.hypot(p["x"] - 50, p["y"]), 50, places=6)
        self.assertGreater(len(pts), 30)  # at most 5° per step on a half circle

    def test_editor_wires_the_curve_everywhere(self):
        html = _html()
        # Screen, block library, print preview, print and fit-to-page renderers.
        self.assertIsNone(re.search(r"lineTo\(s\.pts\[i\]\.x,s\.pts\[i\]\.y\)", html))
        self.assertGreaterEqual(len(re.findall(r"cblPolylinePathV1\((ctx|ctx3|px|ox),s\)", html)), 5)
        crosses = _editor_function(html, "function cblShapeCrossesSelectionRectV1(")
        self.assertIn("cblPolylineTraceV1(s)", crosses)
        block_segments = html[html.index("function cblSelectionBlockChildSegmentsV1("):]
        self.assertIn("cblPolylineTraceV1(ch)", block_segments[:block_segments.index("\nfunction ")])
        mouseup = html[html.index("cvs.addEventListener('mouseup'"):]
        self.assertIn("cblPolylineTraceV1(s),inside=", mouseup[:mouseup.index("sel=cblWindowSelectBlocksV1")])
        # Snapping keeps the real vertices, not sampled arc points.
        snap = html[html.index("if(t==='polyline'){ps=cblSelectionPolylinePointsV1(s);for(i=0;i<ps.length;i++)cblBlockSnapAddPointV1"):]
        self.assertTrue(snap)
