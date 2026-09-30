import json
import math
import subprocess
from unittest import skipUnless

from django.test import SimpleTestCase

from .test_cad_dwg_save_integrity import NODE, _BuildOpsRunner, _editor_function, _html

ARC_HARNESS = """
var shapes = [], sel = [], styl = {stroke: '#fff', fill: 'none'}, actLay = 1, vScale = %(vscale)s;
var drawing = false, startPt = null, arcMidPt = null, hint = '', renders = 0;
function saveH(){} function updUI(){} function render(){ renders++; } function setHint(m){ hint = m; }
function requestAnimationFrame(){}
%(helpers)s
process.stdout.write(JSON.stringify((function(){ %(body)s })()));
"""

ARC_HELPERS = ["function cblArcFrom3PointsV1(p1,p2,p3){", "function cblArcClickV1(p){", "function cblArcPreviewV1(c,cur){"]


def _passes_through(arc, point):
    """True when `point` lies on the CCW sweep a1 -> a2 of the arc."""
    two_pi = 2 * math.pi
    ang = math.atan2(point[1] - arc["cy"], point[0] - arc["cx"])
    sweep = (arc["a2"] - arc["a1"]) % two_pi
    return (ang - arc["a1"]) % two_pi <= sweep + 1e-9


@skipUnless(NODE, "node is required to execute the CAD editor helpers")
class CadArcToolTests(SimpleTestCase):
    """ARC draws like AutoCAD: start point, a point on the arc, end point."""

    def run_arc(self, body, vscale=1):
        html = _html()
        source = "\n".join(_editor_function(html, sig) for sig in ARC_HELPERS)
        script = ARC_HARNESS % {"helpers": source, "body": body, "vscale": vscale}
        run = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=60)
        self.assertEqual(run.returncode, 0, run.stderr)
        return json.loads(run.stdout)

    def arc_from(self, p1, p2, p3):
        return self.run_arc("return cblArcFrom3PointsV1(%s,%s,%s);" % tuple(
            json.dumps({"x": p[0], "y": p[1]}) for p in (p1, p2, p3)))

    def assertArc(self, arc, cx, cy, r, a1_deg, a2_deg, places=6):
        self.assertAlmostEqual(arc["cx"], cx, places=places)
        self.assertAlmostEqual(arc["cy"], cy, places=places)
        self.assertAlmostEqual(arc["r"], r, places=places)
        self.assertAlmostEqual(math.degrees(arc["a1"]), a1_deg, places=places)
        self.assertAlmostEqual(math.degrees(arc["a2"]), a2_deg, places=places)

    def test_counter_clockwise_points_give_the_upper_half(self):
        self.assertArc(self.arc_from((10, 0), (0, 10), (-10, 0)), 0, 0, 10, 0, 180)

    def test_clockwise_points_give_the_same_arc_stored_counter_clockwise(self):
        self.assertArc(self.arc_from((-10, 0), (0, 10), (10, 0)), 0, 0, 10, 0, 180)

    def test_the_arc_goes_through_the_second_point_below(self):
        arc = self.arc_from((-10, 0), (0, -10), (10, 0))
        self.assertArc(arc, 0, 0, 10, 180, 0)
        self.assertTrue(_passes_through(arc, (0, -10)))
        self.assertFalse(_passes_through(arc, (0, 10)))

    def test_a_short_arc_through_three_points(self):
        # Quarter arc of the circle c=(100,50) r=20: 0deg -> 45deg -> 90deg.
        s45 = 20 * math.sqrt(0.5)
        arc = self.arc_from((120, 50), (100 + s45, 50 + s45), (100, 70))
        self.assertArc(arc, 100, 50, 20, 0, 90)

    def test_angles_stay_between_0_and_360(self):
        arc = self.arc_from((0, -10), (-10, 0), (0, 10))  # clockwise through the left side
        for key in ("a1", "a2"):
            self.assertGreaterEqual(arc[key], 0)
            self.assertLess(arc[key], 2 * math.pi)
        self.assertTrue(_passes_through(arc, (-10, 0)))

    def test_large_drawing_coordinates_stay_accurate(self):
        ox, oy = 312456.789, 548123.456
        arc = self.arc_from((ox + 5000, oy), (ox, oy + 5000), (ox - 5000, oy))
        self.assertArc(arc, ox, oy, 5000, 0, 180, places=4)

    def test_points_on_one_line_do_not_make_an_arc(self):
        self.assertIsNone(self.arc_from((0, 0), (5, 5), (10, 10)))
        self.assertIsNone(self.arc_from((0, 0), (10, 0), (0, 0)))

    def test_three_clicks_draw_one_arc_through_the_middle_click(self):
        result = self.run_arc("""
            var states = [];
            cblArcClickV1({x:10, y:0});  states.push([drawing, shapes.length, hint]);
            cblArcClickV1({x:0, y:10});  states.push([drawing, shapes.length, hint]);
            cblArcClickV1({x:-10, y:0}); states.push([drawing, shapes.length]);
            return {states: states, shape: shapes[0], sel: sel.length, left: [startPt, arcMidPt]};
        """)
        states = result["states"]
        self.assertEqual(states[0][:2], [True, 0])
        self.assertIn("두 번째 점", states[0][2])
        self.assertEqual(states[1][:2], [True, 0])  # still drawing: heavy-drawing finish waits for the last click
        self.assertIn("끝점", states[1][2])
        self.assertEqual(states[2], [False, 1])
        shape = result["shape"]
        self.assertEqual(shape["type"], "arc")
        self.assertEqual(shape["layId"], 1)
        self.assertArc(shape, 0, 0, 10, 0, 180)
        self.assertEqual(result["sel"], 1)
        self.assertEqual(result["left"], [None, None])

    def test_a_straight_end_point_waits_for_another_click(self):
        result = self.run_arc("""
            cblArcClickV1({x:10, y:0}); cblArcClickV1({x:0, y:10}); cblArcClickV1({x:-10, y:20});
            var first = {drawing: drawing, count: shapes.length, hint: hint};
            cblArcClickV1({x:-10, y:0});
            return {first: first, count: shapes.length, shape: shapes[0]};
        """)
        self.assertEqual(result["first"]["count"], 0)
        self.assertTrue(result["first"]["drawing"])
        self.assertIn("직선", result["first"]["hint"])
        self.assertEqual(result["count"], 1)
        self.assertArc(result["shape"], 0, 0, 10, 0, 180)

    def test_second_click_on_the_start_point_is_ignored(self):
        result = self.run_arc("""
            cblArcClickV1({x:0, y:0}); cblArcClickV1({x:0.5, y:0});
            return {mid: arcMidPt, drawing: drawing};
        """)
        self.assertEqual(result, {"mid": None, "drawing": True})

    def test_preview_draws_a_line_then_the_three_point_arc(self):
        result = self.run_arc("""
            var calls = [];
            var c = {beginPath:function(){calls.push(['begin']);}, moveTo:function(x,y){calls.push(['move',x,y]);},
                     lineTo:function(x,y){calls.push(['line',x,y]);}, stroke:function(){calls.push(['stroke']);},
                     arc:function(){calls.push(['arc'].concat([].slice.call(arguments)));}};
            startPt = {x:10, y:0};
            cblArcPreviewV1(c, {x:0, y:10});
            var first = calls; calls = [];
            arcMidPt = {x:0, y:10};
            cblArcPreviewV1(c, {x:-10, y:0});
            return {first: first, second: calls};
        """)
        self.assertEqual(result["first"], [["begin"], ["move", 10, 0], ["line", 0, 10], ["stroke"]])
        arc_call = [c for c in result["second"] if c[0] == "arc"]
        self.assertEqual(len(arc_call), 1)
        self.assertEqual([round(v, 6) for v in arc_call[0][1:5]], [0, 0, 10, 0])
        self.assertAlmostEqual(arc_call[0][5], math.pi)

    def test_editor_wires_the_three_point_tool(self):
        html = _html()
        # Both previews (normal render and the heavy-drawing overlay) share the three-point preview.
        self.assertNotIn("dst(startPt,cur),0,Math.PI", html)
        self.assertNotIn("dst(ref, cur), 0, Math.PI", html)
        self.assertGreaterEqual(html.count("cblArcPreviewV1(ctx,"), 2)
        mousedown = html[html.index("cvs.addEventListener('mousedown',function(e){"):]
        mousedown = mousedown[:mousedown.index("\n});")]
        self.assertIn("if(tool==='arc'){cblArcClickV1(wp);return;}", mousedown)
        self.assertLess(mousedown.index("cblArcClickV1(wp)"), mousedown.index("// Other drawing tools"))
        set_tool = _editor_function(html, "function setTool(t){")
        self.assertIn("arcMidPt=null", set_tool)
        self.assertNotIn("tool==='arc')s=Object.assign(s,{type:'arc'", html)


@skipUnless(NODE, "node is required to execute the CAD save helpers")
class CadArcToolSaveTests(_BuildOpsRunner, SimpleTestCase):
    def test_a_drawn_arc_is_saved_as_add_arc_with_its_angles(self):
        arc = {"type": "arc", "layId": 1, "stroke": "#fff", "cx": 0, "cy": 0, "r": 10,
               "a1": math.pi, "a2": 0, "clientShapeId": "arc-1"}
        result = self.run_cases({"a": {"base": [], "shapes": [arc]}})["a"]
        self.assertEqual(len(result.get("ops", [])), 1, result)
        op = result["ops"][0]
        self.assertEqual(op["type"], "add_arc")
        self.assertEqual(op["center"][:2], [0, 0])
        self.assertEqual(op["radius"], 10)
        self.assertAlmostEqual(op["startAngle"], math.pi)
        self.assertEqual(op["endAngle"], 0)
