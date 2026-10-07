import json
import math
import shutil
import subprocess
from pathlib import Path
from unittest import skipUnless

from django.conf import settings
from django.test import SimpleTestCase

CAD_HTML = Path(settings.BASE_DIR) / "core" / "static" / "core" / "tools" / "CBLCAD_VER2.html"
NODE = shutil.which("node")

HARNESS = """
%(helpers)s
const cases = %(cases)s;
const out = {};
for (const [name, c] of Object.entries(cases)) {
  const m = c.normal ? cblOcsMatrixV1(c.normal[0], c.normal[1], c.normal[2], c.elevation || 0) : c.matrix;
  out[name] = {
    matrix: m ? Array.from(m) : null, tilted: !!(m && m.cblTilted),
    arc: c.arc ? cblArcThroughMatrixV1(c.arc[0], c.arc[1], c.arc[2], c.arc[3], c.arc[4], m) : null,
    points: c.points ? cblArcPointsThroughMatrixV1(c.points[0], c.points[1], c.points[2], c.points[3], c.points[4], m, !!c.full) : null,
    text: c.text !== undefined ? cblTextRotationThroughMatrixV1(c.text, m) : null,
  };
}
process.stdout.write(JSON.stringify(out));
"""


def _ocs_helpers():
    """The OCS import helpers exactly as shipped in the CAD page."""
    html = CAD_HTML.read_text(encoding="utf-8")
    start = html.index("/* CBL_OCS_IMPORT_V1_START")
    return html[start:html.index("/* CBL_OCS_IMPORT_V1_END */", start)]


def _arc_points(arc, n=192):
    span = (arc["a2"] - arc["a1"]) % (2 * math.pi) or 2 * math.pi
    return [(arc["cx"] + arc["r"] * math.cos(arc["a1"] + span * i / n), arc["cy"] + arc["r"] * math.sin(arc["a1"] + span * i / n))
            for i in range(n + 1)]


def _world(entity):
    """World plan points along an ezdxf entity (through a block reference too)."""
    from ezdxf import path

    if entity.dxftype() == "INSERT":
        return [p for child in entity.virtual_entities() for p in _world(child)]
    return [(v.x, v.y) for v in path.make_path(entity).flattening(0.01, segments=64)]


@skipUnless(NODE, "node is required to execute the CAD import helpers")
class CadOcsImportTests(SimpleTestCase):
    """The editor draws objects kept in their own coordinates (OCS) where AutoCAD does.

    An arc whose normal points down (AutoCAD's 3D mirror) was drawn with x the
    other way (사무동1층.dwg ARC 88BB at x -11019 instead of 11019); an arc in
    a block mirrored with x scale -1 was drawn turned half round instead of
    mirrored (a door's swing on the wrong side); a tilted arc was drawn flat.
    """

    def run_cases(self, cases):
        script = HARNESS % {"helpers": _ocs_helpers(), "cases": json.dumps(cases)}
        run = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=60)
        self.assertEqual(run.returncode, 0, run.stderr)
        return json.loads(run.stdout)

    def assertNear(self, got, expected, tolerance=0.05):
        """Every point of each polyline lies on the other one."""
        def to_segment(p, a, b):
            dx, dy = b[0] - a[0], b[1] - a[1]
            t = max(0, min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / (dx * dx + dy * dy or 1)))
            return math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy)

        def one_way(a, b):
            return max(min(to_segment(p, b[i], b[i + 1]) for i in range(len(b) - 1)) for p in a)
        self.assertLess(max(one_way(got, expected), one_way(expected, got)), tolerance)

    def test_flipped_and_tilted_arcs_and_mirrored_blocks_are_drawn_as_autocad_does(self):
        import ezdxf

        doc = ezdxf.new("R2018")
        msp = doc.modelspace()
        flipped = msp.add_arc((-2000, 500), 80, 200, 20, dxfattribs={"extrusion": (0, 0, -1)})
        tilted = msp.add_arc((30, -40, 12), 100, 10, 120, dxfattribs={"extrusion": (0, 0.6, 0.8)})
        tilted_circle = msp.add_circle((5, 6, -3), 25, dxfattribs={"extrusion": (0.3, -0.4, 0.866)})
        block = doc.blocks.new("DOOR")
        block.add_arc((0, 0), 50, 0, 90)
        mirrored = msp.add_blockref("DOOR", (8000, 100), dxfattribs={"xscale": -1, "rotation": 30})
        cases = {
            "flipped": {"normal": [0, 0, -1], "arc": [-2000, 500, 80, 200, 20]},
            "tilted": {"normal": [0, 0.6, 0.8], "elevation": 12, "points": [30, -40, 100, 10, 120]},
            "tilted_circle": {"normal": [0.3, -0.4, 0.866], "elevation": -3, "points": [5, 6, 25, 0, 360], "full": True},
            # The editor's block matrix of DOOR at (8000, 100), x scale -1, 30 degrees.
            "mirrored_block": {"matrix": [-math.cos(math.radians(30)), -math.sin(math.radians(30)), -math.sin(math.radians(30)),
                                          math.cos(math.radians(30)), 8000, 100], "arc": [0, 0, 50, 0, 90]},
            "plan": {"normal": [0, 0, 1], "arc": [10, 20, 5, 30, 60]},
        }
        out = self.run_cases(cases)
        self.assertIsNone(out["plan"]["matrix"])
        self.assertEqual(out["flipped"]["matrix"], [-1, 0, 0, 1, 0, 0])
        self.assertFalse(out["flipped"]["tilted"])
        self.assertTrue(out["tilted"]["tilted"])
        self.assertNear(_arc_points(out["flipped"]["arc"]), _world(flipped))
        # Drawn with 72 chords a turn, as an ellipse is: within r·(1 - cos(π/72)).
        self.assertNear([(p["x"], p["y"]) for p in out["tilted"]["points"]], _world(tilted), 100 * (1 - math.cos(math.pi / 72)) + 0.02)
        self.assertNear([(p["x"], p["y"]) for p in out["tilted_circle"]["points"]], _world(tilted_circle), 25 * (1 - math.cos(math.pi / 72)) + 0.02)
        self.assertNear(_arc_points(out["mirrored_block"]["arc"]), _world(mirrored))

    def test_plain_matrices_give_the_values_they_gave_before(self):
        # The old import: centre through the matrix, radius by the mean scale,
        # angles plus the matrix rotation.  Only mirroring matrices changed.
        cases = {"none": {"matrix": None, "arc": [10, 20, 5, 30, 60], "text": 0.5}}
        for i, m in enumerate(([0.5, 0.8, -0.8, 0.5, 3, 4], [2, 0, 0, 3, 1, 1], [1, 0, 0, 1, 7, -2])):
            cases["m%d" % i] = {"matrix": m, "arc": [10, 20, 5, 30, 60], "text": 0.5}
        out = self.run_cases(cases)
        for name, case in cases.items():
            m = case["matrix"]
            with self.subTest(name):
                if m is None:
                    expected = {"cx": 10, "cy": 20, "r": 5, "a1": 30 * math.pi / 180, "a2": 60 * math.pi / 180}
                    self.assertEqual(out[name]["text"], 0.5)
                else:
                    scale = (math.sqrt(m[0] * m[0] + m[1] * m[1]) + math.sqrt(m[2] * m[2] + m[3] * m[3])) / 2
                    rot = math.atan2(m[1], m[0])
                    expected = {"cx": m[0] * 10 + m[2] * 20 + m[4], "cy": m[1] * 10 + m[3] * 20 + m[5], "r": abs(5 * scale),
                                "a1": 30 * math.pi / 180 + rot, "a2": 60 * math.pi / 180 + rot}
                    self.assertEqual(out[name]["text"], 0.5 + rot)
                self.assertEqual(out[name]["arc"], expected)

    def test_a_text_in_a_mirrored_block_runs_where_the_mirror_takes_it(self):
        out = self.run_cases({"mirror": {"matrix": [-1, 0, 0, 1, 0, 0], "text": math.radians(30)}})
        self.assertAlmostEqual(out["mirror"]["text"], math.radians(150))
