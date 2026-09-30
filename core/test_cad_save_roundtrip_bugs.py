from unittest import skipUnless

from django.test import SimpleTestCase

from .test_cad_block_editing import _attrib, _child, _insert
from .test_cad_dwg_save_integrity import NODE, _BuildOpsRunner, _arc, _line


def _group(*children):
    return {"type": "group", "layId": 1, "ch": list(children), "stroke": "#fff", "fill": "none"}


@skipUnless(NODE, "node is required to execute the CAD save helpers")
class CadGroupSaveTests(_BuildOpsRunner, SimpleTestCase):
    """A group is an editor-only container; its members are still saved one by one."""

    def test_grouping_saved_shapes_sends_nothing(self):
        base = [_line("A1"), _arc("A2")]
        result = self.run_cases({"g": {"base": base, "shapes": [_group(_line("A1"), _arc("A2"))]}})["g"]
        self.assertEqual(result, {"ops": []})

    def test_moving_a_group_updates_its_members(self):
        base = [_line("A1"), _arc("A2")]
        moved = _group(_line("A1", x=50.0), _arc("A2", cx=100.0))
        result = self.run_cases({"g": {"base": base, "shapes": [moved]}})["g"]
        self.assertEqual(self.kinds(result), [("update", "A1"), ("update", "A2")])

    def test_new_shape_inside_a_group_is_added(self):
        result = self.run_cases({"g": {"base": [_line("A1")], "shapes": [_group(_line("A1"), _line(x=500.0))]}})["g"]
        self.assertEqual(self.kinds(result), [("add_line", None)])

    def test_nested_groups_are_flattened(self):
        base = [_line("A1"), _arc("A2")]
        result = self.run_cases({"g": {"base": base, "shapes": [_group(_group(_line("A1")), _arc("A2"))]}})["g"]
        self.assertEqual(result, {"ops": []})

    def test_ungrouping_after_a_save_sends_nothing(self):
        # After a save the committed baseline holds the group itself.
        base = [_group(_line("A1"), _arc("A2"))]
        result = self.run_cases({"g": {"base": base, "shapes": [_line("A1"), _arc("A2")]}})["g"]
        self.assertEqual(result, {"ops": []})

    def test_imported_dimension_groups_are_left_alone(self):
        # parseDXF turns a DWG DIMENSION into a group of plain lines/texts without handles.
        dim = {"type": "group", "rawDxfType": "DIMENSION", "dimensionHandle": "D1", "dimensionStyleName": "ISO-25",
               "ch": [_line(), {"type": "text", "layId": 1, "x": 0.0, "y": 5.0, "text": "100", "size": 2.5,
                                "cblDimensionText": True}]}
        result = self.run_cases({"g": {"base": [dim, _line("A1")], "shapes": [dim, _line("A1")]}})["g"]
        self.assertEqual(result, {"ops": []})
        moved = dict(dim, ch=[_line(x=50.0), dict(dim["ch"][1], x=50.0)])
        result = self.run_cases({"g": {"base": [dim], "shapes": [moved]}})["g"]
        self.assertEqual(result, {"ops": []})

    def test_deleting_a_group_deletes_its_members(self):
        base = [_group(_line("A1"), _arc("A2"))]
        result = self.run_cases({"g": {"base": base, "shapes": []}})["g"]
        self.assertEqual(self.kinds(result), [("delete", "A1"), ("delete", "A2")])


@skipUnless(NODE, "node is required to execute the CAD save helpers")
class CadRestoredShapeSaveTests(_BuildOpsRunner, SimpleTestCase):
    """A shape whose DWG entity was deleted by an earlier save (then restored by undo) is written again."""

    def test_restored_line_is_added_again(self):
        result = self.run_cases({"u": {"base": [_line("B1")], "shapes": [_line("B1"), _line("12239", x=300.0)]}})["u"]
        self.assertEqual(self.kinds(result), [("add_line", None)])
        self.assertEqual(result["ops"][0]["start"][:2], [300.0, 0.0])

    def test_shapes_still_in_the_file_are_untouched(self):
        result = self.run_cases({"u": {"base": [_line("B1"), _arc("B2")], "shapes": [_line("B1"), _arc("B2")]}})["u"]
        self.assertEqual(result, {"ops": []})

    def test_restored_block_is_inserted_by_name(self):
        base = [_line("B1")]
        shapes = [_line("B1"), _insert("1549F", x=20.0), _child("1549F", dx=20.0)]
        result = self.run_cases({"u": {"base": base, "shapes": shapes}})["u"]
        self.assertEqual(self.kinds(result), [("add_insert", None)])
        self.assertEqual(result["ops"][0]["copyOf"], "")
        self.assertEqual(result["ops"][0]["blockName"], "FDN")
        self.assertEqual(result["ops"][0]["insert"][:2], [20.0, 0.0])

    def test_restored_block_with_attributes_is_refused(self):
        base = [_line("B1")]
        shapes = [_line("B1"), _insert("34"), _child("34"), _attrib("34")]
        result = self.run_cases({"u": {"base": base, "shapes": shapes}})["u"]
        self.assertIn("되살린", result["error"])
        self.assertIn("속성", result["error"])

    def test_restored_attribute_alone_sends_nothing(self):
        # Its INSERT is still in the file; an attribute is never written on its own.
        base = [_insert("34")]
        result = self.run_cases({"u": {"base": base, "shapes": [_insert("34"), _attrib("34")]}})["u"]
        self.assertEqual(result, {"ops": []})


def _text(handle, size, **extra):
    shape = {"type": "text", "layId": 1, "rawLayerName": "0", "x": 0.0, "y": 0.0, "text": "T", "size": size, "rot": 0,
             "handle": handle, "sourceHandle": handle, "originalHandle": handle, "rawDxfType": "TEXT"}
    shape.update(extra)
    return shape


def _dim(**extra):
    shape = {"type": "dimlinear", "layId": 1, "rawLayerName": "0", "x1": 0.0, "y1": 0.0, "x2": 2000.0, "y2": 0.0,
             "off": -500, "dimType": "h"}
    shape.update(extra)
    return shape


@skipUnless(NODE, "node is required to execute the CAD save helpers")
class CadDimensionOpSizeTests(_BuildOpsRunner, SimpleTestCase):
    """The editor draws dimension text at a fixed screen size; the DWG needs drawing units."""

    def dim_op(self, texts):
        result = self.run_cases({"d": {"base": texts, "shapes": texts + [_dim()]}})["d"]
        ops = [o for o in result["ops"] if o["type"] == "add_dimension"]
        self.assertEqual(len(ops), 1, result)
        return ops[0]

    def test_dimension_text_follows_the_drawing_text_height(self):
        op = self.dim_op([_text("T1", 600), _text("T2", 300), _text("T3", 600)])
        self.assertEqual(op["textHeight"], 600)
        self.assertAlmostEqual(op["arrowSize"], 600 * 11 / 20)
        self.assertEqual(op["dimensionStyle"], "CBL_DIMSTYLE")

    def test_existing_dimension_text_wins(self):
        op = self.dim_op([_text("T1", 600), _text("T2", 250, cblDimensionText=True)])
        self.assertEqual(op["textHeight"], 250)


try:
    from .test_cad_dwg_text_validation import EXECUTABLE, _run_writer
except Exception:  # pragma: no cover - runtime missing
    EXECUTABLE = None

if EXECUTABLE is not None:
    import json
    import shutil
    import tempfile
    from pathlib import Path

    from django.conf import settings

    from . import views as core_views

    ARC_FIXTURE = Path(settings.BASE_DIR) / "core" / "test_fixtures" / "cad" / "arc_bulge_ac1018.dwg"


@skipUnless(EXECUTABLE is not None, "ACadSharp runtime is not installed")
class CadDimensionSaveValidationTests(SimpleTestCase):
    """Adding a dimension creates CBL_DIMSTYLE in the DWG; that is an expected change, not a broken file."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cbl-dim-save-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def save(self, ops):
        original_for_ops = core_views._cbl_free_dwg_save_local_json_v1(ARC_FIXTURE, None)
        ops = core_views._cbl_normalize_free_dwg_ops_v1(original_for_ops, ops)
        ops_path = self.tmp / "ops.json"
        ops_path.write_text(json.dumps({"ops": ops}), encoding="utf-8")
        output = self.tmp / "saved.dwg"
        report = _run_writer([ARC_FIXTURE, output, "AC1018", ops_path])
        core_views._cbl_free_dwg_save_local_validate_v1(ARC_FIXTURE, output, None, ops, report)
        return core_views._cbl_free_dwg_acadsharp_metadata_v1(output)

    def dimension_op(self, style="CBL_DIMSTYLE"):
        return {"type": "add_dimension", "dimensionKind": "linear", "layer": "0",
                "firstPoint": [0, 0, 0], "secondPoint": [2000, 0, 0], "definitionPoint": [2000, -500, 0],
                "textPosition": [1000, -500, 0], "rotation": 0, "measurement": 2000, "overrideText": "",
                "dimensionStyle": style, "lineColor": 7, "extensionColor": 7, "textColor": 3,
                "textHeight": 250, "arrowSize": 137.5, "textStyle": "STANDARD", "clientShapeId": "cbl-shape-1"}

    def test_adding_a_dimension_passes_validation(self):
        meta = self.save([self.dimension_op()])
        types = [str(e.get("type", "")).upper() for e in meta["entities"]]
        self.assertTrue(any(t.startswith("DIMENSION") for t in types), types)

    def test_adding_a_dimension_with_other_edits_passes_validation(self):
        line = {"type": "add_line", "layer": "0", "start": [0, 10, 0], "end": [100, 10, 0], "clientShapeId": "cbl-shape-2"}
        self.save([self.dimension_op(), line])


import json as _json
import math as _math
import subprocess as _subprocess

from .test_cad_dwg_save_integrity import _editor_function, _html

RECT_HARNESS = """
var sel = %(sel)s, hint = '';
function saveH(){} function requestCadRenderV1(){} function setHint(m){ hint = m; }
function cloneJ(x){ return JSON.parse(JSON.stringify(x)); }
function getBB(s){
  if (s.type === 'rect') return {x: s.x, y: s.y, w: s.w, h: s.h};
  var xs = s.pts.map(function(p){ return p.x; }), ys = s.pts.map(function(p){ return p.y; });
  var x = Math.min.apply(null, xs), y = Math.min.apply(null, ys);
  return {x: x, y: y, w: Math.max.apply(null, xs) - x, h: Math.max.apply(null, ys) - y};
}
%(helpers)s
process.stdout.write(JSON.stringify((function(){ %(body)s })()));
"""


@skipUnless(NODE, "node is required to execute the CAD editor helpers")
class CadRectTransformTests(SimpleTestCase):
    """A rectangle turned off its axes must actually turn."""

    RECT = {"type": "rect", "x": 0.0, "y": 0.0, "w": 40.0, "h": 20.0, "layId": 1, "stroke": "#fff",
            "sourceHandle": "R1", "handle": "R1"}

    def run_rect(self, body):
        html = _html()
        helpers = [_editor_function(html, sig) for sig in
                   ("function cblRectMapV1(s,f,keepRect){", "function rotateSelected(angle){", "function applyMirror(shps,pt1,pt2){")]
        script = RECT_HARNESS % {"sel": _json.dumps([dict(self.RECT)]), "helpers": "\n".join(helpers), "body": body}
        run = _subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=60)
        self.assertEqual(run.returncode, 0, run.stderr)
        return _json.loads(run.stdout)

    def assertPoints(self, pts, expected):
        self.assertEqual(len(pts), len(expected), pts)
        for p, (x, y) in zip(pts, expected):
            self.assertAlmostEqual(p["x"], x, places=6, msg=pts)
            self.assertAlmostEqual(p["y"], y, places=6, msg=pts)

    def test_rotating_by_30_degrees_turns_it_into_a_closed_polyline(self):
        s = self.run_rect("rotateSelected(30); return sel[0];")
        self.assertEqual(s["type"], "polyline")
        self.assertTrue(s["closed"])
        self.assertEqual(s["sourceHandle"], "R1")
        self.assertNotIn("w", s)
        c, sn = _math.cos(_math.radians(30)), _math.sin(_math.radians(30))
        expected = [(20 + (x - 20) * c - (y - 10) * sn, 10 + (x - 20) * sn + (y - 10) * c)
                    for x, y in ((0, 0), (40, 0), (40, 20), (0, 20))]
        self.assertPoints(s["pts"], expected)

    def test_quarter_turn_keeps_a_rectangle_and_swaps_its_sides(self):
        s = self.run_rect("rotateSelected(90); return sel[0];")
        self.assertEqual(s["type"], "rect")
        for key, value in (("x", 10.0), ("y", -10.0), ("w", 20.0), ("h", 40.0)):
            self.assertAlmostEqual(s[key], value, places=6, msg=s)

    def test_mirror_across_a_slanted_line_gives_the_reflected_corners(self):
        s = self.run_rect("return applyMirror(sel, {x:0, y:0}, {x:Math.cos(Math.PI/6), y:Math.sin(Math.PI/6)})[0];")
        self.assertEqual(s["type"], "polyline")
        t = _math.radians(60)  # reflection across a line at 30° = rotation of the axes by 2·30°

        def reflect(x, y):
            return x * _math.cos(t) + y * _math.sin(t), x * _math.sin(t) - y * _math.cos(t)
        self.assertPoints(s["pts"], [reflect(x, y) for x, y in ((0, 0), (40, 0), (40, 20), (0, 20))])

    def test_mirror_across_a_vertical_line_keeps_a_rectangle(self):
        s = self.run_rect("return applyMirror(sel, {x:100, y:0}, {x:100, y:10})[0];")
        self.assertEqual(s["type"], "rect")
        for key, value in (("x", 160.0), ("y", 0.0), ("w", 40.0), ("h", 20.0)):
            self.assertAlmostEqual(s[key], value, places=6, msg=s)


@skipUnless(NODE, "node is required to execute the CAD save helpers")
class CadTurnedRectSaveTests(_BuildOpsRunner, SimpleTestCase):
    def test_a_saved_rectangle_turned_into_a_polyline_updates_the_same_lwpolyline(self):
        rect = {"type": "rect", "layId": 1, "rawLayerName": "0", "x": 0.0, "y": 0.0, "w": 40.0, "h": 20.0,
                "handle": "1A1", "sourceHandle": "1A1", "originalHandle": "1A1", "cblRawAci": 256, "cblRawLineType": "ByLayer"}
        turned = dict(rect, type="polyline", closed=True,
                      pts=[{"x": 5.0, "y": -5.0}, {"x": 40.0, "y": 15.0}, {"x": 30.0, "y": 32.0}, {"x": -5.0, "y": 12.0}])
        for key in ("x", "y", "w", "h"):
            turned.pop(key)
        result = self.run_cases({"r": {"base": [rect], "shapes": [turned]}})["r"]
        self.assertEqual(self.kinds(result), [("update", "1A1")])
        op = result["ops"][0]
        self.assertTrue(op["closed"])
        self.assertEqual([pt[:2] for pt in op["points"]], [[5.0, -5.0], [40.0, 15.0], [30.0, 32.0], [-5.0, 12.0]])


CYCLE_HARNESS = """
const window = {layers: [{id: 1, name: '0'}], CBL_ACADSHARP_FULL_DXF_ACTIVE: true, CBL_CAD_TEXT_STYLES_V1: {},
                CBL_FREE_DWG_ORIGINAL_LAYER_NAMES: ['0']};
function cblRevisionCleanV1(x){ return x; } function cblComputeDocumentRevisionV1(){ return 'r'; }
%(snapshot)s
window.CBL_FREE_DWG_SNAPSHOT_API_V1 = {snapshotShape: cblFreeDwgSnapshotShapeV1};
%(helpers)s
const base = %(base)s, live = %(live)s;
window.CBL_FREE_DWG_ORIGINAL_SHAPES = base.map(cblFreeDwgSnapshotShapeV1);
window.shapes = live;
const out = {};
try {
  out.first = buildOps().ops.map(o => o.type + ':' + (o.handle || ''));
  // The first save commits: the baseline becomes a snapshot of the live model.
  window.CBL_FREE_DWG_ORIGINAL_SHAPES = prepareDwgSaveCommitV1({shapes: window.shapes}).baseline;
  %(edit)s
  out.second = buildOps().ops.map(o => o.type + ':' + (o.handle || ''));
} catch (e) { out.error = String(e && e.message || e); }
process.stdout.write(JSON.stringify(out));
"""


@skipUnless(NODE, "node is required to execute the CAD save helpers")
class CadGroupSaveCycleTests(SimpleTestCase):
    """Two saves in a row: the committed baseline must remember the members of a group."""

    def cycle(self, base, live, edit):
        html = _html()
        script = html.index('<script id="CBL_FREE_DWG_AC1018_SAVE_V1_SCRIPT">')
        start = html.index("  function shapes(){try{if(Array.isArray(window.shapes))", script)
        helpers = html[start:html.index("  function name(options){", start)]
        commit_start = html.index("  function prepareDwgSaveCommitV1(result){", script)
        helpers += html[commit_start:html.index("  var freeDwgSaveInFlightV1=false;", commit_start)]
        snap_start = html.index("  function cblFreeDwgSnapshotShapeV1(shape){")
        snapshot = html[snap_start:html.index("  window.CBL_FREE_DWG_SNAPSHOT_API_V1={", snap_start)]
        script_text = CYCLE_HARNESS % {"snapshot": snapshot, "helpers": helpers, "base": _json.dumps(base),
                                       "live": _json.dumps(live), "edit": edit}
        run = _subprocess.run([NODE, "-e", script_text], capture_output=True, text=True, timeout=60)
        self.assertEqual(run.returncode, 0, run.stderr)
        return _json.loads(run.stdout)

    def test_moving_a_saved_group_updates_its_members(self):
        base = [_line("A1"), _line("A2", y=50.0)]
        live = [_group(_line("A1"), _line("A2", y=50.0))]
        edit = "window.shapes[0].ch.forEach(function(s){ s.x1 += 10; s.x2 += 10; });"
        self.assertEqual(self.cycle(base, live, edit), {"first": [], "second": ["update:A1", "update:A2"]})

    def test_ungrouping_after_a_save_sends_nothing(self):
        base = [_line("A1"), _line("A2", y=50.0)]
        live = [_group(_line("A1"), _line("A2", y=50.0))]
        edit = "window.shapes = window.shapes[0].ch.slice();"
        self.assertEqual(self.cycle(base, live, edit), {"first": [], "second": []})

    def test_deleting_a_saved_group_deletes_its_members(self):
        base = [_line("A1"), _line("A2", y=50.0)]
        live = [_group(_line("A1"), _line("A2", y=50.0))]
        self.assertEqual(self.cycle(base, live, "window.shapes = [];"), {"first": [], "second": ["delete:A1", "delete:A2"]})


from .test_cad_dwg_save_integrity import BUILD_OPS_HARNESS, _build_ops_source

EMPTY_COMMIT_HARNESS = """
const window = {layers: [{id: 1, name: '0'}], CBL_ACADSHARP_FULL_DXF_ACTIVE: true, CBL_CAD_TEXT_STYLES_V1: {},
                CBL_FREE_DWG_SNAPSHOT_API_V1: {snapshotShape: function(s){ return JSON.parse(JSON.stringify(s)); }}};
function cblRevisionCleanV1(x){ return x; } function cblComputeDocumentRevisionV1(){ return 'r'; }
%(helpers)s
const out = {};
for (const allow of [false, true]) {
  try { out[String(allow)] = prepareDwgSaveCommitV1({shapes: [], allowEmpty: allow}).baseline; }
  catch (e) { out[String(allow)] = 'error: ' + e.message; }
}
process.stdout.write(JSON.stringify(out));
"""


@skipUnless(NODE, "node is required to execute the CAD save helpers")
class CadEmptyDrawingSaveTests(SimpleTestCase):
    """Deleting every entity is a real edit: save it after the user confirms, never silently."""

    def empties(self, cases):
        html = _html()
        script = BUILD_OPS_HARNESS % {"helpers": _build_ops_source(html), "cases": _json.dumps(cases)}
        script = script.replace("out[name] = {ops: buildOps().ops};",
                                "var pk = buildOps(); out[name] = {ops: pk.ops.map(function(o){return o.type;}), empties: pk.emptiesDrawing};")
        run = _subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=60)
        self.assertEqual(run.returncode, 0, run.stderr)
        return _json.loads(run.stdout)

    def test_deleting_everything_is_flagged(self):
        result = self.empties({"all": {"base": [_line("A1"), _arc("A2")], "shapes": []},
                               "some": {"base": [_line("A1"), _arc("A2")], "shapes": [_arc("A2")]},
                               "blank": {"base": [], "shapes": []}})
        self.assertEqual(result["all"], {"ops": ["delete", "delete"], "empties": True})
        self.assertEqual(result["some"], {"ops": ["delete"], "empties": False})
        self.assertEqual(result["blank"], {"ops": [], "empties": False})

    def test_commit_of_an_empty_model_needs_the_confirmation(self):
        html = _html()
        script = html.index('<script id="CBL_FREE_DWG_AC1018_SAVE_V1_SCRIPT">')
        start = html.index("  function shapes(){try{if(Array.isArray(window.shapes))", script)
        helpers = html[start:html.index("  function name(options){", start)]
        commit_start = html.index("  function prepareDwgSaveCommitV1(result){", script)
        helpers += html[commit_start:html.index("  var freeDwgSaveInFlightV1=false;", commit_start)]
        run = _subprocess.run([NODE, "-e", EMPTY_COMMIT_HARNESS % {"helpers": helpers}], capture_output=True, text=True, timeout=60)
        self.assertEqual(run.returncode, 0, run.stderr)
        result = _json.loads(run.stdout)
        self.assertIn("저장 기준 모델이 없어", result["false"])
        self.assertEqual(result["true"], [])

    def test_save_flow_asks_before_emptying_the_drawing(self):
        html = _html()
        start = html.index("    var pack=buildOps();")
        flow = html[start:start + 6000]
        self.assertIn("if(pack.emptiesDrawing&&", flow)
        self.assertIn("window.confirm(", flow[:flow.index("var fd=new FormData()")])
        self.assertEqual(html.count("shapes:pack.mappedShapes,allowEmpty:pack.emptiesDrawing===true"), 3)


@skipUnless(NODE, "node is required to execute the CAD save helpers")
class CadDuplicateShapeSaveTests(_BuildOpsRunner, SimpleTestCase):
    """Drawing the same line twice makes two entities; a saved twin must not swallow the new one."""

    DRAWN = {"type": "line", "layId": 1, "x1": 0.0, "y1": 200.0, "x2": 100.0, "y2": 200.0, "stroke": "#fff"}

    def test_duplicate_of_a_saved_line_is_added(self):
        saved = dict(self.DRAWN, handle="B6", sourceHandle="B6", originalHandle="B6")
        result = self.run_cases({"d": {"base": [saved], "shapes": [dict(saved), dict(self.DRAWN)]}})["d"]
        self.assertEqual(self.kinds(result), [("add_line", None)])

    def test_unhandled_imported_shape_is_still_recognised(self):
        # A top-level shape read without a handle stays in the baseline without one; it is not new.
        result = self.run_cases({"u": {"base": [dict(self.DRAWN)], "shapes": [dict(self.DRAWN)]}})["u"]
        self.assertEqual(result, {"ops": []})

    def test_one_unhandled_baseline_shape_stands_in_for_one_live_shape(self):
        result = self.run_cases({"u": {"base": [dict(self.DRAWN)], "shapes": [dict(self.DRAWN), dict(self.DRAWN)]}})["u"]
        self.assertEqual(self.kinds(result), [("add_line", None)])
