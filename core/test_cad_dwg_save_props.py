import json
import shutil
import subprocess
from pathlib import Path
from unittest import skipUnless

from django.conf import settings
from django.test import SimpleTestCase

CAD_HTML = Path(settings.BASE_DIR) / "core" / "static" / "core" / "tools" / "CBLCAD_VER2.html"
NODE = shutil.which("node")

HARNESS = """
const window = {layers: [{id: 11, name: 'A-계단'}, {id: 20, name: '0_STR_OPEN_HID'}, {id: 1, name: '0'}]};
%(helpers)s
const cases = %(cases)s;
const out = {};
for (const [name, c] of Object.entries(cases)) {
  out[name] = {
    same: sameGeometry(c.base, c.current),
    update: opForShape(c.current, true),
    add: opForShape(c.current, false),
  };
}
process.stdout.write(JSON.stringify(out));
"""


def _save_helpers_source():
    """The free-DWG save helpers exactly as shipped in the CAD page."""
    html = CAD_HTML.read_text(encoding="utf-8")
    start = html.index("  function layers(){try{if(Array.isArray(window.layers))")
    end = html.index("  function isTopLevelEditable(s){", start)
    return html[start:end]


def _imported_line(**overrides):
    # Shape as the DWG import leaves it when the save baseline is captured.
    shape = {
        "type": "line", "handle": "11DDC", "sourceHandle": "11DDC", "layId": 11, "rawLayerName": "A-계단",
        "x1": 100.0, "y1": 200.0, "x2": 300.0, "y2": 200.0,
        "linetype": "HID", "lineType": "HID", "dash": "HID", "cblLineType": "HID",
        "cblRawLineType": "HID", "cblRawLinetype": "HID", "cblRawAci": 256, "cblRawTrueColor": None,
        "cblRawLineWeight": -1, "lineweight": -3, "color": "#00ffff", "stroke": "#00ffff",
    }
    shape.update(overrides)
    return shape


def _rendered(shape, **overrides):
    # Renderer/layer patches rewrite display fields after the baseline is taken.
    current = dict(shape, linetype="solid", dash="solid", stroke="#808080")
    current.update(overrides)
    return current


class _SaveHelpersRunner:
    def run_cases(self, cases):
        script = HARNESS % {"helpers": _save_helpers_source(), "cases": json.dumps(cases, ensure_ascii=False)}
        run = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=60)
        self.assertEqual(run.returncode, 0, run.stderr)
        return json.loads(run.stdout)


@skipUnless(NODE, "node is required to execute the CAD save helpers")
class CadDwgSavePropertyTests(_SaveHelpersRunner, SimpleTestCase):

    def test_display_only_rewrites_are_not_treated_as_edits(self):
        hidden = _imported_line()
        colored = _imported_line(handle="11C8D", sourceHandle="11C8D", layId=20, rawLayerName="0_STR_OPEN_HID",
                                 linetype="D2", lineType="D2", dash="D2", cblRawLineType="ByLayer",
                                 cblRawLinetype="ByLayer", cblRawAci=4)
        result = self.run_cases({
            "hidden": {"base": hidden, "current": _rendered(hidden)},
            "colored": {"base": colored, "current": _rendered(colored)},
        })
        self.assertTrue(result["hidden"]["same"])
        self.assertTrue(result["colored"]["same"])

    def test_edited_dwg_entity_keeps_its_original_linetype_color_and_lineweight(self):
        hidden = _imported_line()
        colored = _imported_line(handle="11C8D", sourceHandle="11C8D", layId=20, rawLayerName="0_STR_OPEN_HID",
                                 cblRawLineType="ByLayer", cblRawLinetype="ByLayer", cblRawAci=4)
        result = self.run_cases({
            "hidden": {"base": hidden, "current": _rendered(hidden, x1=130.0, x2=330.0)},
            "colored": {"base": colored, "current": _rendered(colored, x1=130.0, x2=330.0)},
        })
        self.assertFalse(result["hidden"]["same"])
        update = result["hidden"]["update"]
        self.assertEqual((update["type"], update["handle"]), ("update", "11DDC"))
        self.assertEqual(update["start"][0], 130.0)
        # The DWG's own linetype name, not a renderer alias ("solid") or a
        # canonical rename ("Hidden") that is missing from the drawing's table.
        self.assertEqual(update["linetype"], "HID")
        self.assertEqual(update["aci"], 256)
        self.assertEqual(update["lineweight"], -1)
        colored_update = result["colored"]["update"]
        self.assertEqual(colored_update["aci"], 4)
        self.assertEqual(colored_update["linetype"], "ByLayer")

    def test_new_editor_shape_without_dwg_properties_is_unchanged(self):
        drawn = {"type": "line", "layId": 1, "x1": 0, "y1": 0, "x2": 10, "y2": 0,
                 "linetype": "solid", "dash": "solid", "color": "#ffffff", "lineWidth": 1.5}
        result = self.run_cases({"drawn": {"base": drawn, "current": drawn}})
        add = result["drawn"]["add"]
        self.assertEqual(add["type"], "add_line")
        self.assertEqual(add["linetype"], "Continuous")
        self.assertEqual(add["aci"], 256)


def _imported_text(**overrides):
    # A TEXT from the full-DXF import carries no style fields; the DWG keeps
    # its own style (e.g. 돋움체) and width factor (e.g. 0.9).
    shape = {
        "type": "text", "handle": "B6D", "sourceHandle": "B6D", "layId": 1, "rawLayerName": "TEX",
        "rawDxfType": "TEXT", "text": "지하주차장 기초구조평면도", "x": 153365.09, "y": 13900.13, "size": 600, "rot": 0,
        "cblRawLineType": "ByLayer", "cblRawAci": 256, "cblRawTrueColor": None, "cblRawLineWeight": -1,
    }
    shape.update(overrides)
    return shape


@skipUnless(NODE, "node is required to execute the CAD save helpers")
class CadDwgTextStyleSaveTests(_SaveHelpersRunner, SimpleTestCase):
    def test_moving_imported_text_does_not_reset_its_dwg_style(self):
        text = _imported_text()
        result = self.run_cases({"moved": {"base": text, "current": dict(text, x=153395.09)}})
        update = result["moved"]["update"]
        self.assertEqual((update["type"], update["handle"]), ("update", "B6D"))
        self.assertEqual(update["insert"][0], 153395.09)
        # Unknown style values are omitted so the writer keeps the DWG's own.
        for key in ("textStyle", "widthFactor", "obliqueAngle"):
            self.assertNotIn(key, update)

    def test_style_chosen_in_the_editor_is_still_sent(self):
        text = _imported_text(textStyleName="HAUD01", textStyle="HAUD01", widthFactor=0.8, obliqueAngle=15)
        result = self.run_cases({"styled": {"base": _imported_text(), "current": text}})
        update = result["styled"]["update"]
        self.assertEqual(update["textStyle"], "HAUD01")
        self.assertEqual(update["widthFactor"], 0.8)
        self.assertEqual(update["obliqueAngle"], 15)

    def test_new_editor_text_keeps_standard_defaults(self):
        drawn = {"type": "text", "layId": 1, "text": "NEW", "x": 0, "y": 0, "size": 14}
        add = self.run_cases({"drawn": {"base": drawn, "current": drawn}})["drawn"]["add"]
        self.assertEqual(add["type"], "add_text")
        self.assertEqual(add["textStyle"], "STANDARD")
        self.assertEqual(add["widthFactor"], 1)
        self.assertEqual(add["obliqueAngle"], 0)
