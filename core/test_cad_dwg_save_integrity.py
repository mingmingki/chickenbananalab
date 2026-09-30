import json
import math
import shutil
import subprocess
from pathlib import Path
from unittest import skipUnless

from django.conf import settings
from django.test import SimpleTestCase

CAD_HTML = Path(settings.BASE_DIR) / "core" / "static" / "core" / "tools" / "CBLCAD_VER2.html"
NODE = shutil.which("node")

BUILD_OPS_HARNESS = """
const window = {layers: [{id: 1, name: '0'}], CBL_ACADSHARP_FULL_DXF_ACTIVE: true,
                CBL_FREE_DWG_ORIGINAL_LAYER_NAMES: ['0'], CBL_CAD_TEXT_STYLES_V1: {}};
%(helpers)s
const cases = %(cases)s;
const out = {};
for (const [name, c] of Object.entries(cases)) {
  window.shapes = c.shapes;
  window.CBL_FREE_DWG_ORIGINAL_SHAPES = c.base;
  try { out[name] = {ops: buildOps().ops}; } catch (e) { out[name] = {error: String(e && e.message || e)}; }
}
process.stdout.write(JSON.stringify(out));
"""

OUTPUT_HANDLES_HARNESS = """
const window = {layers: [{id: 1, name: '0'}]};
%(helpers)s
(async () => {
  const shape = {type: 'line', layId: 1, x1: 0, y1: 0, x2: 10, y2: 0, clientShapeId: 'cbl-shape-7'};
  window.shapes = [shape];
  const pack = {ops: [{type: 'add_line', clientShapeId: 'cbl-shape-7'}], pendingAddRefs: [{opIndex: 0, shape}]};
  await applyOutputHandlesV1('{"0": "3540B"}', pack);
  const applied = {handle: shape.handle, sourceHandle: shape.sourceHandle, mapped: pack.mappedShapes[0].sourceHandle};
  pack.restoreOutputHandlesV1();
  const restored = {handle: shape.handle, sourceHandle: shape.sourceHandle, originalHandle: shape.originalHandle,
                    keys: Object.keys(shape).sort()};
  process.stdout.write(JSON.stringify({applied, restored}));
})().catch(e => { process.stderr.write(String(e && e.stack || e)); process.exit(1); });
"""


def _html():
    return CAD_HTML.read_text(encoding="utf-8")


def _build_ops_source(html):
    script = html.index('<script id="CBL_FREE_DWG_AC1018_SAVE_V1_SCRIPT">')
    start = html.index("  function shapes(){try{if(Array.isArray(window.shapes))", script)
    return html[start:html.index("  function safeName(value){", start)]


def _output_handles_source(html):
    script = html.index('<script id="CBL_FREE_DWG_AC1018_SAVE_V1_SCRIPT">')
    start = html.index("  function parseOutputHandlesV1(value){", script)
    return html[start:html.index("  function prepareDwgSaveCommitV1(result){", start)]


def _line(handle=None, x=0.0, y=0.0, **extra):
    shape = {"type": "line", "layId": 1, "rawLayerName": "0", "x1": x, "y1": y, "x2": x + 100.0, "y2": y,
             "cblRawAci": 256, "cblRawLineType": "ByLayer", "cblRawLineWeight": -1}
    if handle:
        shape.update(handle=handle, sourceHandle=handle, originalHandle=handle, rawDxfType="LINE")
    shape.update(extra)
    return shape


def _arc(handle=None, cx=50.0, cy=50.0, **extra):
    shape = {"type": "arc", "layId": 1, "rawLayerName": "0", "cx": cx, "cy": cy, "r": 20.0,
             "a1": math.pi / 6, "a2": 5 * math.pi / 6, "cblRawAci": 256, "cblRawLineType": "ByLayer"}
    if handle:
        shape.update(handle=handle, sourceHandle=handle, originalHandle=handle, rawDxfType="ARC")
    shape.update(extra)
    return shape


def _poly(handle=None, dy=0.0, bulges=(0, 1, 0, 0), **extra):
    pts = [{"x": 0.0, "y": 100.0 + dy}, {"x": 40.0, "y": 100.0 + dy}, {"x": 80.0, "y": 100.0 + dy},
           {"x": 80.0, "y": 140.0 + dy}]
    for pt, bulge in zip(pts, bulges):
        pt["bulge"] = bulge
    shape = {"type": "polyline", "layId": 1, "rawLayerName": "0", "pts": pts, "closed": False,
             "cblRawAci": 256, "cblRawLineType": "ByLayer"}
    if handle:
        shape.update(handle=handle, sourceHandle=handle, originalHandle=handle, rawDxfType="LWPOLYLINE")
    shape.update(extra)
    return shape


class _BuildOpsRunner:
    def run_cases(self, cases):
        script = BUILD_OPS_HARNESS % {"helpers": _build_ops_source(_html()), "cases": json.dumps(cases)}
        run = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=60)
        self.assertEqual(run.returncode, 0, run.stderr)
        return json.loads(run.stdout)

    @staticmethod
    def kinds(result):
        return [(op["type"], op.get("handle") or None) for op in result["ops"]]


@skipUnless(NODE, "node is required to execute the CAD save helpers")
class CadDwgSaveOpsIntegrityTests(_BuildOpsRunner, SimpleTestCase):
    """What the browser sends to the DWG writer must match what the user sees."""

    def test_copy_of_an_imported_shape_is_added_and_the_original_stays(self):
        original = _line("111FD")
        copy = _line("111FD", x=500.0)  # the copy command cloned the DWG identity too
        result = self.run_cases({"copy": {"base": [original], "shapes": [dict(original), copy]}})["copy"]
        self.assertEqual(self.kinds(result), [("add_line", None)])
        self.assertEqual(result["ops"][0]["start"][:2], [500.0, 0.0])

    def test_copy_after_moving_the_original_updates_one_and_adds_one(self):
        original = _line("111FD")
        result = self.run_cases({"c": {"base": [original],
                                       "shapes": [_line("111FD", x=50.0), _line("111FD", x=500.0)]}})["c"]
        self.assertEqual(self.kinds(result), [("update", "111FD"), ("add_line", None)])
        self.assertEqual(result["ops"][0]["start"][:2], [50.0, 0.0])
        self.assertEqual(result["ops"][1]["start"][:2], [500.0, 0.0])

    def test_pasted_new_shapes_get_distinct_client_ids(self):
        first = _line(x=0.0, clientShapeId="cbl-shape-3")
        second = _line(x=300.0, clientShapeId="cbl-shape-3")
        result = self.run_cases({"p": {"base": [], "shapes": [first, second]}})["p"]
        ids = [op["clientShapeId"] for op in result["ops"]]
        self.assertEqual(len(ids), 2)
        self.assertNotEqual(ids[0], ids[1])

    def test_new_arc_is_saved(self):
        result = self.run_cases({"a": {"base": [], "shapes": [_arc()]}})["a"]
        self.assertEqual(self.kinds(result), [("add_arc", None)])
        op = result["ops"][0]
        self.assertEqual(op["center"][:2], [50.0, 50.0])
        self.assertEqual(op["radius"], 20.0)
        self.assertAlmostEqual(op["startAngle"], math.pi / 6)
        self.assertAlmostEqual(op["endAngle"], 5 * math.pi / 6)

    def test_moved_imported_arc_is_updated(self):
        result = self.run_cases({"m": {"base": [_arc("11A68")], "shapes": [_arc("11A68", cx=60.0)]}})["m"]
        self.assertEqual(self.kinds(result), [("update", "11A68")])
        self.assertEqual(result["ops"][0]["center"][:2], [60.0, 50.0])
        self.assertAlmostEqual(result["ops"][0]["endAngle"], 5 * math.pi / 6)

    def test_polyline_bulges_are_sent_for_updates_and_adds(self):
        result = self.run_cases({
            "moved": {"base": [_poly("15449")], "shapes": [_poly("15449", dy=10.0)]},
            "new": {"base": [], "shapes": [_poly(bulges=(0.5, 0, -1, 0))]},
        })
        self.assertEqual(result["moved"]["ops"][0]["bulges"], [0, 1, 0, 0])
        self.assertEqual(result["new"]["ops"][0]["bulges"], [0.5, 0, -1, 0])

    def test_bulge_only_edit_is_detected(self):
        result = self.run_cases({"b": {"base": [_poly("15449")], "shapes": [_poly("15449", bulges=(0, 0, 0, 0))]}})["b"]
        self.assertEqual(self.kinds(result), [("update", "15449")])

    def test_unsupported_new_shape_stops_the_save_instead_of_vanishing(self):
        ellipse = {"type": "ellipse", "layId": 1, "cx": 0.0, "cy": 0.0, "rx": 10.0, "ry": 5.0}
        result = self.run_cases({"e": {"base": [], "shapes": [ellipse]}})["e"]
        self.assertIn("error", result)
        self.assertIn("ellipse", result["error"])

    def test_unsupported_edit_of_an_imported_shape_stops_the_save(self):
        base = {"type": "spline", "layId": 1, "handle": "2A", "sourceHandle": "2A", "rawDxfType": "SPLINE",
                "pts": [{"x": 0.0, "y": 0.0}, {"x": 10.0, "y": 5.0}]}
        moved = dict(base, pts=[{"x": 5.0, "y": 0.0}, {"x": 15.0, "y": 5.0}])
        result = self.run_cases({"s": {"base": [base], "shapes": [moved]}})["s"]
        self.assertIn("error", result)
        self.assertIn("spline", result["error"].lower())

    def test_untouched_unsupported_shape_does_not_block_saving(self):
        base = {"type": "spline", "layId": 1, "handle": "2A", "sourceHandle": "2A", "rawDxfType": "SPLINE",
                "pts": [{"x": 0.0, "y": 0.0}, {"x": 10.0, "y": 5.0}]}
        result = self.run_cases({"s": {"base": [base, _line("2B")],
                                       "shapes": [dict(base), _line("2B", x=5.0)]}})["s"]
        self.assertEqual(self.kinds(result), [("update", "2B")])


def _insert(handle="1549F", x=0.0, **extra):
    shape = {"type": "blockref", "layId": 1, "rawLayerName": "0", "name": "FDN", "blockName": "FDN",
             "x": x, "y": 0.0, "rotation": 0.0, "scaleX": 1, "scaleY": 1, "scaleZ": 1, "rawDxfType": "INSERT",
             "handle": handle, "sourceHandle": handle, "originalHandle": handle}
    shape.update(extra)
    return shape


def _child_text(owner="1549F", dx=0.0, **extra):
    shape = {"type": "text", "layId": 1, "rawLayerName": "0", "text": "F1", "x": 10.0 + dx, "y": 5.0, "size": 300,
             "rawDxfType": "TEXT", "sourceHandle": "1576D", "sourcePath": owner + "/15773/1576D",
             "blockChild": True, "isBlockChild": True, "fromBlock": True, "parentBlockName": "FDN",
             "parentInsertName": "FDN", "blockName": "FDN", "ownerSourceHandle": owner, "parentSourceHandle": owner}
    shape.update(extra)
    return shape


@skipUnless(NODE, "node is required to execute the CAD save helpers")
class CadDwgSaveBlockContentTests(_BuildOpsRunner, SimpleTestCase):
    """Block contents are drawn from flattened children but saved only through their INSERT."""

    def test_untouched_block_saves_nothing(self):
        result = self.run_cases({"b": {"base": [_insert(), _child_text()], "shapes": [_insert(), _child_text()]}})["b"]
        self.assertEqual(result, {"ops": []})

    def test_child_display_differences_never_block_saving(self):
        # Real S-501 import: the baseline holds more flattened children than the live
        # model, and dimension text children are rotated after the baseline is taken.
        result = self.run_cases({"b": {
            "base": [_insert(), _child_text(), _child_text(sourcePath="1549F/15773/99")],
            "shapes": [_insert(), _child_text(rotation=1.5707963267948966), _line(x=900.0)],
        }})["b"]
        self.assertEqual(self.kinds(result), [("add_line", None)])

    def test_moving_the_whole_block_updates_only_the_insert(self):
        result = self.run_cases({"b": {"base": [_insert(), _child_text()],
                                       "shapes": [_insert(x=100.0), _child_text(dx=100.0)]}})["b"]
        self.assertEqual(self.kinds(result), [("update", "1549F")])


@skipUnless(NODE, "node is required to execute the CAD save helpers")
class CadDwgSaveHandleRollbackTests(SimpleTestCase):
    def test_failed_save_restores_the_new_shape_without_a_dwg_handle(self):
        html = _html()
        helpers = _build_ops_source(html) + _output_handles_source(html)
        run = subprocess.run([NODE, "-e", OUTPUT_HANDLES_HARNESS % {"helpers": helpers}],
                             capture_output=True, text=True, timeout=60)
        self.assertEqual(run.returncode, 0, run.stderr)
        result = json.loads(run.stdout)
        self.assertEqual(result["applied"], {"handle": "3540B", "sourceHandle": "3540B", "mapped": "3540B"})
        # JSON drops undefined values, so a removed handle is simply absent.
        self.assertIsNone(result["restored"].get("handle"))
        self.assertIsNone(result["restored"].get("sourceHandle"))
        self.assertIsNone(result["restored"].get("originalHandle"))
        self.assertNotIn("sourceHandle", result["restored"]["keys"])

    def test_every_failure_path_of_the_save_restores_handles(self):
        html = _html()
        script = html.index('<script id="CBL_FREE_DWG_AC1018_SAVE_V1_SCRIPT">')
        save = html[html.index("  window.cblFreeDwgSaveAC1018V1=async function(options){", script):
                    html.index("  window.cblFreeDwgExportAC1018V1=function(options){", script)]
        # The in-use native file path returns early, and every thrown error ends in the catch.
        in_use = save[save.index("if(isNativeWriteStateErrorV1(writeError)){"):save.index("return false;", save.index("if(isNativeWriteStateErrorV1(writeError)){"))]
        self.assertIn("restoreOutputHandlesV1", in_use)
        catch = save[save.rindex("}catch(e){"):]
        self.assertIn("restoreOutputHandlesV1", catch)


class CadFreeDwgSaveAsShortcutTests(SimpleTestCase):
    """In free-DWG mode Save As belongs to the native dispatcher; the legacy V4 path overwrote the original file."""

    def test_legacy_save_as_steps_aside_in_free_dwg_mode(self):
        html = _html()
        start = html.index("<!-- CBLCAD_CREATE_REAL_SAVE_AS_BUTTON_V4_START -->")
        block = html[start:html.index("<!-- CBLCAD_CREATE_REAL_SAVE_AS_BUTTON_V4_END -->", start)]
        run_save_as = block[block.index("async function runRealSaveAs(ev) {"):block.index("var suggestedName = todayName();")]
        # Checked before preventDefault/stopImmediatePropagation so the native handlers still see the event.
        self.assertIn("freeDwgLocal === true", run_save_as)
        self.assertLess(run_save_as.index("freeDwgLocal === true"), run_save_as.index("ev.preventDefault()"))


EDITOR_HARNESS = """
var shapes = [], styl = {stroke: '#fff'}, actLay = 1, vScale = %(vscale)s, polyPts = %(poly)s, drawing = true, hint = '';
function saveH(){} function updUI(){} function render(){} function setHint(m){ hint = m; }
function cloneJ(x){ return JSON.parse(JSON.stringify(x)); }
%(helpers)s
process.stdout.write(JSON.stringify((function(){ %(body)s })()));
"""


def _editor_function(html, signature):
    start = html.index(signature)
    end = html.index("\nfunction ", start + len(signature))
    return html[start:end]


@skipUnless(NODE, "node is required to execute the CAD editor helpers")
class CadEditorCommandTests(SimpleTestCase):
    def run_editor(self, helpers, body, vscale=1, poly=()):
        html = _html()
        source = "\n".join(_editor_function(html, sig) for sig in helpers)
        script = EDITOR_HARNESS % {"helpers": source, "body": body, "vscale": vscale, "poly": json.dumps(list(poly))}
        run = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=60)
        self.assertEqual(run.returncode, 0, run.stderr)
        return json.loads(run.stdout)

    def test_mirror_reverses_arcs_and_polyline_bulges_and_moves_text(self):
        result = self.run_editor(["function applyMirror(shps,pt1,pt2){"], """
            var out = applyMirror([
              {type:'arc', cx:100, cy:0, r:10, a1:0, a2:Math.PI/2},
              {type:'polyline', pts:[{x:0,y:0,bulge:1},{x:10,y:0}]},
              {type:'text', x:100, y:50, text:'ABC', tw:30, rotation:0}
            ], {x:0,y:0}, {x:0,y:10});
            return {arc:[out[0].cx, out[0].a1*180/Math.PI, out[0].a2*180/Math.PI], bulge:out[1].pts[0].bulge, text:[out[2].x, out[2].y]};
        """)
        self.assertEqual([round(v, 6) for v in result["arc"]], [-100, 90, 180])
        self.assertEqual(result["bulge"], -1)
        self.assertEqual(result["text"], [-130, 50])

    def test_double_click_end_does_not_repeat_the_last_vertex(self):
        result = self.run_editor(["function finishPolylineV1(){"], """
            finishPolylineV1(); return {pts: shapes[0].pts.length, closed: !!shapes[0].closed, left: polyPts.length};
        """, poly=[{"x": 0, "y": 0}, {"x": 10, "y": 0}, {"x": 20, "y": 5}, {"x": 20, "y": 5}])
        self.assertEqual(result, {"pts": 3, "closed": False, "left": 0})

    def test_closing_a_polyline_marks_it_closed(self):
        result = self.run_editor(["function closePolyline(){"], """
            globalThis.requestAnimationFrame = function(){}; closePolyline(); return {closed: shapes[0].closed, pts: shapes[0].pts.length};
        """, poly=[{"x": 0, "y": 0}, {"x": 10, "y": 0}, {"x": 10, "y": 10}])
        self.assertEqual(result, {"closed": True, "pts": 4})

    def test_new_text_follows_the_drawing_scale(self):
        body = "return cblDefaultTextSizeV1();"
        helpers = ["function cblDefaultTextSizeV1(){"]
        self.assertEqual(self.run_editor(helpers, "shapes=[{type:'text',size:600},{type:'text',size:300},{type:'text',size:600}];" + body), 600)
        self.assertEqual(self.run_editor(helpers, body, vscale=1), 14)
        self.assertEqual(self.run_editor(helpers, body, vscale=0.0032), 4000)

    def test_zoom_label_shows_small_percentages(self):
        helpers = ["function cblZoomLabelV1(scale){"]
        self.assertEqual(self.run_editor(helpers, "return [cblZoomLabelV1(0.0032), cblZoomLabelV1(0.05), cblZoomLabelV1(1.5)];"),
                         ["0.32%", "5.0%", "150%"])


class CadFreeDwgShortcutFocusTests(SimpleTestCase):
    """After typing a command the focus stays in the command line; Cmd+S must still save the DWG."""

    def test_legacy_json_shortcuts_step_aside_in_free_dwg_mode(self):
        html = _html()
        start = html.index("<!-- CBLCAD_BETA_V255_BOX_V1_START -->")
        block = html[start:html.index("<!-- CBLCAD_BETA_V255_BOX_V1_END -->", start)]
        handler = block[block.index("document.addEventListener('keydown', function(e){"):block.index("cblBottomSaveJSON();", block.index("document.addEventListener('keydown', function(e){"))]
        self.assertIn("freeDwgLocal === true", handler)

    def test_free_dwg_shortcuts_work_from_the_command_line(self):
        html = _html()
        for marker in ("window.cblNativeDwgSaveFromGestureV1.__cblChromeDispatcherV1=true;",
                       "  function newNow(ev){return resetNew();}"):
            start = html.index(marker)
            handler = html[html.index("document.addEventListener('keydown',function(ev){", start):]
            handler = handler[:handler.index("},true);")]
            self.assertIn("'cminp'", handler)
