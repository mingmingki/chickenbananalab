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
from .test_cad_dwg_save_integrity import NODE, _html
from .test_cad_dwg_text_validation import EXECUTABLE

FIXTURES = Path(settings.BASE_DIR) / "core" / "test_fixtures" / "cad"
MLEADER = FIXTURES / "mleader_ac1032.dwg"
KINDS = FIXTURES / "move_kinds_ac1032.dwg"

HARNESS = """
var window = globalThis; window.CBL_DXF_SINGLE_MODEL_IMPORT_V1 = true;
var document = {querySelector: () => null, querySelectorAll: () => [], getElementById: () => null,
                addEventListener: () => {}, createElement: () => ({style: {}, appendChild(){}, setAttribute(){}})};
window.addEventListener = () => {};
console.log = () => {};
%(module)s
const parsed = window.cblParseDxfSingleModelV1(require('fs').readFileSync(process.argv[2], 'utf8'));
process.stdout.write(JSON.stringify({skipped: parsed.stats.skipped, shapes: parsed.shapes.map(s => ({
  type: s.type, raw: s.rawDxfType, owner: s.ownerSourceHandle || '', handle: s.sourceHandle || '',
  displayOnly: !!s.displayOnly, text: s.text, size: s.size, rot: s.rot, x: s.x, y: s.y,
  x1: s.x1, y1: s.y1, x2: s.x2, y2: s.y2, pts: s.pts}))}));
"""


def _single_model_module(html):
    at = html.index("window.__CBL_DXF_SINGLE_MODEL_IMPORT_V1__ = true;")
    return html[html.rindex("<script>", 0, at) + len("<script>"):html.index("</script>", at)]


@skipUnless(NODE and EXECUTABLE is not None, "node and the ACadSharp runtime are required")
class CadEntityDisplayTests(SimpleTestCase):
    """What the editor draws for SPLINE, LEADER, MULTILEADER and DIMENSION.

    Multileaders and leaders were not drawn at all, fit-point splines were
    skipped and control-point splines were drawn as their control polygon.
    A drawing whose only object was a DIMENSION without its block (older
    ChickenBananaCAD saves) did not open: "full DXF에서 편집 객체를 찾지 못했습니다".
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cbl-display-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def parse_text(self, dxf_text):
        path = self.tmp / "editor.dxf"
        path.write_text(dxf_text, encoding="utf-8")
        script = self.tmp / "harness.js"
        script.write_text(HARNESS % {"module": _single_model_module(_html())}, encoding="utf-8")
        run = subprocess.run([NODE, str(script), str(path)], capture_output=True, text=True, timeout=120)
        self.assertEqual(run.returncode, 0, run.stderr[-1500:])
        return json.loads(run.stdout)

    def parse_dwg(self, dwg):
        # The DXF text the open API gives the editor.
        return self.parse_text(core_views._cbl_free_dwg_to_dxf_text_v1(dwg)[0])

    def parse_ezdxf(self, doc):
        dxf, dwg = self.tmp / "source.dxf", self.tmp / "source.dwg"
        doc.saveas(dxf)
        run = subprocess.run([str(EXECUTABLE), "--dwg-from-dxf", str(dxf), str(dwg)], capture_output=True, timeout=300)
        self.assertEqual(run.returncode, 0, run.stderr[-800:])
        return self.parse_dwg(dwg)

    def owned_by(self, parsed, handle):
        return [s for s in parsed["shapes"] if s["owner"] == handle]

    def test_multileaders_show_leaders_and_text(self):
        meta = core_views._cbl_free_dwg_acadsharp_metadata_v1(MLEADER)
        handles = [e["handle"] for e in meta["entities"] if e["type"] == "MULTILEADER"]
        parsed = self.parse_dwg(MLEADER)
        self.assertNotIn("MULTILEADER", parsed["skipped"])
        texts = []
        for handle in handles:
            children = self.owned_by(parsed, handle)
            self.assertTrue(children and all(c["displayOnly"] for c in children), handle)
            leader = next(c for c in children if c["type"] == "polyline")
            self.assertEqual(len(leader["pts"]), 2)
            texts += [c["text"] for c in children if c["type"] == "text"]
        self.assertEqual(texts, ["기초 F1"])
        text_leader = next(c for h in handles for c in self.owned_by(parsed, h) if c["type"] == "polyline" and c["pts"][0] == {"x": 0, "y": 0})
        self.assertEqual(text_leader["pts"][1], {"x": 990, "y": 798})

    def test_leader_is_drawn_through_its_vertices(self):
        import ezdxf

        doc = ezdxf.new("R2018", setup=True)
        doc.modelspace().add_leader([(0, 0), (50, 50), (100, 50)])
        parsed = self.parse_ezdxf(doc)
        self.assertNotIn("LEADER", parsed["skipped"])
        line = next(s for s in parsed["shapes"] if s["type"] == "polyline" and s["displayOnly"])
        self.assertEqual(line["pts"], [{"x": 0, "y": 0}, {"x": 50, "y": 50}, {"x": 100, "y": 50}])
        self.assertTrue(line["owner"])

    def test_spline_follows_the_curve(self):
        import ezdxf

        doc = ezdxf.new("R2018")
        control = [(0, 0), (100, 200), (300, -100), (400, 100), (600, 0)]
        spline = doc.modelspace().add_open_spline(control, degree=3)
        expected = [tuple(v)[:2] for v in spline.construction_tool().approximate(200)]
        parsed = self.parse_ezdxf(doc)
        shape = next(s for s in parsed["shapes"] if s["raw"] == "SPLINE")
        pts = [(q["x"], q["y"]) for q in shape["pts"]]
        self.assertGreater(len(pts), len(control))
        self.assertEqual(pts[0], (0, 0))
        self.assertEqual(pts[-1], (600, 0))
        for x, y in pts:
            gap = min(((x - ex) ** 2 + (y - ey) ** 2) ** 0.5 for ex, ey in expected)
            self.assertLess(gap, 3.0, (x, y))
        # Not the control polygon: the curve passes well inside (100, 200).
        self.assertNotIn((100, 200), pts)

    def test_fit_point_spline_is_drawn(self):
        import ezdxf

        doc = ezdxf.new("R2018")
        doc.modelspace().add_spline([(0, 100), (50, 150), (100, 100), (150, 150)])
        parsed = self.parse_ezdxf(doc)
        self.assertNotIn("SPLINE_NO_PTS", parsed["skipped"])
        shape = next(s for s in parsed["shapes"] if s["raw"] == "SPLINE")
        pts = [(q["x"], q["y"]) for q in shape["pts"]]
        # A smooth curve through every fit point, not their polyline.
        self.assertGreater(len(pts), 4)
        for fit in [(0, 100), (50, 150), (100, 100), (150, 150)]:
            self.assertIn(fit, pts)
        fit = [(0, 100), (50, 150), (100, 100), (150, 150)]

        def off_polyline(x, y):
            gaps = []
            for (ax, ay), (bx, by) in zip(fit, fit[1:]):
                t = max(0, min(1, ((x - ax) * (bx - ax) + (y - ay) * (by - ay)) / ((bx - ax) ** 2 + (by - ay) ** 2)))
                gaps.append(((x - ax - t * (bx - ax)) ** 2 + (y - ay - t * (by - ay)) ** 2) ** 0.5)
            return min(gaps)

        self.assertGreater(max(off_polyline(x, y) for x, y in pts), 2, pts)

    def test_dimension_without_block_is_drawn_from_its_points(self):
        dxf = "\n".join([
            "0", "SECTION", "2", "TABLES", "0", "TABLE", "2", "DIMSTYLE",
            "0", "DIMSTYLE", "2", "CBL_DIMSTYLE", "40", "1.0", "41", "11.0", "140", "20.0", "0", "ENDTAB", "0", "ENDSEC",
            "0", "SECTION", "2", "ENTITIES",
            "0", "DIMENSION", "5", "4A", "8", "0", "100", "AcDbDimension", "10", "433.867504905631", "20", "-319.198742641554",
            "11", "317.195706082982", "21", "-234.206440875527", "53", "0.0", "70", "161", "3", "CBL_DIMSTYLE",
            "100", "AcDbAlignedDimension", "13", "180.0", "23", "-180.0", "14", "420.0", "24", "-340.0",
            "0", "ENDSEC", "0", "EOF", ""])
        parsed = self.parse_text(dxf)
        self.assertNotIn("DIMENSION_NO_BLOCK", parsed["skipped"])
        children = self.owned_by(parsed, "4A")
        self.assertTrue(children and all(c["displayOnly"] for c in children))
        self.assertEqual([c["text"] for c in children if c["type"] == "text"], ["288.44"])
        text = next(c for c in children if c["type"] == "text")
        self.assertEqual(text["size"], 20)
        # Text along the dimension line (direction 240, -160).
        self.assertAlmostEqual(text["rot"], -0.5880026035475675, places=6)
        self.assertEqual(sum(1 for c in children if c["type"] == "line"), 7)


    def test_rotated_dimension_and_multileader_text_turn_with_the_object(self):
        # Saved by the writer's "transform" (the editor's rotate command).  The
        # dimension text stood horizontal after the reopen (its DIMENSION
        # record's text rotation 0 means "along the dimension line", not 0
        # degrees) and the multileader text slid down by its height.
        ops = self.tmp / "ops.json"
        ops.write_text(json.dumps({"ops": [{"type": "transform", "handle": h, "matrix": [0, 1, -1, 0, 1000, 0]} for h in ("93", "B1")]}))
        rotated = self.tmp / "rotated.dwg"
        run = subprocess.run([str(EXECUTABLE), str(KINDS), str(rotated), "AC1018", str(ops)], capture_output=True, timeout=300)
        self.assertEqual(run.returncode, 0, run.stderr[-800:])
        before, after = self.parse_dwg(KINDS), self.parse_dwg(rotated)
        for handle in ("93", "B1"):
            with self.subTest(handle):
                old = next(c for c in self.owned_by(before, handle) if c["type"] == "text")
                new = next(c for c in self.owned_by(after, handle) if c["type"] == "text")
                self.assertAlmostEqual(new["rot"], old["rot"] + math.pi / 2, places=6)
                self.assertAlmostEqual(new["x"], 1000 - old["y"], places=4)
                self.assertAlmostEqual(new["y"], old["x"], places=4)
        # A dimension the file already had at 31 degrees showed level text as well.
        self.assertAlmostEqual(next(c for c in self.owned_by(after, "A2") if c["type"] == "text")["rot"], math.atan2(60, 100), places=6)

@skipUnless(NODE, "node is required to execute the CAD editor helpers")
class CadUndisplayedNoticeTests(SimpleTestCase):
    def message(self, skipped):
        html = _html()
        start = html.index("  function cblUndisplayedObjectsMessageV1(skipped){")
        script = html[start:html.index("\n  function ", start + 10)]
        run = subprocess.run([NODE, "-e", script + "\nprocess.stdout.write(JSON.stringify(cblUndisplayedObjectsMessageV1(%s)));" % json.dumps(skipped)],
                             capture_output=True, text=True, timeout=60)
        self.assertEqual(run.returncode, 0, run.stderr)
        return json.loads(run.stdout)

    def test_names_what_is_not_drawn(self):
        self.assertEqual(self.message({"MLINE": 1, "XLINE": 2, "SEQEND": 4, "HATCH_EMPTY": 1}),
                         "화면에 표시하지 못한 객체 다중선 1개, 무한선 2개 (DWG에는 그대로 남고 저장해도 유지됩니다)")
        self.assertEqual(self.message({"SEQEND": 3}), "")
        self.assertEqual(self.message({}), "")

    def test_a_complete_dxf_with_nothing_to_draw_opens(self):
        html = _html()
        start = html.index("var emptyDrawing = Array.isArray(imported)")
        self.assertIn("cblDxfIsCompleteV1(result.dxf)", html[start:html.index("\n", start)])
        source = html[html.index("  function cblDxfIsCompleteV1(dxf){"):html.index("  function cblDxfHasNoModelSpaceEntitiesV1(dxf){")]
        cases = {
            "complete": "  0\nSECTION\n  2\nENTITIES\n  0\nMLINE\n  8\n0\n  0\nENDSEC\n  0\nEOF\n",
            "crlf": "  0\r\nSECTION\r\n  2\r\nENTITIES\r\n  0\r\nXLINE\r\n  0\r\nENDSEC\r\n  0\r\nEOF\r\n",
            "cut_off": "  0\nSECTION\n  2\nENTITIES\n  0\nLINE\n  8\n0\n 10\n",
            "garbage": "not a dxf",
        }
        script = source + "\nconst cases = " + json.dumps(cases) + ";\nconst out = {}; for (const [k, v] of Object.entries(cases)) out[k] = cblDxfIsCompleteV1(v);\nprocess.stdout.write(JSON.stringify(out));"
        run = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=60)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(json.loads(run.stdout), {"complete": True, "crlf": True, "cut_off": False, "garbage": False})


@skipUnless(NODE, "node is required to execute the CAD editor helpers")
class CadTextDedupeTests(SimpleTestCase):
    """The open removes repeated texts, but never a TEXT the user can edit.

    A TEXT entity drawn exactly over a dimension's (or block's) text, or over
    another TEXT entity, was hidden as a "duplicate": it stayed in the DWG but
    could not be selected, moved or deleted, so deleting the visible one left
    it in the saved file and it came back on the next open.
    """

    def test_a_render_only_piece_does_not_hide_an_editable_text(self):
        html = _html()
        start = html.index("<!-- CBL_TEXT_CLEANUP_DEDUPE_V1 -->")
        module = html[html.index("<script>", start) + len("<script>"):html.index("</script>", start)]
        piece = {"type": "text", "text": "8,000", "x": 10, "y": 20, "size": 600, "rot": 0.5, "layId": 3,
                 "displayOnly": True, "blockChild": True, "ownerSourceHandle": "1F60"}
        entity = {"type": "text", "text": "8,000", "x": 10, "y": 20, "size": 600, "rot": 0.5, "layId": 3,
                  "sourceHandle": "36CB8", "rawDxfType": "TEXT"}
        twin = dict(entity, sourceHandle="36CBB")
        cases = {"piece_first": [piece, entity], "entity_first": [entity, piece], "two_pieces": [piece, piece],
                 "two_entities": [entity, twin], "same_entity_twice": [entity, entity]}
        script = ("var window = globalThis; var console = {log(){}, error(){}};\n"
                  "var cases = %s, current = null;\nwindow.parseDXF = function(){ return JSON.parse(JSON.stringify(current)); };\n"
                  "%s\nvar out = {};\nfor (var k in cases) { current = cases[k]; out[k] = window.parseDXF().map(function(s){ return s.sourceHandle || 'piece'; }); }\n"
                  "process.stdout.write(JSON.stringify(out));") % (json.dumps(cases), module)
        run = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=60)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(json.loads(run.stdout), {"piece_first": ["piece", "36CB8"], "entity_first": ["36CB8"], "two_pieces": ["piece"],
                                                  "two_entities": ["36CB8", "36CBB"], "same_entity_twice": ["36CB8"]})
