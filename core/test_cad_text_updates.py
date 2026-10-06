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
from .test_cad_dwg_save_integrity import BUILD_OPS_HARNESS, NODE, _build_ops_source, _html
from .test_cad_dwg_text_validation import EXECUTABLE
from .test_cad_entity_display import HARNESS, _single_model_module

# ezdxf + ODA (review only): MTEXT 2F "TOP LEFT" at the origin (attachment 1,
# height 5), 30 "\pxqc;FRIDGE\P555X585\P[B180]" at (100, 100) centred
# (attachment 5, height 4, turned 90 degrees), 31 "RIGHT" at (200, 0)
# (attachment 9, height 5).
MTEXT = Path(settings.BASE_DIR) / "core" / "test_fixtures" / "cad" / "mtext_attach_ac1032.dwg"


def _text(handle, raw, **extra):
    shape = {"type": "text", "layId": 1, "rawLayerName": "0", "rawDxfType": raw, "handle": handle, "sourceHandle": handle,
             "originalHandle": handle, "cblRawAci": 256, "cblRawLineType": "ByLayer"}
    shape.update(extra)
    return shape


@skipUnless(NODE, "node is required to execute the CAD save helpers")
class CadTextUpdateTests(SimpleTestCase):
    """Moving or turning a text keeps what the editor does not show.

    A text's update always sent the editor's display text and height: moving a
    multi-line MTEXT saved it as one line without its formatting, and a text
    lower than 2 units (shown at 2) was saved at height 2.  An MTEXT is also
    placed by its attachment point, which the editor now honours.
    """

    def run_cases(self, cases):
        script = BUILD_OPS_HARNESS % {"helpers": _build_ops_source(_html()), "cases": json.dumps(cases)}
        run = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=60)
        self.assertEqual(run.returncode, 0, run.stderr)
        return json.loads(run.stdout)

    def test_moving_keeps_value_and_height_and_finds_the_attachment_point(self):
        fridge = _text("30", "MTEXT", text="FRIDGE 555X585 [B180]", x=102.0, y=70.6, size=4, rot=math.pi / 2, rotation=math.pi / 2,
                       tw=58.8, cblMTextAnchorV1=[0.5, 0.5], cblRawTextHeightV1=4)
        tiny = _text("40", "TEXT", text="N", x=0.0, y=0.0, size=2, rot=0, tw=20, cblRawTextHeightV1=0.25)
        lines = _text("50", "MTEXT", text="A\nB", x=0.0, y=0.0, size=5, rot=0, tw=20, cblMTextAnchorV1=[0, 1], cblRawTextHeightV1=5)
        moved = dict(fridge, x=112.0)
        edited = dict(fridge, text="FREEZER")
        out = self.run_cases({
            "moved": {"base": [fridge, tiny], "shapes": [moved, dict(tiny, x=3.0)]},
            "edited": {"base": [fridge], "shapes": [edited]},
            "lines_edited": {"base": [lines], "shapes": [dict(lines, text="A\nC")]},
            "scaled": {"base": [tiny], "shapes": [dict(tiny, size=4, tw=40)]},
        })
        ops = {o["handle"]: o for o in out["moved"]["ops"]}
        # The DWG insertion point is the centre: (100, 100) moved 10 to the right.
        self.assertEqual([round(v, 6) for v in ops["30"]["insert"]], [110, 100, 0])
        self.assertNotIn("text", ops["30"])
        self.assertNotIn("height", ops["30"])
        self.assertNotIn("height", ops["40"])
        self.assertEqual(out["edited"]["ops"][0]["text"], "FREEZER")
        # An MTEXT's line breaks are written as \\P.
        self.assertEqual(out["lines_edited"]["ops"][0]["text"], "A\\PC")
        self.assertEqual(out["scaled"]["ops"][0]["height"], 0.5)


@skipUnless(NODE and EXECUTABLE is not None, "node and the ACadSharp runtime are required")
class CadMTextAttachmentDisplayTests(SimpleTestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cbl-mtext-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def parse(self, dwg):
        path = self.tmp / "editor.dxf"
        path.write_text(core_views._cbl_free_dwg_to_dxf_text_v1(dwg)[0], encoding="utf-8")
        script = self.tmp / "harness.js"
        script.write_text(HARNESS.replace("rot: s.rot,", "rot: s.rot, anchor: s.cblMTextAnchorV1,") % {"module": _single_model_module(_html())}, encoding="utf-8")
        run = subprocess.run([NODE, str(script), str(path)], capture_output=True, text=True, timeout=120)
        self.assertEqual(run.returncode, 0, run.stderr[-1500:])
        return {s["handle"]: s for s in json.loads(run.stdout)["shapes"] if s["raw"] == "MTEXT"}

    def test_mtext_is_drawn_from_its_attachment_point(self):
        shapes = self.parse(MTEXT)
        top = shapes["2F"]
        self.assertEqual((top["x"], top["y"], top["anchor"]), (0, -5, [0, 1]))   # one height below the top
        fridge = shapes["30"]
        # Three lines (\\P), no paragraph code.
        self.assertEqual(fridge["text"], "FRIDGE\n555X585\n[B180]")
        width = max(7 * 4 * 0.7, 20)                     # the longest line
        height = 4 * (1 + 1.3 * 2)                       # three lines, 1.3 apart
        # Centred: half the width back along the (turned) text; the first
        # baseline one line height below the top of the centred block.
        drop = 4 - height / 2
        self.assertAlmostEqual(fridge["x"], 100 + drop, places=6)
        self.assertAlmostEqual(fridge["y"], 100 - width / 2, places=6)
        right = shapes["31"]
        self.assertEqual((right["x"], right["y"]), (200 - 20, 0))               # bottom right, width 20

    def test_a_moved_mtext_keeps_its_lines(self):
        ops = self.tmp / "ops.json"
        ops.write_text(json.dumps({"ops": [{"type": "update", "handle": "30", "entity": "MTEXT", "insert": [110, 100, 0], "rotation": math.pi / 2}]}))
        out = self.tmp / "moved.dwg"
        run = subprocess.run([str(EXECUTABLE), str(MTEXT), str(out), "AC1018", str(ops)], capture_output=True, timeout=300)
        self.assertEqual(run.returncode, 0, run.stderr[-800:])
        meta = core_views._cbl_free_dwg_acadsharp_metadata_v1(out)
        value = next(e for e in meta["entities"] if e["handle"] == "30")
        self.assertIn("\\P", value.get("text") or value.get("value") or "")


@skipUnless(NODE, "node is required to execute the CAD save helpers")
class CadMTextCopyTests(SimpleTestCase):
    """A copied MTEXT stays an MTEXT (lines, attachment and formatting).

    It was saved as a one-line TEXT at the shape's point; with lines and the
    attachment point that would be a TEXT showing \\P, in another place.
    """

    run_cases = CadTextUpdateTests.run_cases

    def test_copied_mtext(self):
        fridge = _text("30", "MTEXT", text="FRIDGE\nA", x=102.0, y=70.6, size=4, rot=math.pi / 2, rotation=math.pi / 2,
                       tw=20, cblMTextAnchorV1=[0.5, -0.8], cblRawTextHeightV1=4)
        moved = dict(fridge, x=152.0, cblCopiedFromHandle="30")
        for key in ("handle", "sourceHandle", "originalHandle"):
            moved.pop(key)
        edited = dict(moved, text="FREEZER\nA")
        out = self.run_cases({"copy": {"base": [fridge], "shapes": [fridge, moved]},
                              "edited_copy": {"base": [fridge], "shapes": [fridge, edited]}})
        op = out["copy"]["ops"][0]
        self.assertEqual((op["type"], op["copyOf"], op["entity"]), ("add_copy", "30", "MTEXT"))
        self.assertEqual([round(v, 6) for v in op["matrix"]], [1, 0, 0, 1, 50, 0])
        # An edited copy is still added as a one-line TEXT where it is shown.
        op = out["edited_copy"]["ops"][0]
        self.assertEqual((op["type"], op["text"]), ("add_text", "FREEZER A"))
        self.assertEqual([round(v, 6) for v in op["insert"]], [152, 70.6, 0])


@skipUnless(EXECUTABLE is not None, "ACadSharp runtime is required")
class CadMTextCopySaveTests(SimpleTestCase):
    def test_a_copied_mtext_is_saved_with_its_lines(self):
        from unittest.mock import patch
        from django.core.files.uploadedfile import SimpleUploadedFile
        from django.test import RequestFactory

        ops = [{"type": "add_copy", "copyOf": "30", "entity": "MTEXT", "matrix": [1, 0, 0, 1, 50, 0], "clientShapeId": "c1"}]
        fields = {"ops": json.dumps({"ops": ops}), "target_version": "AC1018", "filename": "m.dwg", "delivery": "binary",
                  "original_dwg": SimpleUploadedFile("m.dwg", MTEXT.read_bytes(), content_type="application/acad")}
        with patch.object(core_views, "_cbl_is_free_dwg_request", return_value=True), \
                patch.object(core_views, "_cbl_free_dwg_local_find_dwgread_v1", return_value=None):
            response = core_views.cblcad_free_dwg_save_local_api(RequestFactory().post("/api/cblcad/free-dwg-save/?mode=free-dwg", fields))
        self.assertEqual(response.status_code, 200, response.content[:400])
        tmp = Path(tempfile.mkdtemp(prefix="cbl-mtext-copy-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        (tmp / "out.dwg").write_bytes(response.content)
        meta = core_views._cbl_free_dwg_acadsharp_metadata_v1(tmp / "out.dwg")
        copy = core_views._cbl_free_dwg_output_handles_v1({"editReport": {"applied": [{"handle": json.loads(response["X-CBL-FREE-DWG-OUTPUT-HANDLES"])["0"]}]}}, ops)["0"]
        texts = {e["handle"]: e for e in meta["entities"] if e["type"] == "MTEXT"}
        self.assertIn("\\P", texts[copy].get("text") or texts[copy].get("value") or "")


DRAW_HARNESS = r"""
console.log = function () {};
var calls = [];
var ctx = {};
['save', 'restore', 'scale', 'translate', 'rotate', 'transform', 'setLineDash', 'beginPath', 'stroke'].forEach(function (n) { ctx[n] = function () {}; });
ctx.fillText = function (t, x, y) { calls.push(['fillText', t, x, y]); };
ctx.fillRect = function (x, y, w, h) { calls.push(['fillRect', x, y, w, h]); };
ctx.strokeRect = function () {};
ctx.moveTo = function (x, y) { calls.push(['moveTo', x, y]); };
ctx.lineTo = function () {};
ctx.measureText = function (t) { return {width: t.length * 10}; };
var window = globalThis;
window.ctx = ctx;
window.vScale = 1;
function setInterval() { return 0; }
function clearInterval() {}
function cblTextFontFamilyV1() { return 'sans-serif'; }
function cblTextRotationRadRenderFixV5(s) { return s.rot || 0; }
window.CBL_CAD_TEXT_STYLES_V1 = {STANDARD: {fontFile: 'txt.shx'}};
function cblTextStyleGetV1(name) { return window.CBL_CAD_TEXT_STYLES_V1[name] || null; }
window.drawShape = function () { calls.push(['old']); };
%(modules)s
var shx = {hasChar: function (c) { return c > 32; }, getCharShape: function (c, size) {
  return {polylines: [[{x: 0, y: 0}, {x: size / 2, y: size}]], lastPoint: {x: size, y: 0}};
}};
var out = {};
calls = [];
window.CBL_CAD_SHX_FONT_FILES_V1['txt.shx'] = {font: shx};
window.cblDrawShxTextV1({type: 'text', text: 'AB\nC', x: 0, y: 0, size: 5}, false);
out.shx = calls;
calls = [];
window.CBL_CAD_TEXT_STYLES_V1 = {STANDARD: {}};
window.drawShape({type: 'text', text: 'FRIDGE\n555X585\n[B180]', x: 100, y: 100, size: 4, rot: Math.PI / 2}, true);
out.rotated = calls;
process.stdout.write(JSON.stringify(out));
"""


def _between(html, start, end):
    begin = html.index(start)
    return html[begin:html.index(end, begin) + len(end)]


@skipUnless(NODE, "node is required to run the CAD drawing helpers")
class CadMultiLineTextDrawTests(SimpleTestCase):
    """A multi-line MTEXT is drawn line by line in every text renderer.

    The unrotated renderer split the lines, but a turned text (and any text
    in an SHX font) was drawn as one line: the line breaks vanished and the
    selection box covered only the first line.
    """

    def test_turned_and_shx_texts_draw_every_line(self):
        html = _html()
        modules = _between(html, "(function(){\n  if(window.__CBL_SHX_BIGFONT_RENDER_V1__)", "\n})();") + "\n" + \
            _between(html, "/* CBL_TEXT_DRAW_RUNTIME_ROTATION_FIX_V8_START */", "/* CBL_TEXT_DRAW_RUNTIME_ROTATION_FIX_V8_END */")
        run = subprocess.run([NODE, "-e", DRAW_HARNESS % {"modules": modules}], capture_output=True, text=True, timeout=60)
        self.assertEqual(run.returncode, 0, run.stderr[-1500:])
        out = json.loads(run.stdout)
        # SHX: A and B on the first baseline, C back at the start one line pitch (1.3 heights) lower.
        self.assertEqual([c[1:] for c in out["shx"] if c[0] == "moveTo"], [[0, 0], [5, 0], [0, -6.5]])
        self.assertNotIn("fillText", [c[0] for c in out["shx"]])
        rotated = out["rotated"]
        self.assertEqual([c[1:] for c in rotated if c[0] == "fillText"],
                         [["FRIDGE", 0, 0], ["555X585", 0, 5.2], ["[B180]", 0, 10.4]])
        box = next(c for c in rotated if c[0] == "fillRect")
        self.assertEqual(round(box[3], 6), 74)                    # the longest line (7 x 10) plus the margin
        self.assertEqual(round(box[4], 6), round(4 + 6 + 2 * 4 * 1.3, 6))
        self.assertNotIn(["old"], rotated)
