import json
import subprocess
from unittest import skipUnless

from django.test import SimpleTestCase

from .test_cad_dwg_save_integrity import NODE, _html


def _function(html, signature):
    """Source of the function starting at `signature`, up to its closing brace."""
    start = html.index(signature)
    depth = 0
    for i in range(html.index("{", start), len(html)):
        depth += {"{": 1, "}": -1}.get(html[i], 0)
        if depth == 0:
            return html[start:i + 1]
    raise AssertionError("unterminated " + signature)


# Shapes on layer 2 when its colour, linetype and lineweight change.
SHAPES = [
    # imported true colour, ByLayer linetype/lineweight
    {"id": "tc", "layId": 2, "stroke": "#3b82f6", "cblRawTrueColor": 0x3B82F6, "cblRawLinetype": "ByLayer", "cblRawLineWeight": -1},
    # imported explicit ACI 1
    {"id": "aci", "layId": 2, "stroke": "#ff0000", "cblRawAci": 1, "cblRawLinetype": "ByLayer"},
    # imported ByLayer everything
    {"id": "bylayer", "layId": 2, "stroke": "#8844cc", "cblRawAci": 256, "cblRawLinetype": "ByLayer", "cblRawLineWeight": -1},
    # ByBlock colour follows the layer here, as before
    {"id": "byblock", "layId": 2, "stroke": "#8844cc", "cblRawAci": 0},
    # drawn in the editor; an earlier layer pass left cblRawLineType behind
    {"id": "drawn", "layId": 2, "stroke": "#8844cc", "cblRawLineType": "Continuous", "dash": "Continuous"},
    # own linetype and lineweight (from the DWG or the property panel)
    {"id": "own_lt_lw", "layId": 2, "stroke": "#8844cc", "cblRawAci": 256, "cblRawLinetype": "HIDDEN", "dash": "HIDDEN",
     "cblRawLineWeight": 50, "lineWidth": 7},
    {"id": "other_layer", "layId": 1, "stroke": "#ffffff"},
    {"id": "grp", "type": "group", "layId": 2, "ch": [
        {"id": "child_own", "layId": 2, "stroke": "#00ff00", "cblRawAci": 3},
        {"id": "child_bylayer", "layId": 2, "stroke": "#8844cc"},
    ]},
]

LAYER = {"id": 2, "name": "TC", "color": "#00aa55", "lineType": "DASHED", "linetype": "DASHED",
         "cblLineType": "DASHED", "cblRawLineType": "DASHED", "lineWidth": 3}

HARNESS = """
var shapes = %(shapes)s, layers = [{id:1,name:'0',color:'#ffffff'}, %(layer)s];
function getShapes(){ return shapes; }
function getLayers(){ return layers; }
function normalizeLayer(l){ return l; }
function ensureLayer(l){ return l; }
function effectiveLayerLW(l){ return {kind:'mm', mm:0.3, raw:30}; }
function mmToPx(mm){ return mm * 10; }
function lwToPx(v){ return Number(v) * 10; }
function ltNorm(v){ return v; }
function normLT(v){ return v; }
function cblWeightLabel(v){ return String(v); }
%(helpers)s
%(call)s
var out = {};
(function walk(list){ list.forEach(function(s){ out[s.id] = {stroke: s.stroke, dash: s.dash, lineWidth: s.lineWidth};
  if (s.ch) walk(s.ch); }); })(shapes);
process.stdout.write(JSON.stringify(out));
"""

PREDICATE = "function cblShapeOwnStyleV1(s){"
MODULES = {
    # layer panel rows + their install() on every layer-panel click/change
    "panel": (["function applyLayerToShapes(l){if(!l)return;normalizeLayer(l);"], "applyLayerToShapes(layers[1]);"),
    # top toolbar "current layer colour"
    "toolbar": (["function shapeEachInLayer(layerId, fn){", "function applyLayerStyleToShapes(layer){"],
                "applyLayerStyleToShapes(layers[1]);"),
    "sync_bridge": (["function applyLayerToShapes(l){\n    l = ensureLayer(l);"], "applyLayerToShapes(layers[1]);"),
    "wide_manager": (["function cblApplyLayerToShape(s, l) {"],
                     "shapes.forEach(function(s){ cblApplyLayerToShape(s, layers[1]); });"),
}

OWN = {"tc", "aci", "child_own"}
FOLLOW = {"bylayer", "byblock", "drawn", "own_lt_lw", "child_bylayer"}


@skipUnless(NODE, "node is required to execute the CAD layer helpers")
class CadLayerKeepsOwnStyleTests(SimpleTestCase):
    """A layer edit repaints only ByLayer properties; objects with their own keep them (AutoCAD)."""

    def run_module(self, name):
        html = _html()
        sigs, call = MODULES[name]
        helpers = "\n".join([_function(html, PREDICATE)] + [_function(html, sig) for sig in sigs])
        script = HARNESS % {"shapes": json.dumps(SHAPES), "layer": json.dumps(LAYER), "helpers": helpers, "call": call}
        run = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=60)
        self.assertEqual(run.returncode, 0, run.stderr)
        return json.loads(run.stdout)

    def test_every_layer_module_keeps_own_colour(self):
        for name in MODULES:
            with self.subTest(module=name):
                out = self.run_module(name)
                self.assertEqual({k for k in OWN | FOLLOW if out[k]["stroke"] == "#00aa55"}, FOLLOW)
                self.assertEqual(out["tc"]["stroke"], "#3b82f6")
                self.assertEqual(out["aci"]["stroke"], "#ff0000")
                self.assertEqual(out["child_own"]["stroke"], "#00ff00")
                self.assertEqual(out["other_layer"]["stroke"], "#ffffff")

    def test_every_layer_module_keeps_own_linetype_and_lineweight(self):
        for name in MODULES:
            with self.subTest(module=name):
                out = self.run_module(name)
                self.assertEqual(out["own_lt_lw"]["dash"], "HIDDEN")
                self.assertEqual(out["own_lt_lw"]["lineWidth"], 7)
                self.assertNotEqual(out["drawn"]["dash"], "Continuous")  # a stale cblRawLineType is not "own"
                self.assertNotEqual(out["bylayer"]["lineWidth"], None)

    def test_own_style_rule(self):
        html = _html()
        script = _function(html, PREDICATE) + """
        process.stdout.write(JSON.stringify([
          {}, {cblRawAci: 256}, {cblRawAci: 0}, {cblRawAci: '1'}, {cblRawTrueColor: 0}, {cblRawTrueColor: ''},
          {cblRawLineType: 'HIDDEN'}, {cblRawLinetype: 'BYLAYER'}, {cblRawLinetype: 'Continuous'},
          {cblRawLineWeight: -1}, {cblRawLineWeight: -2}, {cblRawLineWeight: -3}, {cblRawLineWeight: '50'}, {cblRawLineWeight: 'ByLayer'}
        ].map(cblShapeOwnStyleV1)));"""
        run = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=60)
        self.assertEqual(run.returncode, 0, run.stderr)
        rows = [[r["color"], r["linetype"], r["lineweight"]] for r in json.loads(run.stdout)]
        self.assertEqual(rows, [
            [False, False, False], [False, False, False], [False, False, False], [True, False, False],
            [True, False, False], [False, False, False],
            [False, False, False], [False, False, False], [False, True, False],
            [False, False, False], [False, False, False], [False, False, True], [False, False, True], [False, False, False],
        ])
