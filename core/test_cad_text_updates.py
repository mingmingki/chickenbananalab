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
        moved = dict(fridge, x=112.0)
        edited = dict(fridge, text="FREEZER")
        out = self.run_cases({
            "moved": {"base": [fridge, tiny], "shapes": [moved, dict(tiny, x=3.0)]},
            "edited": {"base": [fridge], "shapes": [edited]},
            "scaled": {"base": [tiny], "shapes": [dict(tiny, size=4, tw=40)]},
        })
        ops = {o["handle"]: o for o in out["moved"]["ops"]}
        # The DWG insertion point is the centre: (100, 100) moved 10 to the right.
        self.assertEqual([round(v, 6) for v in ops["30"]["insert"]], [110, 100, 0])
        self.assertNotIn("text", ops["30"])
        self.assertNotIn("height", ops["30"])
        self.assertNotIn("height", ops["40"])
        self.assertEqual(out["edited"]["ops"][0]["text"], "FREEZER")
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
        self.assertEqual(fridge["text"], "FRIDGE 555X585 [B180]")              # no paragraph code
        width = len(fridge["text"]) * 4 * 0.7
        # Centred: half the width back along the (turned) text, half a height down.
        self.assertAlmostEqual(fridge["x"], 100 + 2, places=6)
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
