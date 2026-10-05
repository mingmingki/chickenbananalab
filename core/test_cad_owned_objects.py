import copy
import json
import subprocess
from unittest import skipUnless

from django.test import SimpleTestCase

from .test_cad_dwg_save_integrity import BUILD_OPS_HARNESS, NODE, _build_ops_source, _editor_function, _html


def _piece(owner, kind, piece_id, shape):
    shape = dict(shape)
    shape.update(ownerSourceHandle=owner, parentSourceHandle=owner, cblOwnerTypeV1=kind, cblPieceIdV1=piece_id,
                 displayOnly=True, rendererOnly=True, blockChild=True, synthetic=True, layId=1, rawLayerName="0",
                 cblRawAci=0, cblRawLineType=None)
    return shape


def _dimension(owner="9C", dx=0.0, dy=0.0):
    return [
        _piece(owner, "DIMENSION", 1, {"type": "line", "x1": 0 + dx, "y1": 0 + dy, "x2": 0 + dx, "y2": 50 + dy}),
        _piece(owner, "DIMENSION", 2, {"type": "line", "x1": 100 + dx, "y1": 0 + dy, "x2": 100 + dx, "y2": 50 + dy}),
        _piece(owner, "DIMENSION", 3, {"type": "text", "x": 40 + dx, "y": 55 + dy, "text": "100", "size": 10, "rot": 0}),
    ]


def _imported(handle, raw, shape):
    shape = dict(shape)
    shape.update(handle=handle, sourceHandle=handle, originalHandle=handle, rawDxfType=raw, layId=1,
                 rawLayerName="0", cblRawAci=256, cblRawLineType="ByLayer")
    return shape


def _moved(shape, dx, dy):
    shape = copy.deepcopy(shape)
    if shape["type"] == "polyline":
        for q in shape["pts"]:
            q["x"] += dx
            q["y"] += dy
    elif shape["type"] == "circle":
        shape["cx"] += dx
        shape["cy"] += dy
    return shape


@skipUnless(NODE, "node is required to execute the CAD save helpers")
class CadOwnedObjectSaveTests(SimpleTestCase):
    """Edits of objects the editor draws as pieces reach the DWG or are refused.

    Deleting or moving a dimension (or a hatch) changed only its render-only
    pieces, which the save ignored: the editor showed the edit, the saved DWG
    did not have it.  Moving a SPLINE, ELLIPSE, SOLID or POINT sent a polyline
    update the writer cannot apply and the whole save failed with a .NET error.
    """

    def run_cases(self, cases):
        html = _html()
        helpers = _editor_function(html, "function mvS(s,dx,dy){") + "\n" + _build_ops_source(html)
        run = subprocess.run([NODE, "-e", BUILD_OPS_HARNESS % {"helpers": helpers, "cases": json.dumps(cases)}],
                             capture_output=True, text=True, timeout=60)
        self.assertEqual(run.returncode, 0, run.stderr)
        return json.loads(run.stdout)

    def test_dimension_delete_move_and_refusals(self):
        base = _dimension()
        rotated = _dimension(dx=10)
        rotated[1].update(x1=110, y1=0, x2=60, y2=40)
        relabelled = _dimension()
        # Display modules recompute a dimension text's rotation and rewrite the
        # linetype of render-only pieces after the open; that is not an edit.
        relabelled[2]["rot"] = 0.5
        relabelled[0]["linetype"] = relabelled[0]["dash"] = "solid"
        out = self.run_cases({
            "same": {"base": base, "shapes": _dimension()},
            "display_only_changes": {"base": base, "shapes": relabelled},
            "deleted": {"base": base, "shapes": []},
            "moved": {"base": base, "shapes": _dimension(dx=10, dy=5)},
            "rotated": {"base": base, "shapes": rotated},
            "one_piece": {"base": base, "shapes": _dimension()[:2] + [_dimension(dx=30)[2]]},
            "copied": {"base": base, "shapes": _dimension() + _dimension(dx=200)},
        })
        self.assertEqual(out["same"], {"ops": []})
        self.assertEqual(out["display_only_changes"], {"ops": []})
        self.assertEqual(out["deleted"]["ops"], [{"type": "delete", "handle": "9C", "sourceHandle": "9C", "entity": "DIMENSION"}])
        self.assertEqual(out["moved"]["ops"], [{"type": "move", "handle": "9C", "sourceHandle": "9C", "entity": "DIMENSION", "delta": [10, 5, 0]}])
        for name in ("rotated", "one_piece", "copied"):
            self.assertIn("수정된 치수(dimension) 1개", out[name].get("error", ""), name)

    def test_hatch_pieces_move_together(self):
        def hatch(dx=0.0):
            return [_piece("5A", "HATCH", 1, {"type": "polyline", "closed": True, "pts": [{"x": 0 + dx, "y": 0}, {"x": 10 + dx, "y": 0}, {"x": 10 + dx, "y": 10}]}),
                    _piece("5A", "HATCH", 2, {"type": "polyline", "closed": True, "pts": [{"x": 2 + dx, "y": 2}, {"x": 4 + dx, "y": 2}, {"x": 4 + dx, "y": 4}]})]
        out = self.run_cases({"moved": {"base": hatch(), "shapes": hatch(dx=7)}, "deleted": {"base": hatch(), "shapes": []}})
        self.assertEqual(out["moved"]["ops"], [{"type": "move", "handle": "5A", "sourceHandle": "5A", "entity": "HATCH", "delta": [7, 0, 0]}])
        self.assertEqual(out["deleted"]["ops"][0]["type"], "delete")

    def test_move_only_kinds(self):
        spline = _imported("A1", "SPLINE", {"type": "polyline", "closed": False, "pts": [{"x": 0, "y": 0}, {"x": 5, "y": 8}, {"x": 10, "y": 0}]})
        point = _imported("B2", "POINT", {"type": "circle", "cx": 3, "cy": 4, "r": 1.5, "cblFromPoint": True})
        edited = copy.deepcopy(spline)
        edited["pts"][1]["y"] = 20
        out = self.run_cases({
            "moved": {"base": [spline, point], "shapes": [_moved(spline, 20, -5), _moved(point, 1, 1)]},
            "edited": {"base": [spline], "shapes": [edited]},
            "deleted": {"base": [spline], "shapes": []},
        })
        self.assertEqual(out["moved"]["ops"], [
            {"type": "move", "handle": "A1", "sourceHandle": "A1", "entity": "SPLINE", "delta": [20, -5, 0]},
            {"type": "move", "handle": "B2", "sourceHandle": "B2", "entity": "POINT", "delta": [1, 1, 0]},
        ])
        self.assertIn("수정된 스플라인(spline) 1개", out["edited"]["error"])
        self.assertEqual(out["deleted"]["ops"][0]["type"], "delete")


@skipUnless(NODE, "node is required to execute the CAD editor helpers")
class CadOwnedObjectSelectionTests(SimpleTestCase):
    def test_picking_a_piece_selects_the_whole_object(self):
        html = _html()
        helpers = "\n".join(_editor_function(html, sig) for sig in (
            "function cblSelectionTypeV1(s){", "function cblIsBlockChildV1(s){", "function cblBlockKeyV1(s){",
            "function cblBlockGroupsV1(){", "function cblExpandBlockSelectionV1(list,groups){",
            "function cblWindowSelectBlocksV1(list,crossing){"))
        pieces = _dimension() + [{"type": "line", "x1": 0, "y1": 0, "x2": 1, "y2": 1, "sourceHandle": "77"}]
        script = ("var shapes = %s;\n%s\nprocess.stdout.write(JSON.stringify({"
                  "pick: cblExpandBlockSelectionV1([shapes[1]]).length,"
                  "crossing: cblWindowSelectBlocksV1([shapes[0]], true).length,"
                  "window_partial: cblWindowSelectBlocksV1([shapes[0]], false).length,"
                  "window_all: cblWindowSelectBlocksV1(shapes.slice(0, 3), false).length,"
                  "plain: cblExpandBlockSelectionV1([shapes[3]]).length}));") % (json.dumps(pieces), helpers)
        run = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=60)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(json.loads(run.stdout), {"pick": 3, "crossing": 3, "window_partial": 0, "window_all": 3, "plain": 1})
