import json
import math
import subprocess
from unittest import skipUnless

from django.test import SimpleTestCase

from .test_cad_dwg_save_integrity import NODE, _html


def _module_functions(html, start_marker, signatures):
    """Functions of one IIFE module, found after its start marker (names repeat across modules).

    A function ends at the closing brace on its own indentation; counting braces
    fails here because the modules' regexes contain "{" and "}".
    """
    begin = html.index(start_marker)
    out = []
    for sig in signatures:
        at = html.index(sig, begin)
        line_start = html.rfind("\n", 0, at) + 1
        first = html[line_start:html.index("\n", at)]
        if first.count("{") == first.count("}"):
            out.append(first)
            continue
        close = "\n" + html[line_start:at] + "}"
        out.append(html[line_start:html.index(close, at) + len(close)])
    return "\n".join(out)


def _run(script):
    run = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=60)
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout)


DEG = math.pi / 180

# S-501 (2차보완)-16: model-space TEXT "6,600" (handle 36016, −25°) and two DIM
# MTEXT "6,600" (D978 at −25°, D84F at 45°) whose own shapes start at 0°.
DIM_SHAPES = [
    {"handle": "36016", "type": "text", "text": "6,600", "layer": "DIM", "rawDxfType": "TEXT",
     "x": 47989.75, "y": 33612.89, "rot": 335 * DEG},
    {"handle": "D978", "type": "text", "text": "6,600", "layer": "DIM", "rawDxfType": "MTEXT",
     "x": 52000.0, "y": 30000.0, "rot": 0},
    {"handle": "D84F", "type": "text", "text": "6,600", "layer": "DIM", "rawDxfType": "MTEXT",
     "x": 90000.0, "y": 90000.0, "rot": 0},
]
DIM_VECTORS = [
    {"handle": "D978", "text": "6,600", "layer": "DIM", "x": 52000.0, "y": 30000.0, "rawRot": -25 * DEG, "rot": -25 * DEG},
    # Its DXF point sits nearer the model-space TEXT than its own shape.
    {"handle": "D84F", "text": "6,600", "layer": "DIM", "x": 47990.0, "y": 33613.0, "rawRot": 45 * DEG, "rot": 45 * DEG},
]

VECTOR_HARNESS = """
var window = {shapes: %(shapes)s, __CBL_DIM_MTEXT_VECTORS_V91: %(vectors)s};
var state = {};
%(helpers)s
applyVectors();
process.stdout.write(JSON.stringify(window.shapes.map(function(s){ return [s.handle || null, s.rot]; })));
"""

ROTATION_HARNESS = """
console.log = function(){};
var window = {shapes: %(shapes)s};
var lastStatus = {};
function render(){}
%(helpers)s
applyRotationFromDxf(%(dxf)s);
process.stdout.write(JSON.stringify(window.shapes.map(function(s){ return [s.handle || null, s.rot]; })));
"""

BLOCK_DXF = "\n".join([
    "0", "SECTION", "2", "BLOCKS",
    "0", "BLOCK", "2", "FDN", "10", "0", "20", "0",
    "0", "TEXT", "8", "0", "10", "0", "20", "0", "40", "100", "1", "F1", "50", "0",
    "0", "ENDBLK",
    "0", "ENDSEC",
    "0", "SECTION", "2", "ENTITIES",
    "0", "INSERT", "8", "0", "2", "FDN", "10", "1000", "20", "0", "50", "90",
    "0", "ENDSEC", "0", "EOF", ""])


@skipUnless(NODE, "node is required to execute the CAD editor modules")
class CadDimMTextRotationEngineTests(SimpleTestCase):
    """CBL_DIM_MTEXT_ROTATION_ENGINE_V9_1 rotates a DIM MTEXT by its own direction only."""

    def apply(self, shapes, vectors):
        helpers = _module_functions(_html(), "CBL_DIM_MTEXT_ROTATION_ENGINE_V9_1_START", [
            "function num(v,d){", "function cleanMText(s){", "function textOf(s){", "function layerOf(s){",
            "function dist2(a,b,c,d){", "function applyVectors(){"])
        out = _run(VECTOR_HARNESS % {"shapes": json.dumps(shapes), "vectors": json.dumps(vectors), "helpers": helpers})
        return {h: rot for h, rot in out}

    def test_direction_goes_to_the_mtext_with_that_handle(self):
        rot = self.apply(DIM_SHAPES, DIM_VECTORS)
        self.assertAlmostEqual(rot["36016"], 335 * DEG)  # the model-space TEXT keeps its own rotation
        self.assertAlmostEqual(rot["D978"], -25 * DEG)
        self.assertAlmostEqual(rot["D84F"], 45 * DEG)

    def test_direction_without_its_shape_changes_nothing(self):
        vectors = [dict(DIM_VECTORS[1], handle="DEAD")]
        rot = self.apply(DIM_SHAPES, vectors)
        self.assertEqual(rot, {"36016": 335 * DEG, "D978": 0, "D84F": 0})

    def test_shapes_without_handles_still_match_by_text_and_distance(self):
        # Parse paths that give shapes no handle keep the old matching, but
        # only among those handle-less shapes.
        shapes = [dict(s, handle=None) for s in DIM_SHAPES[1:]]
        vectors = DIM_VECTORS
        out = _run(VECTOR_HARNESS % {"shapes": json.dumps(shapes), "vectors": json.dumps(vectors), "helpers": _module_functions(
            _html(), "CBL_DIM_MTEXT_ROTATION_ENGINE_V9_1_START", [
                "function num(v,d){", "function cleanMText(s){", "function textOf(s){", "function layerOf(s){",
                "function dist2(a,b,c,d){", "function applyVectors(){"])})
        self.assertEqual(sorted(round(r / DEG) for _, r in out), [-25, 45])


@skipUnless(NODE, "node is required to execute the CAD editor modules")
class CadBlockTextParentRotationTests(SimpleTestCase):
    """CBL_BLOCK_TEXT_PARENT_ROTATION_FIX_V7_1 rotates only texts that came out of that block."""

    def apply(self, shapes):
        helpers = _module_functions(_html(), "CBL_BLOCK_TEXT_PARENT_ROTATION_FIX_V7_1_START", [
            "function num(v,d){", "function first(p, code, d){", "function vals(p, code){", "function add(p, code, val){",
            "function cleanText(t){", "function decodeMplus(s){", "function readEntity(lines, i){",
            "function parseDxfTextBlocks(dxf){", "function worldPoint(ins, b, t){", "function dist2(a,b){",
            "function ntext(t){", "function applyRotationFromDxf(dxf){"])
        out = _run(ROTATION_HARNESS % {"shapes": json.dumps(shapes), "dxf": json.dumps(BLOCK_DXF), "helpers": helpers})
        return {h: rot for h, rot in out}

    def test_block_text_gets_the_insert_rotation(self):
        rot = self.apply([{"handle": "B1", "type": "text", "text": "F1", "x": 1000, "y": 0, "rot": 0, "cblBlockName": "FDN"}])
        self.assertAlmostEqual(rot["B1"], 90 * DEG)

    def test_model_space_text_with_the_same_content_is_left_alone(self):
        # A model-space TEXT "F1" next to the insert is not the block's text.
        rot = self.apply([{"handle": "36CB8", "type": "text", "text": "F1", "x": 1000, "y": 5, "rot": 0, "cblBlockName": ""}])
        self.assertEqual(rot["36CB8"], 0)

    def test_block_text_still_wins_over_a_nearer_model_space_text(self):
        rot = self.apply([
            {"handle": "36CB8", "type": "text", "text": "F1", "x": 1000, "y": 0, "rot": 0, "cblBlockName": ""},
            {"handle": "B1", "type": "text", "text": "F1", "x": 1000, "y": 40, "rot": 0, "cblBlockName": "FDN"},
        ])
        self.assertEqual(rot["36CB8"], 0)
        self.assertAlmostEqual(rot["B1"], 90 * DEG)


def _mtext(handle, x, y, deg):
    return ["0", "MTEXT", "5", handle, "8", "DIM", "10", str(x), "20", str(y), "1", "6,600",
            "11", repr(math.cos(deg * DEG)), "21", repr(math.sin(deg * DEG))]


DIM_DXF = "\n".join(["0", "SECTION", "2", "ENTITIES"] + _mtext("D978", 52000, 30000, -25)
                    + _mtext("D84F", 47990, 33613, 45) + ["0", "ENDSEC", "0", "EOF", ""])

DIRECTION_HARNESS = """
console.log = function(){};
var window = {shapes: %(shapes)s, render: function(){}};
var lastStatus = {};
%(helpers)s
applyDimMTextDirections(%(dxf)s);
process.stdout.write(JSON.stringify(window.shapes.map(function(s){ return [s.handle || null, s.rot]; })));
"""


@skipUnless(NODE, "node is required to execute the CAD editor modules")
class CadDimMTextDirectionFixTests(SimpleTestCase):
    """CBL_DIM_MTEXT_DIRECTION_VECTOR_FIX_V9 follows the same handle rule as V9_1."""

    def test_direction_goes_to_the_mtext_with_that_handle(self):
        helpers = _module_functions(_html(), "CBL_DIM_MTEXT_DIRECTION_VECTOR_FIX_V9_START", [
            "function n(v, d){", "function one(p, code, d){", "function arr(p, code){", "function add(p, code, value){",
            "function cleanMText(s){", "function textOf(s){", "function layerOf(s){", "function isDimTextString(t){",
            "function readEntity(lines, i){", "function parseDimMTextDirections(dxf){", "function dist2(a,b,c,d){",
            "function readableRot(rad){", "function applyDimMTextDirections(dxf){"])
        out = _run(DIRECTION_HARNESS % {"shapes": json.dumps(DIM_SHAPES), "dxf": json.dumps(DIM_DXF), "helpers": helpers})
        rot = {h: r for h, r in out}
        self.assertAlmostEqual(rot["36016"], 335 * DEG)
        self.assertAlmostEqual(rot["D978"], -25 * DEG)
        self.assertAlmostEqual(rot["D84F"], 45 * DEG)

