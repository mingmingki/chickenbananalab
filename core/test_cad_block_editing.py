import json
import math
import shutil
import subprocess
from unittest import skipUnless

from django.test import SimpleTestCase

from .test_cad_dwg_save_integrity import _BuildOpsRunner, _html, _line

NODE = shutil.which("node")

EDITOR_HARNESS = """
var shapes = %(shapes)s, sel = [], blocks = {}, vScale = 1, hint = '';
function saveH(){} function updUI(){} function render(){} function requestCadRenderV1(){} function setHint(m){ hint = m; }
function cloneJ(x){ return JSON.parse(JSON.stringify(x)); }
function getBB(s){ return {x: 0, y: 0, w: 0, h: 0}; }
%(helpers)s
process.stdout.write(JSON.stringify((function(){ %(body)s })()));
"""

BLOCK_SECTION = ("// ===== DWG BLOCK GROUPS =====", "function hitAll(wp){")


def _between(html, start, end):
    i = html.index(start)
    return html[i:html.index(end, i)]


def _function(html, signature):
    start = html.index(signature)
    return html[start:html.index("\nfunction ", start + len(signature))]


def _insert(handle="1549F", x=0.0, **extra):
    shape = {"type": "blockref", "layId": 1, "rawLayerName": "0", "name": "FDN", "blockName": "FDN",
             "x": x, "y": 0.0, "rotation": 0.0, "scaleX": 1, "scaleY": 1, "scaleZ": 1, "scale": 1,
             "rawDxfType": "INSERT", "handle": handle, "sourceHandle": handle, "originalHandle": handle}
    shape.update(extra)
    return shape


def _child(owner="1549F", kind="line", dx=0.0, **extra):
    shape = {"layId": 1, "rawLayerName": "0", "rawDxfType": kind.upper(), "blockChild": True, "isBlockChild": True,
             "fromBlock": True, "rendererOnly": True, "displayOnly": True, "synthetic": True,
             "parentBlockName": "FDN", "parentInsertName": "FDN", "blockName": "FDN",
             "ownerSourceHandle": owner, "parentSourceHandle": owner}
    if kind == "line":
        shape.update(type="line", x1=dx, y1=0.0, x2=dx + 10.0, y2=0.0, sourceHandle="1576E",
                     sourcePath=owner + "/1576E")
    else:
        shape.update(type="text", x=dx, y=5.0, text="F1", size=3, rot=0, sourceHandle="1576D",
                     sourcePath=owner + "/1576D")
    shape.update(extra)
    return shape


def _attrib(owner="34", dx=0.0, **extra):
    shape = {"type": "text", "layId": 1, "rawLayerName": "0", "rawDxfType": "ATTRIB", "x": 100.0 + dx, "y": 85.0,
             "text": "ROOM-1", "size": 2.5, "rot": 0, "handle": "36", "sourceHandle": "36", "originalHandle": "36",
             "cblAttribOwnerHandle": owner}
    shape.update(extra)
    return shape


@skipUnless(NODE, "node is required to execute the CAD editor helpers")
class CadBlockGroupEditorTests(SimpleTestCase):
    """Imported blocks behave as one object: select, copy, explode, rotate and mirror as a unit."""

    def run_editor(self, shapes, body, extra=()):
        html = _html()
        helpers = [_function(html, "function cblSelectionTypeV1(s){"), _between(html, *BLOCK_SECTION)]
        helpers += [_function(html, sig) for sig in extra]
        script = EDITOR_HARNESS % {"shapes": json.dumps(shapes), "helpers": "\n".join(helpers), "body": body}
        run = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=60)
        self.assertEqual(run.returncode, 0, run.stderr)
        return json.loads(run.stdout)

    def test_clicking_any_part_selects_the_whole_block_with_its_attributes(self):
        shapes = [_line("2B"), _insert("34"), _child("34"), _child("34", kind="text"), _attrib("34")]
        result = self.run_editor(shapes, "return cblExpandBlockSelectionV1([shapes[3]]).map(function(s){return shapes.indexOf(s);});")
        self.assertEqual(result, [1, 2, 3, 4])
        self.assertEqual(self.run_editor(shapes, "return cblExpandBlockSelectionV1([shapes[0]]).length;"), 1)

    def test_window_selection_needs_the_whole_block_but_crossing_takes_any_part(self):
        shapes = [_insert("34"), _child("34"), _child("34", kind="text")]
        body = "return [cblWindowSelectBlocksV1([shapes[1]], false).length, cblWindowSelectBlocksV1([shapes[1]], true).length, cblWindowSelectBlocksV1([shapes[1], shapes[2]], false).length];"
        self.assertEqual(self.run_editor(shapes, body), [0, 3, 3])

    def test_copies_of_a_block_get_their_own_group(self):
        shapes = [_insert("34"), _child("34"), _line("2B")]
        result = self.run_editor(shapes, """
            var copies = cblRekeyCopiedBlocksV1(shapes.map(cloneJ)); shapes = shapes.concat(copies);
            return {keys: copies.map(cblBlockKeyV1), original: cblExpandBlockSelectionV1([shapes[1]]).length,
                    copy: cblExpandBlockSelectionV1([copies[1]]).map(function(s){return shapes.indexOf(s);})};
        """)
        self.assertEqual(result["keys"][0], result["keys"][1])
        self.assertTrue(result["keys"][0].startswith("copy-"))
        self.assertEqual(result["keys"][2], "")
        self.assertEqual(result["original"], 2)
        self.assertEqual(result["copy"], [3, 4])

    def test_explode_keeps_contents_as_standalone_entities(self):
        attdef = _child("34", kind="text", rawDxfType="ATTDEF", sourceHandle="33", sourcePath="34/33")
        shapes = [_insert("34"), _child("34"), attdef, _attrib("34")]
        result = self.run_editor(shapes, """
            sel = cblExpandBlockSelectionV1([shapes[0]]); explode();
            return shapes.map(function(s){return {type: s.type, keys: Object.keys(s).filter(function(k){
              return /Handle|blockChild|rendererOnly|displayOnly|synthetic|sourcePath|cblAttrib|parent|owner/.test(k);})};});
        """, extra=["function explode(){"])
        self.assertEqual([s["type"] for s in result], ["line", "text"])
        self.assertEqual([s["keys"] for s in result], [[], []])

    def test_rotating_a_block_turns_the_insert_text_and_arcs(self):
        shapes = [_insert("34", x=10.0), {"type": "text", "x": 10.0, "y": 0.0, "text": "A", "rot": 0},
                  {"type": "arc", "cx": 10.0, "cy": 0.0, "r": 1, "a1": 0, "a2": math.pi / 2}]
        result = self.run_editor(shapes, """
            sel = shapes.slice(); rotateSelected(90);
            return [shapes[0].x, shapes[0].y, shapes[0].rotation, shapes[1].x, shapes[1].y, shapes[1].rot, shapes[1].rotation, shapes[2].a1, shapes[2].a2];
        """, extra=["function rotateSelected(angle){", "function cblTextRotationRadRenderFixV5(s){"])
        self.assertEqual([round(v, 6) for v in result],
                         [0, 10, round(math.pi / 2, 6), 0, 10, round(math.pi / 2, 6), round(math.pi / 2, 6),
                          round(math.pi / 2, 6), round(math.pi, 6)])

    def test_mirroring_a_block_reflects_its_insert(self):
        result = self.run_editor([_insert("34", x=10.0, rotation=0.5)], """
            var m = applyMirror([shapes[0]], {x: 0, y: 0}, {x: 0, y: 10})[0];
            return [m.x, m.rotation, m.scaleX, m.scaleY];
        """, extra=["function applyMirror(shps,pt1,pt2){"])
        self.assertEqual([round(v, 6) for v in result], [-10, round(math.pi - 0.5, 6), 1, -1])


@skipUnless(NODE, "node is required to execute the CAD save helpers")
class CadBlockGroupSaveTests(_BuildOpsRunner, SimpleTestCase):
    def test_moving_a_block_updates_only_the_insert(self):
        base = [_insert("34"), _child("34"), _attrib("34")]
        moved = [_insert("34", x=50.0), _child("34", dx=50.0), _attrib("34", dx=50.0)]
        result = self.run_cases({"m": {"base": base, "shapes": moved}})["m"]
        self.assertEqual(self.kinds(result), [("update", "34")])

    def test_editing_an_attribute_alone_is_refused(self):
        result = self.run_cases({"a": {"base": [_insert("34"), _attrib("34")],
                                       "shapes": [_insert("34"), _attrib("34", text="ROOM-2")]}})["a"]
        self.assertIn("블록 속성", result["error"])

    def test_copied_block_adds_one_insert_and_no_attribute_text(self):
        base = [_insert("34"), _child("34"), _attrib("34")]
        copy = [_insert("34", x=50.0, cblBlockKey="copy-1"), _child("34", dx=50.0, cblBlockKey="copy-1"),
                _attrib("34", dx=50.0, cblBlockKey="copy-1")]
        result = self.run_cases({"c": {"base": base, "shapes": base + copy}})["c"]
        self.assertEqual(self.kinds(result), [("add_insert", None)])
        self.assertEqual(result["ops"][0]["copyOf"], "34")

    def test_exploded_block_deletes_the_insert_and_adds_its_contents(self):
        base = [_insert("34"), _child("34"), _attrib("34")]
        standalone_line = {"type": "line", "layId": 1, "rawLayerName": "0", "x1": 0.0, "y1": 0.0, "x2": 10.0, "y2": 0.0}
        standalone_text = {"type": "text", "layId": 1, "rawLayerName": "0", "x": 100.0, "y": 85.0, "text": "ROOM-1",
                           "size": 2.5, "rot": 0}
        result = self.run_cases({"x": {"base": base, "shapes": [standalone_line, standalone_text]}})["x"]
        self.assertEqual(self.kinds(result), [("delete", "34"), ("add_line", None), ("add_text", None)])


@skipUnless(NODE, "node is required to execute the CAD save helpers")
class CadAttributeBlockTransformTests(_BuildOpsRunner, SimpleTestCase):
    """The writer cannot yet place attributes under rotation/scale; say so before sending anything."""

    def test_rotating_a_block_with_attributes_is_refused_in_korean(self):
        result = self.run_cases({"r": {"base": [_insert("34"), _attrib("34")],
                                       "shapes": [_insert("34", rotation=1.0), _attrib("34", rot=1.0, rotation=1.0)]}})["r"]
        self.assertIn("속성", result["error"])
        self.assertIn("회전", result["error"])

    def test_mirrored_copy_of_a_block_with_attributes_is_refused(self):
        base = [_insert("34"), _attrib("34")]
        copy = [_insert("34", x=50.0, scaleY=-1, cblBlockKey="copy-1"), _attrib("34", dx=50.0, cblBlockKey="copy-1")]
        result = self.run_cases({"m": {"base": base, "shapes": base + copy}})["m"]
        self.assertIn("속성", result["error"])

    def test_moving_a_block_with_attributes_is_still_saved(self):
        result = self.run_cases({"t": {"base": [_insert("34"), _attrib("34")],
                                       "shapes": [_insert("34", x=5.0), _attrib("34", dx=5.0)]}})["t"]
        self.assertEqual(self.kinds(result), [("update", "34")])
