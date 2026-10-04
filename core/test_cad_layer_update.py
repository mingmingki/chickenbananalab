import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from unittest import skipUnless

from django.test import SimpleTestCase

from . import views as core_views
from .test_cad_dwg_text_validation import EXECUTABLE, _run_writer

try:
    import ezdxf
except ImportError:  # pragma: no cover - verification dependency only
    ezdxf = None


@skipUnless(EXECUTABLE is not None and ezdxf is not None, "ACadSharp runtime and ezdxf are required")
class CadLayerUpdateSaveTests(SimpleTestCase):
    """Layer colour, lineweight, on/off, lock and linetype edits are written to the DWG."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cbl-layer-update-"))
        self.original = self.tmp / "layers.dwg"
        create = self.tmp / "create.json"
        create.write_text(json.dumps({"ops": [
            {"type": "create_layer", "name": "LYR", "color": 3},
            {"type": "add_line", "layer": "LYR", "start": [0, 0, 0], "end": [100, 0, 0]},
        ]}), encoding="utf-8")
        _run_writer(["--create", self.original, "AC1018", create])

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def save(self, ops, validate=True):
        original_for_ops = core_views._cbl_free_dwg_save_local_json_v1(self.original, None)
        ops = core_views._cbl_normalize_free_dwg_ops_v1(original_for_ops, ops)
        ops_path = self.tmp / "ops.json"
        ops_path.write_text(json.dumps({"ops": ops}), encoding="utf-8")
        output = self.tmp / "saved.dwg"
        report = _run_writer([self.original, output, "AC1018", ops_path])
        if validate:
            core_views._cbl_free_dwg_save_local_validate_v1(self.original, output, None, ops, report)
        dxf = self.tmp / "saved.dxf"
        run = subprocess.run([str(EXECUTABLE), "--dxf", str(output), str(dxf)], capture_output=True, timeout=300)
        self.assertEqual(run.returncode, 0, run.stderr.decode("utf-8", "replace")[-800:])
        return ezdxf.readfile(str(dxf)).layers.get("LYR")

    def test_aci_lineweight_off_and_lock(self):
        layer = self.save([{"type": "update_layer", "name": "LYR", "aci": 1, "lineweight": 50, "on": False, "locked": True}])
        self.assertEqual(layer.color, 1)
        self.assertFalse(layer.is_on())
        self.assertTrue(layer.is_locked())
        self.assertEqual(layer.dxf.lineweight, 50)

    def test_true_color(self):
        layer = self.save([{"type": "update_layer", "name": "LYR", "trueColor": 0x3B82F6}])
        self.assertEqual(layer.rgb, (0x3B, 0x82, 0xF6))

    def test_linetype_present_in_the_drawing(self):
        layer = self.save([{"type": "update_layer", "name": "LYR", "linetype": "continuous"}])
        self.assertEqual(layer.dxf.linetype.upper(), "CONTINUOUS")

    def test_unknown_linetype_fails_the_save(self):
        with self.assertRaises(Exception) as caught:
            self.save([{"type": "update_layer", "name": "LYR", "linetype": "NOPE_LT"}], validate=False)
        self.assertIn("NOPE_LT", str(caught.exception))

    def test_unknown_layer_fails_the_save(self):
        with self.assertRaises(Exception):
            self.save([{"type": "update_layer", "name": "MISSING", "aci": 2}], validate=False)


@skipUnless(EXECUTABLE is not None and ezdxf is not None, "ACadSharp runtime and ezdxf are required")
class CadTrueColorByteOrderTests(SimpleTestCase):
    """Ops carry true colour as 0xRRGGBB (DXF group 420); the writer must not swap red and blue."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cbl-truecolor-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_new_and_updated_lines_keep_their_true_colour(self):
        original = self.tmp / "tc.dwg"
        create = self.tmp / "create.json"
        create.write_text(json.dumps({"ops": [
            {"type": "add_line", "layer": "0", "start": [0, 0, 0], "end": [100, 0, 0], "trueColor": 0x3B82F6},
        ]}), encoding="utf-8")
        _run_writer(["--create", original, "AC1018", create])
        handle = core_views._cbl_free_dwg_acadsharp_metadata_v1(original)["entities"][0]["handle"]
        ops = self.tmp / "ops.json"
        ops.write_text(json.dumps({"ops": [{"type": "update", "handle": handle, "entity": "LINE", "layer": "0",
                                            "start": [0, 10, 0], "end": [100, 10, 0], "trueColor": 0xF6823B, "aci": 256}]}))
        output = self.tmp / "out.dwg"
        _run_writer([original, output, "AC1018", ops])
        colours = []
        for path in (original, output):
            dxf = self.tmp / (path.stem + ".dxf")
            run = subprocess.run([str(EXECUTABLE), "--dxf", str(path), str(dxf)], capture_output=True, timeout=300)
            self.assertEqual(run.returncode, 0, run.stderr.decode("utf-8", "replace")[-800:])
            colours.append(tuple(next(iter(ezdxf.readfile(str(dxf)).modelspace())).rgb))
        self.assertEqual(colours, [(0x3B, 0x82, 0xF6), (0xF6, 0x82, 0x3B)])


class CadWriterErrorMessageTests(SimpleTestCase):
    def test_missing_linetype_is_explained_in_korean(self):
        detail = '{"status": "failed", "error": "System.IO.InvalidDataException: Linetype not found: Dashed\\n   at ..."}'
        message = core_views._cbl_free_dwg_writer_error_message_v1(detail)
        self.assertIn("도면에 없는 선종류(Dashed)", message)

    def test_region_without_payload_is_explained_in_korean(self):
        # AutoCAD 2013+ drawings keep REGION geometry where ACadSharp cannot
        # read it; the writer refuses rather than drop the REGION.
        detail = ('{"status": "failed", "error": "System.IO.InvalidDataException: Modeler geometry REGION '
                  'has no ACIS payload\\n   at ACadSharp.IO.DWG.DwgObjectWriter.writeModelerGeometry"}')
        message = core_views._cbl_free_dwg_writer_error_message_v1(detail)
        self.assertIn("면 영역(REGION)", message)
        self.assertIn("원본 파일은 바뀌지 않았습니다", message)
        self.assertNotIn("ACadSharp", message)

    def test_other_failures_keep_the_writer_detail(self):
        message = core_views._cbl_free_dwg_writer_error_message_v1("boom")
        self.assertEqual(message, "ACadSharp Save As 실패: boom")


import shutil as _shutil

from .test_cad_dwg_save_integrity import _build_ops_source, _html, _line
from .test_cad_layer_own_style import _function

NODE = _shutil.which("node")

LAYER_OPS_HARNESS = """
const window = {CBL_ACADSHARP_FULL_DXF_ACTIVE: true, CBL_CAD_TEXT_STYLES_V1: {}};
window.layers = %(layers)s;
window.CBL_FREE_DWG_ORIGINAL_LAYER_NAMES = window.layers.map(function(l){ return l.name; });
%(helpers)s
window.CBL_FREE_DWG_ORIGINAL_LAYER_PROPS_V1 = cblLayerPropsSnapshotV1(window.layers);
window.CBL_FREE_DWG_ORIGINAL_SHAPES = %(base)s;
window.shapes = %(shapes)s;
%(edit)s
process.stdout.write(JSON.stringify(buildOps().ops));
"""

LAYER = {"id": 1, "name": "0", "color": "#ffffff", "cblRawAci": 7, "cblRawLineType": "Continuous",
         "cblLineWeightRaw": -3, "visible": True, "locked": False}
CEN = {"id": 2, "name": "CEN", "color": "#ff0000", "cblRawAci": 1, "cblRawLineType": "CEN",
       "cblLineWeightRaw": -3, "visible": True, "locked": False}


@skipUnless(NODE, "node is required to execute the CAD save helpers")
class CadLayerUpdateOpsTests(SimpleTestCase):
    def ops(self, edit, shapes=None, base=None):
        script = LAYER_OPS_HARNESS % {"layers": json.dumps([LAYER, CEN]), "helpers": _build_ops_source(_html()),
                                      "base": json.dumps(base or [_line("A1")]), "shapes": json.dumps(shapes or [_line("A1")]),
                                      "edit": edit}
        run = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=60)
        self.assertEqual(run.returncode, 0, run.stderr)
        return json.loads(run.stdout)

    def test_unchanged_layers_send_nothing(self):
        self.assertEqual(self.ops(""), [])

    def test_layer_colour_lineweight_visibility_and_lock(self):
        ops = self.ops("var l=window.layers[1]; l.color='#ffff00'; l.cblLineWeightRaw=50; l.visible=false; l.locked=true;")
        self.assertEqual(ops, [{"type": "update_layer", "name": "CEN", "aci": 2, "lineweight": 50, "on": False, "locked": True}])

    def test_off_palette_colour_is_saved_as_true_colour(self):
        ops = self.ops("window.layers[0].color='#3B82F6';")
        self.assertEqual(ops, [{"type": "update_layer", "name": "0", "trueColor": 0x3B82F6}])

    def test_linetype_change(self):
        ops = self.ops("window.layers[0].cblRawLineType='CEN';")
        self.assertEqual(ops, [{"type": "update_layer", "name": "0", "linetype": "CEN"}])

    def test_object_colour_is_saved_on_the_object(self):
        ops = self.ops("cblSetObjectColorV1(window.shapes, '#ff0000');")
        self.assertEqual([(o["type"], o["handle"], o["aci"], o["trueColor"]) for o in ops], [("update", "A1", 1, None)])
        ops = self.ops("cblSetObjectColorV1(window.shapes, '#123456');")
        self.assertEqual([(o["type"], o["handle"], o["trueColor"]) for o in ops], [("update", "A1", 0x123456)])


class CadPropertyPanelWiringTests(SimpleTestCase):
    def test_panel_edits_selected_objects_not_the_current_layer(self):
        html = _html()
        start = html.index("const wrappedSyncSt = function (key, value) {")
        body = html[start:html.index("wrappedSyncSt.__cblLayerWideWrapped = true;", start)]
        guard = body.index("if (picked.length && (key === \"stroke\" || key === \"lineWidth\" || key === \"dash\"))")
        self.assertLess(guard, body.index("const l = cblCurrentLayer();"))
        self.assertIn("window.cblSetObjectColorV1(picked, value)", body)
        self.assertIn("cblSetObjectLinetypeV1(picked, value)", body)

    def test_layer_snapshot_is_taken_at_open_commit_and_new_drawing(self):
        html = _html()
        self.assertIn("window.CBL_FREE_DWG_ORIGINAL_LAYER_PROPS_V1=typeof window.cblLayerPropsSnapshotV1==='function'", html)
        self.assertIn("window.CBL_FREE_DWG_ORIGINAL_LAYER_PROPS_V1=cblLayerPropsSnapshotV1(window.layers);", html)
        # New drawing, and a drawing detached from its DWG (JSON open, new tab).
        self.assertEqual(html.count("window.CBL_FREE_DWG_ORIGINAL_LAYER_PROPS_V1={};"), 3)

    def test_save_api_keeps_its_csrf_exemption(self):
        # The browser posts DWG saves without a CSRF token; the view must stay exempt.
        self.assertTrue(getattr(core_views.cblcad_free_dwg_save_local_api, "csrf_exempt", False))
        self.assertFalse(getattr(core_views._cbl_free_dwg_writer_error_message_v1, "csrf_exempt", False))


APPLY_HARNESS = """
var layers = [];
%(helpers)s
var l = {id: 1, color: '#5f7f3f', lineWidth: 2, linetype: 'dash', lineWeightLabel: '0.50mm'};
var shapes = %(shapes)s;
shapes.forEach(function(s){ cblApplyLayerToShape(s, l); });
process.stdout.write(JSON.stringify(shapes.map(function(s){ return [s.stroke, s.dash, s.lineWidth]; })));
"""


@skipUnless(NODE, "node is required to execute the CAD editor helpers")
class CadLayerFollowTests(SimpleTestCase):
    """Only ByLayer properties follow the layer; an object's own colour/linetype/lineweight stay."""

    def apply(self, shapes):
        html = _html()
        start = html.index("  function cblApplyLayerToShape(s, l) {")
        body = html[start:html.index("\n  function cblSyncShapesByLayer", start)]
        weight = html.index("  function cblWeightLabel(width) {")
        helpers = html[weight:html.index("\n  function ", weight + 10)] + "\n" + body
        helpers = _function(html, "function cblShapeOwnStyleV1(s){") + "\n" + helpers
        script = APPLY_HARNESS % {"helpers": helpers, "shapes": json.dumps(shapes)}
        run = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=60)
        self.assertEqual(run.returncode, 0, run.stderr)
        return json.loads(run.stdout)

    def test_bylayer_shape_follows_the_layer(self):
        shape = {"layId": 1, "stroke": "#fff", "dash": "solid", "lineWidth": 1, "cblRawAci": 256,
                 "cblRawLinetype": "ByLayer", "cblRawLineWeight": -1}
        self.assertEqual(self.apply([shape]), [["#5f7f3f", "dash", 2]])

    def test_new_editor_shape_follows_the_layer(self):
        self.assertEqual(self.apply([{"layId": 1, "stroke": "#fff", "dash": "solid", "lineWidth": 1}]), [["#5f7f3f", "dash", 2]])

    def test_own_colour_linetype_and_lineweight_stay(self):
        shape = {"layId": 1, "stroke": "#00ff00", "dash": "HIDDEN", "lineWidth": 3, "cblRawAci": 3,
                 "cblRawLinetype": "HIDDEN", "cblRawLineWeight": 35}
        self.assertEqual(self.apply([shape]), [["#00ff00", "HIDDEN", 3]])

    def test_own_true_colour_stays(self):
        shape = {"layId": 1, "stroke": "#123456", "cblRawAci": 256, "cblRawTrueColor": 0x123456}
        self.assertEqual(self.apply([shape])[0][0], "#123456")
