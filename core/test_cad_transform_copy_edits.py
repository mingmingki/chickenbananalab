import json
import math
import subprocess
from unittest import skipUnless

from django.test import SimpleTestCase

from .test_cad_dwg_save_integrity import NODE, _build_ops_source, _editor_function, _html, _output_handles_source
from .test_cad_owned_objects import _dimension, _imported, _piece

# The editor's own rotate / scale / mirror / copy commands produce the edited
# shapes, so these tests follow what a user does.  getBB is stubbed to put the
# rotation and scaling centre at the origin.
HARNESS = """
const window = {layers: [{id: 1, name: '0'}], CBL_ACADSHARP_FULL_DXF_ACTIVE: true,
                CBL_FREE_DWG_ORIGINAL_LAYER_NAMES: ['0'], CBL_CAD_TEXT_STYLES_V1: {}};
var sel = [], cblBlockCopySeqV1 = 0;
function getBB(s) { return {x: 0, y: 0, w: 0, h: 0}; }
function saveH() {}
function requestCadRenderV1() {}
function setHint() {}
var prompt = function () { return '2'; };
%(helpers)s
const base = %(base)s;
const clone = (list) => JSON.parse(JSON.stringify(list));
const copyOf = (list, dx, dy) => cblRekeyCopiedBlocksV1(list.map((s) => { const c = cloneJ(s); mvS(c, dx, dy); return c; }));
const mirrorX = (list, x) => cblRekeyCopiedBlocksV1(applyMirror(list, {x: x, y: 0}, {x: x, y: 10}));
// The block groups of cblBlockGroupsV1, over a given list (the editor reads its global shapes).
const groupsOf = (all) => { const g = {}; all.forEach((s) => { const k = cblBlockKeyV1(s); if (!k) return; const x = g[k] || (g[k] = {owner: null, members: []}); x.members.push(s); if (!cblIsBlockChildV1(s) && cblSelectionTypeV1(s) === 'blockref') x.owner = s; }); return g; };
const expand = (all, s) => cblExpandBlockSelectionV1([s], groupsOf(all));
const pick = (list, handle) => list.filter((s) => (s.ownerSourceHandle || s.sourceHandle) === handle);
const out = {};
function run(name, shapes) {
  window.shapes = shapes;
  window.CBL_FREE_DWG_ORIGINAL_SHAPES = base;
  try { const r = buildOps(); out[name] = {ops: r.ops, pending: r.pendingAddRefs.map((p) => p.opIndex)}; }
  catch (e) { out[name] = {error: String(e && e.message || e)}; }
}
(async () => {
%(body)s
process.stdout.write(JSON.stringify(out));
})().catch((e) => { process.stderr.write(String(e && e.stack || e)); process.exit(1); });
"""

EDITOR_COMMANDS = ("function mvS(s,dx,dy){", "function cloneJ(x){", "function cblTextRotationRadRenderFixV5(s){",
                   "function cblSetTextRotationV1(s,rad){", "function applyMirror(shps,pt1,pt2){",
                   "function cblRectMapV1(s,f,keepRect){", "function rotateSelected(angle){", "function scaleDialog(){",
                   "function cblSelectionTypeV1(s){", "function cblIsBlockChildV1(s){", "function cblBlockKeyV1(s){",
                   "function cblRekeyCopiedBlocksV1(copies){", "function cblExpandBlockSelectionV1(list,groups){",
                   "function cblScaleDimTextV1(text,f){")


def _hatch(owner="5A"):
    return [_piece(owner, "HATCH", 1, {"type": "polyline", "closed": True, "pts": [{"x": 0, "y": 0}, {"x": 10, "y": 0}, {"x": 10, "y": 10}]}),
            _piece(owner, "HATCH", 2, {"type": "polyline", "closed": True, "pts": [{"x": 2, "y": 2}, {"x": 4, "y": 2}, {"x": 4, "y": 4}]})]


def _mleader(owner="B1"):
    return [_piece(owner, "MULTILEADER", 1, {"type": "polyline", "closed": False, "pts": [{"x": 0, "y": 0}, {"x": 20, "y": 20}, {"x": 40, "y": 20}]}),
            _piece(owner, "MULTILEADER", 2, {"type": "text", "x": 42, "y": 18, "text": "NOTE", "size": 5, "rot": 0, "tw": 20})]


SPLINE = _imported("A1", "SPLINE", {"type": "polyline", "closed": False, "pts": [{"x": 0, "y": 0}, {"x": 5, "y": 8}, {"x": 10, "y": 0}]})
POINT = _imported("B2", "POINT", {"type": "circle", "cx": 3, "cy": 4, "r": 1.5, "cblFromPoint": True})
BLOCK = {"type": "blockref", "name": "DOOR", "x": 50, "y": 50, "rotation": 0, "scaleX": 1, "scaleY": 1, "layId": 1,
         "rawLayerName": "0", "handle": "C1", "sourceHandle": "C1", "originalHandle": "C1", "rawDxfType": "INSERT",
         "cblRawAci": 256, "cblRawLineType": "ByLayer"}
BASE = _dimension() + _hatch() + _mleader() + [SPLINE, POINT, BLOCK]


@skipUnless(NODE, "node is required to execute the CAD editor helpers")
class CadTransformCopyEditTests(SimpleTestCase):
    """Rotating, scaling, mirroring and copying objects the editor cannot rebuild.

    A rotated or scaled dimension, hatch, leader, spline or ellipse used to stop
    the save ("수정된 치수"), a copied hatch or spline was saved as a polyline
    (the hatch lost its fill) and a copied dimension stopped the save ("새 치수").
    The save now sends the similarity (a "transform" or an "add_copy" of the
    source), which the writer applies to the DWG object itself.
    """

    def run_body(self, body, with_handles=False, base=None):
        html = _html()
        helpers = "\n".join(_editor_function(html, sig) for sig in EDITOR_COMMANDS) + "\n" + _build_ops_source(html)
        if with_handles:
            helpers += _output_handles_source(html)
        script = HARNESS % {"helpers": helpers, "base": json.dumps(BASE if base is None else base), "body": body}
        run = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=60)
        self.assertEqual(run.returncode, 0, run.stderr)
        return json.loads(run.stdout)

    def assertMatrix(self, got, expected):
        self.assertEqual(len(got), 6)
        for g, e in zip(got, expected):
            self.assertAlmostEqual(g, e, places=6, msg=(got, expected))

    def only_op(self, result):
        self.assertNotIn("error", result)
        self.assertEqual(len(result["ops"]), 1, result["ops"])
        return result["ops"][0]

    def test_rotating_scaling_and_mirroring_owned_objects(self):
        out = self.run_body("""
          var s = clone(base); sel = pick(s, '9C'); rotateSelected(90); run('dimension_rotated', s);
          s = clone(base); sel = pick(s, '9C'); scaleDialog(); run('dimension_scaled', s);
          s = clone(base); pick(s, '9C')[2].cblDimScalableV1 = true; base[2].cblDimScalableV1 = true; sel = pick(s, '9C'); scaleDialog(); run('dimension_scaled_number', s); out.scaledText = pick(s, '9C')[2].text;
          s = clone(base); sel = pick(s, '9C'); scaleDialog(); pick(s, '9C')[2].text = '999'; run('dimension_scaled_text_edited', s); delete base[2].cblDimScalableV1;
          s = clone(base); sel = pick(s, '5A'); scaleDialog(); run('hatch_scaled', s);
          s = clone(base); sel = pick(s, 'B1'); rotateSelected(45); run('mleader_rotated', s);
          s = clone(base); s.splice(s.indexOf(pick(s, '5A')[0]), 1, ...applyMirror(pick(s, '5A').slice(0, 1), {x: 0, y: 0}, {x: 0, y: 10})); run('hatch_one_piece_mirrored', s);
        """)
        op = self.only_op(out["dimension_rotated"])
        self.assertEqual({k: op[k] for k in ("type", "handle", "entity")}, {"type": "transform", "handle": "9C", "entity": "DIMENSION"})
        self.assertMatrix(op["matrix"], [0, 1, -1, 0, 0, 0])
        # A scaled dimension is saved; its number becomes the new length (the
        # editor shows it at once, the writer writes the same in the DWG).
        op = self.only_op(out["dimension_scaled"])
        self.assertEqual((op["type"], op["handle"]), ("transform", "9C"))
        self.assertMatrix(op["matrix"], [2, 0, 0, 2, 0, 0])
        self.assertEqual(out["scaledText"], "200")
        self.assertMatrix(self.only_op(out["dimension_scaled_number"])["matrix"], [2, 0, 0, 2, 0, 0])
        self.assertIn("수정된 치수(dimension) 1개", out["dimension_scaled_text_edited"]["error"])
        op = self.only_op(out["hatch_scaled"])
        self.assertEqual((op["type"], op["handle"], op["entity"]), ("transform", "5A", "HATCH"))
        self.assertMatrix(op["matrix"], [2, 0, 0, 2, 0, 0])
        op = self.only_op(out["mleader_rotated"])
        c = math.cos(math.pi / 4)
        self.assertEqual((op["type"], op["entity"]), ("transform", "MULTILEADER"))
        self.assertMatrix(op["matrix"], [c, c, -c, c, 0, 0])
        self.assertIn("수정된 해치(hatch) 1개", out["hatch_one_piece_mirrored"]["error"])

    def test_copies_of_owned_objects_keep_their_kind(self):
        out = self.run_body("""
          var s = clone(base); s.push(...copyOf(pick(s, '9C'), 200, 0)); run('dimension_copied', s);
          s = clone(base); var c = copyOf(pick(s, '9C'), 0, 0); s.push(...c); sel = c; rotateSelected(90); run('dimension_copy_rotated', s);
          s = clone(base); s.push(...mirrorX(pick(s, '5A'), 300)); run('hatch_mirrored_copy', s);
          s = clone(base); s.push(...mirrorX(pick(s, '9C'), 300)); run('dimension_mirrored_copy', s);
          s = clone(base); s.push(...mirrorX(pick(s, 'B1'), 300)); run('mleader_mirrored_copy', s);
          s = clone(base); c = copyOf(pick(s, '5A'), 50, 0); c[0].pts[0].x += 3; s.push(...c); run('hatch_copy_edited', s);
          s = clone(base); s.push(...copyOf(pick(s, '9C'), 200, 0)); s = s.filter((x) => x.ownerSourceHandle !== '9C' || x.cblBlockKey); run('original_deleted', s);
        """)
        op = self.only_op(out["dimension_copied"])
        self.assertEqual({k: op[k] for k in ("type", "copyOf", "entity")}, {"type": "add_copy", "copyOf": "9C", "entity": "DIMENSION"})
        self.assertMatrix(op["matrix"], [1, 0, 0, 1, 200, 0])
        self.assertTrue(op["clientShapeId"].startswith("copy-"))
        self.assertEqual(out["dimension_copied"]["pending"], [0])
        self.assertMatrix(self.only_op(out["dimension_copy_rotated"])["matrix"], [0, 1, -1, 0, 0, 0])
        op = self.only_op(out["hatch_mirrored_copy"])
        self.assertEqual((op["type"], op["copyOf"], op["entity"]), ("add_copy", "5A", "HATCH"))
        self.assertMatrix(op["matrix"], [-1, 0, 0, 1, 600, 0])
        # The mirror command keeps text readable; the writer cannot mirror these.
        self.assertIn("대칭한 치수(dimension) 1개", out["dimension_mirrored_copy"]["error"])
        self.assertIn("대칭한 다중 지시선(multileader) 1개", out["mleader_mirrored_copy"]["error"])
        self.assertIn("새 해치(hatch) 1개", out["hatch_copy_edited"]["error"])
        # The copy is cloned before its source is deleted.
        self.assertEqual([(o["type"], o.get("copyOf") or o.get("handle")) for o in out["original_deleted"]["ops"]],
                         [("add_copy", "9C"), ("delete", "9C")])

    def test_spline_ellipse_solid_and_point_edits(self):
        out = self.run_body("""
          var s = clone(base); sel = pick(s, 'A1'); rotateSelected(30); run('spline_rotated', s);
          s = clone(base); sel = pick(s, 'B2'); scaleDialog(); run('point_scaled', s);
          s = clone(base); s.push(...mirrorX(pick(s, 'A1'), 100)); run('spline_mirrored_copy', s);
          s = clone(base); var c = copyOf(pick(s, 'A1'), 20, -20); c[0].pts[1].y = 30; s.push(...c); run('spline_copy_edited', s);
          s = clone(base); s.push(...copyOf(pick(s, 'A1'), 20, -20)); mvS(pick(s, 'A1')[0], 5, 0); run('spline_copy_then_original_moved', s);
          s = clone(base); s.push(...copyOf(pick(s, 'C1'), 20, -20)); mvS(pick(s, 'C1')[0], 5, 0); run('block_copy_then_original_moved', s);
        """)
        op = self.only_op(out["spline_rotated"])
        c, d = math.cos(math.pi / 6), math.sin(math.pi / 6)
        self.assertEqual((op["type"], op["handle"], op["entity"]), ("transform", "A1", "SPLINE"))
        self.assertMatrix(op["matrix"], [c, d, -d, c, 0, 0])
        # A POINT has no size in the DWG: scaling only moves it.
        self.assertEqual(self.only_op(out["point_scaled"]), {"type": "move", "handle": "B2", "sourceHandle": "B2", "entity": "POINT", "delta": [3, 4, 0]})
        op = self.only_op(out["spline_mirrored_copy"])
        self.assertEqual((op["type"], op["copyOf"], op["entity"]), ("add_copy", "A1", "SPLINE"))
        self.assertMatrix(op["matrix"], [-1, 0, 0, 1, 200, 0])
        self.assertEqual(out["spline_mirrored_copy"]["pending"], [0])
        self.assertIn("새 스플라인(spline) 1개", out["spline_copy_edited"]["error"])
        ops = out["spline_copy_then_original_moved"]["ops"]
        self.assertEqual([o["type"] for o in ops], ["add_copy", "move"])
        self.assertMatrix(ops[0]["matrix"], [1, 0, 0, 1, 20, -20])
        self.assertEqual(ops[1]["delta"], [5, 0, 0])
        ops = out["block_copy_then_original_moved"]["ops"]
        self.assertEqual(ops[0]["type"], "add_insert")
        self.assertEqual(ops[0]["copyOf"], "C1")
        self.assertTrue(all(o.get("handle") == "C1" for o in ops[1:]) and len(ops) > 1, ops)

    def test_saved_copies_belong_to_their_new_object(self):
        out = self.run_body("""
          var s = clone(base), c = copyOf(pick(s, '9C'), 200, 0), sp = copyOf(pick(s, 'A1'), 0, 50);
          s.push(...c, ...sp);
          window.shapes = s; window.CBL_FREE_DWG_ORIGINAL_SHAPES = base;
          var pack = buildOps();
          out.types = pack.ops.map((o) => o.type + ':' + o.copyOf);
          var handles = {}; pack.pendingAddRefs.forEach((r, i) => { handles[String(r.opIndex)] = ['1F0', '1F1'][i]; });
          await applyOutputHandlesV1(JSON.stringify(handles), pack);
          out.owners = c.map((p) => [p.ownerSourceHandle, p.parentSourceHandle, p.cblBlockKey || null]);
          out.spline = [sp[0].sourceHandle, sp[0].handle];
          // The next save sees the copies as saved objects: nothing to send.
          window.CBL_FREE_DWG_ORIGINAL_SHAPES = JSON.parse(JSON.stringify(pack.mappedShapes));
          out.second = buildOps().ops;
          pack.restoreOutputHandlesV1();
          out.restored = c.map((p) => [p.ownerSourceHandle, String(p.cblBlockKey).slice(0, 5)]);
        """, with_handles=True)
        self.assertEqual(sorted(out["types"]), ["add_copy:9C", "add_copy:A1"])
        handle_of_dimension = "1F0" if out["types"][0] == "add_copy:9C" else "1F1"
        self.assertEqual(out["owners"], [[handle_of_dimension, handle_of_dimension, None]] * 3)
        self.assertEqual(out["spline"][0], out["spline"][1])
        self.assertIn(out["spline"][0], ("1F0", "1F1"))
        self.assertEqual(out["second"], [])
        self.assertEqual(out["restored"], [["9C", "copy-"]] * 3)


def _attrib(owner, handle, tag, x, y, size=5.0, **extra):
    shape = {"type": "text", "text": tag + "-1", "x": x, "y": y, "size": size, "rot": 0, "rotation": 0, "tw": 20,
             "layId": 1, "rawLayerName": "0", "cblRawAci": 256, "cblRawLineType": "ByLayer", "rawDxfType": "ATTRIB",
             "handle": handle, "sourceHandle": handle, "originalHandle": handle, "cblAttribOwnerHandle": owner, "cblAttribTagV1": tag}
    shape.update(extra)
    return shape


ATTRIB_BASE = [dict(BLOCK, x=0, y=0), _attrib("C1", "A7", "NO", -15, -15), _attrib("C1", "A8", "NAME", -12, 12, size=4.0)]


@skipUnless(NODE, "node is required to execute the CAD editor helpers")
class CadAttribBlockEditTests(SimpleTestCase):
    """A block with attributes can be rotated, scaled, mirrored and copied.

    The save stopped with "회전·크기를 바꾼 속성": the writer only moved
    attributes.  It now sends where the editor shows each attribute.
    """

    run_body = CadTransformCopyEditTests.run_body
    only_op = CadTransformCopyEditTests.only_op

    def test_rotating_scaling_and_mirroring_place_the_attributes(self):
        out = self.run_body("""
          var s = clone(base); sel = expand(s, s[0]); rotateSelected(90); run('rotated', s);
          s = clone(base); sel = expand(s, s[0]); scaleDialog(); run('scaled', s);
          s = clone(base); mvS(s[0], 10, 0); mvS(s[1], 10, 0); mvS(s[2], 10, 0); run('moved', s);
          s = clone(base); sel = expand(s, s[0]); rotateSelected(90); s.splice(2, 1); run('attribute_deleted', s);
        """, base=ATTRIB_BASE)
        op = self.only_op(out["rotated"])
        self.assertEqual((op["type"], op["handle"], op["entity"]), ("update", "C1", "INSERT"))
        self.assertAlmostEqual(op["rotation"], math.pi / 2)
        placed = {a["tag"]: a for a in op["attributes"]}
        self.assertEqual(sorted(placed), ["NAME", "NO"])
        self.assertEqual((placed["NO"]["handle"], placed["NO"]["sourceHandle"]), ("A7", "A7"))
        self.assertEqual([round(v, 6) for v in placed["NO"]["insert"]], [15, -15, 0])
        self.assertAlmostEqual(placed["NO"]["rotation"], math.pi / 2)
        self.assertEqual(placed["NO"]["height"], 5)
        placed = {a["tag"]: a for a in self.only_op(out["scaled"])["attributes"]}
        self.assertEqual([round(v, 6) for v in placed["NAME"]["insert"]], [-24, 24, 0])
        self.assertEqual(placed["NAME"]["height"], 8)
        # A plain move keeps the writer moving the attributes with the block.
        self.assertNotIn("attributes", self.only_op(out["moved"]))
        self.assertIn("삭제된 블록 속성", out["attribute_deleted"]["error"])

    def test_copies_of_a_block_with_attributes(self):
        out = self.run_body("""
          var s = clone(base), c = copyOf(expand(s, s[0]), 20, -20); s.push(...c); sel = c; rotateSelected(90); run('copy_rotated', s);
          s = clone(base); s.push(...mirrorX(expand(s, s[0]), 100)); run('copy_mirrored', s);
          s = clone(base); s.push(...copyOf(expand(s, s[0]), 20, -20)); run('copy_moved', s);
        """, base=ATTRIB_BASE)
        op = self.only_op(out["copy_rotated"])
        self.assertEqual((op["type"], op["copyOf"]), ("add_insert", "C1"))
        placed = {a["tag"]: a for a in op["attributes"]}
        self.assertEqual((placed["NO"]["handle"], placed["NO"]["sourceHandle"]), ("", "A7"))
        self.assertAlmostEqual(placed["NO"]["rotation"], math.pi / 2)
        op = self.only_op(out["copy_mirrored"])
        self.assertEqual(op["scale"][1], -1)
        placed = {a["tag"]: a for a in op["attributes"]}
        # The mirror command keeps text readable: same direction, mirrored place.
        self.assertEqual(placed["NO"]["rotation"], 0)
        self.assertEqual(placed["NAME"]["sourceHandle"], "A8")
        self.assertNotIn("attributes", self.only_op(out["copy_moved"]))


@skipUnless(NODE, "node is required to execute the CAD editor helpers")
class CadRestoreEditTests(SimpleTestCase):
    """Undo after a save that deleted an object: it comes back as its own kind.

    The object is no longer in the DWG, so the save clones it from the
    drawing as it was opened ("fromOpened"); before, a spline came back as a
    polyline and a dimension or a block with attributes stopped the save.
    """

    run_body = CadTransformCopyEditTests.run_body
    assertMatrix = CadTransformCopyEditTests.assertMatrix

    def test_restored_objects_are_cloned_from_the_opened_drawing(self):
        opened = BASE + ATTRIB_BASE[1:]
        committed = [s for s in opened if (s.get("ownerSourceHandle") or s.get("sourceHandle")) not in ("9C", "A1", "C1")
                     and s.get("cblAttribOwnerHandle") != "C1"]
        out = self.run_body("""
          function restore(name, live, openedList) {
            window.shapes = live; window.CBL_FREE_DWG_ORIGINAL_SHAPES = committed; window.CBL_FREE_DWG_OPENED_SHAPES_V1 = openedList;
            try { const r = buildOps(); out[name] = {ops: r.ops, pending: r.pendingAddRefs.map((p) => p.opIndex)}; }
            catch (e) { out[name] = {error: String(e && e.message || e)}; }
          }
          var committed = %s, opened = %s;
          var live = clone(opened); mvS(pick(live, 'A1')[0], 5, 0);
          restore('restored', live, opened);
          restore('without_opened', clone(opened), null);
        """ % (json.dumps(committed), json.dumps(opened)), base=committed)
        ops = {(o["type"], o.get("copyOf")): o for o in out["restored"]["ops"]}
        self.assertEqual(sorted(ops), [("add_copy", "9C"), ("add_copy", "A1"), ("add_insert", "C1")])
        self.assertTrue(all(o["fromOpened"] for o in ops.values()))
        self.assertMatrix(ops[("add_copy", "A1")]["matrix"], [1, 0, 0, 1, 5, 0])
        self.assertEqual(ops[("add_copy", "9C")]["entity"], "DIMENSION")
        self.assertEqual(len(out["restored"]["pending"]), 3)
        # Without the opened drawing (an older session) the old refusal stays.
        self.assertIn("새 치수(dimension) 1개", out["without_opened"]["error"])
