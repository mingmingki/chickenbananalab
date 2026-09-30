import json
import subprocess
from unittest import skipUnless

from django.test import SimpleTestCase

from .test_cad_dwg_save_integrity import NODE, _editor_function, _html

HARNESS = """
var shapes = [], sel = [], styl = {stroke: '#fff', fill: 'none'}, actLay = 1, vScale = %(vscale)s;
var drawing = false, startPt = null, dimAlignPt2 = null, hint = '';
function saveH(){} function updUI(){} function render(){} function setHint(m){ hint = m; }
function requestAnimationFrame(){}
function cblIsDisplayableShapeV1(){ return true; }
%(helpers)s
process.stdout.write(JSON.stringify((function(){ %(body)s })()));
"""

HELPERS = ["function cblDimAlignedOffsetV1(p1,p2,p){", "function cblDimAlignedClickV1(p){"]


@skipUnless(NODE, "node is required to execute the CAD editor helpers")
class CadDimAlignedToolTests(SimpleTestCase):
    """DAL places like AutoCAD: first point, second point, then the dimension line."""

    def run_dal(self, body, vscale=1, helpers=HELPERS):
        html = _html()
        script = HARNESS % {"helpers": "\n".join(_editor_function(html, sig) for sig in helpers), "body": body,
                            "vscale": vscale}
        run = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=60)
        self.assertEqual(run.returncode, 0, run.stderr)
        return json.loads(run.stdout)

    def test_third_click_sets_the_dimension_line_offset(self):
        result = self.run_dal("""
            var states = [];
            cblDimAlignedClickV1({x:0, y:0});   states.push([drawing, shapes.length, hint]);
            cblDimAlignedClickV1({x:100, y:0}); states.push([drawing, shapes.length, hint]);
            cblDimAlignedClickV1({x:40, y:30});
            return {states: states, shape: shapes[0], drawing: drawing, left: [startPt, dimAlignPt2], sel: sel.length};
        """)
        self.assertEqual(result["states"][0][:2], [True, 0])
        self.assertIn("두 번째 점", result["states"][0][2])
        self.assertEqual(result["states"][1][:2], [True, 0])
        self.assertIn("치수선 위치", result["states"][1][2])
        shape = result["shape"]
        self.assertEqual(shape["type"], "dimaligned")
        self.assertEqual([shape["x1"], shape["y1"], shape["x2"], shape["y2"]], [0, 0, 100, 0])
        self.assertAlmostEqual(shape["off"], 30)  # left of 0,0 -> 100,0 is +y
        self.assertEqual((result["drawing"], result["left"], result["sel"]), (False, [None, None], 1))

    def test_offset_on_the_right_side_is_negative(self):
        off = self.run_dal("return cblDimAlignedOffsetV1({x:0,y:0},{x:100,y:0},{x:70,y:-45});")
        self.assertAlmostEqual(off, -45)
        off = self.run_dal("return cblDimAlignedOffsetV1({x:0,y:0},{x:0,y:100},{x:-20,y:50});")
        self.assertAlmostEqual(off, 20)

    def test_second_click_on_the_first_point_is_ignored(self):
        result = self.run_dal("cblDimAlignedClickV1({x:0,y:0}); cblDimAlignedClickV1({x:0.5,y:0}); return [drawing, dimAlignPt2];")
        self.assertEqual(result, [True, None])

    def test_selection_box_covers_the_dimension_line_on_either_side(self):
        helpers = HELPERS + ["function getBB(s){"]
        box = self.run_dal("return [getBB({type:'dimaligned',x1:0,y1:0,x2:100,y2:0,off:300}),"
                           "getBB({type:'dimaligned',x1:0,y1:0,x2:100,y2:0,off:-300})];", helpers=helpers)
        self.assertLessEqual(box[0]["y"], 0)
        self.assertGreaterEqual(box[0]["y"] + box[0]["h"], 300)
        self.assertLessEqual(box[1]["y"], -300)
        self.assertGreaterEqual(box[1]["y"] + box[1]["h"], 0)

    def test_editor_wiring_and_names(self):
        html = _html()
        self.assertNotIn("dimaligned:'각도치수'", html)
        self.assertIn("dimaligned:'사선치수'", html)
        mousedown = html[html.index("cvs.addEventListener('mousedown',function(e){"):]
        mousedown = mousedown[:mousedown.index("\n});")]
        self.assertIn("if(tool==='dimaligned'){cblDimAlignedClickV1(wp);return;}", mousedown)
        self.assertLess(mousedown.index("cblDimAlignedClickV1(wp)"), mousedown.index("// Other drawing tools"))
        self.assertIn("dimAlignPt2=null", _editor_function(html, "function setTool(t){"))
        self.assertGreaterEqual(html.count("cblDimAlignedPreviewV1("), 3)  # definition + two previews


@skipUnless(NODE, "node is required to execute the CAD editor helpers")
class CadTextEditorPlacementTests(SimpleTestCase):
    """The text box stays inside the drawing area instead of being cut off at its edge."""

    def place(self, *args):
        html = _html()
        script = _editor_function(html, "function cblTextEditorPositionV1(x,y,boxW,boxH,areaW,areaH){") + \
            "\nprocess.stdout.write(JSON.stringify(cblTextEditorPositionV1(%s)));" % ",".join(str(a) for a in args)
        run = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=60)
        self.assertEqual(run.returncode, 0, run.stderr)
        return json.loads(run.stdout)

    def test_box_near_the_right_edge_moves_left(self):
        self.assertEqual(self.place(539, 310, 539, 241, 715, 520), {"left": 168, "top": 242})

    def test_box_near_the_bottom_moves_up(self):
        self.assertEqual(self.place(100, 500, 300, 241, 715, 520), {"left": 100, "top": 271})

    def test_box_larger_than_the_area_starts_at_the_margin(self):
        self.assertEqual(self.place(300, 5, 900, 241, 715, 520), {"left": 8, "top": 10})

    def test_show_ted_uses_the_placement(self):
        body = _editor_function(_html(), "function showTed(wp,existing){")
        self.assertIn("cblTextEditorPositionV1(", body)
        self.assertLess(body.index("ed.classList.add('on')"), body.index("cblTextEditorPositionV1("))
