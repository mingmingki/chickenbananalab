"""Rebar by storey from CAD: schedule bars on the takeoff's lengths, counts and areas, by the floor
rule, weighed by diameter and steel grade; shown in 3D as thin lines coloured by grade."""
import json
import tempfile
from pathlib import Path

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings

from core import quantity_calc as qc
from core import quantity_edits as qe
from core import quantity_floor_rebar as fr
from core import quantity_floor_takeoff as ft
from core import quantity_views as views
from core.test_quantity_floor_takeoff import FloorRuleTests

CORE = Path(__file__).parent


def links():
    return {
        "plans": [{"drawing_number": "S-2", "kind": "floor_plan", "section": "101동"},
                  {"drawing_number": "S-3", "kind": "floor_plan", "section": "101동"}],
        "schedule_rows": [
            [{"values": {"thickness": "200", "vertical_bar": "HD13@200", "horizontal_bar": "HD10@300"}, "levels_text": "1F - 13F"}],
            [{"values": {"size": "500 X 600", "top_bar": "4-SHD22", "bottom_bar": "3-SHD22", "stirrup": "HD10@200"}}],
            [{"values": {"size": "600 X 600", "main_bar": "12-SHD22", "hoop_mid": "HD10@300"}}],
        ],
        "marks": [
            {"category": "walls", "mark": "W1", "specs": [{"rows": 0}],
             "typical_plans": [{"plan": 0, "drawing_number": "S-2", "count": 1, "measured": {"length_mm": 10000, "widths": {"200": 10000}}}]},
            {"category": "beams", "mark": "G1", "specs": [{"rows": 1}], "occurrences": [{"plan": 1, "count": 1, "measured": {"widths": {"500": 8000}}}]},
            {"category": "columns", "mark": "C1", "specs": [{"rows": 2}], "occurrences": [{"plan": 0, "count": 4}]},
        ],
    }


def model():
    m = FloorRuleTests().model()
    for i, plan in enumerate(m["buildings"][0]["plans"]):
        plan["id"] = f"p{i}"
    return m


def rows(**kwargs):
    rebar = fr.build_rebar(model(), links(), **kwargs)
    return rebar, {r["key"]: r for r in rebar["buildings"][0]["rows"]}


class GradeTests(SimpleTestCase):
    def test_grade_from_the_bar_text_then_the_general_notes(self):
        self.assertEqual(fr.grade_of("HD13@200", 13, None), ("SD400", True))
        self.assertEqual(fr.grade_of("4-SHD22", 22, None), ("SD500", True))
        self.assertEqual(fr.grade_of("UHD16@300", 16, None), ("SD600", True))
        self.assertEqual(fr.grade_of("D19", 19, {"rebar_grade_table": [{"bar_size_max": 16, "fy_mpa": 400}, {"bar_size_max": 99, "fy_mpa": 500}]}),
                         ("SD500", True))
        self.assertEqual(fr.grade_of("D10@200", 10, None), ("SD400", False))  # assumed, said in the notes


class RebarTests(SimpleTestCase):
    def test_walls_columns_beams_and_slab_by_the_floor_rule(self):
        rebar, by_key = rows()
        one = by_key["1"]
        height = 2.85 - 0.21
        lap13, _ = qc.get_splice_length("D13", None, category="전단벽")
        vertical = qc._spacing_bar_count(10, 0.2) * 2 * (height + lap13) * 0.995  # W1: 10 m, two layers
        self.assertGreater(one["kg"]["wall"], vertical)                              # plus the unmarked walls
        self.assertTrue(any("부호 없는 벽" in n for n in one["notes"]))
        self.assertGreater(one["kg"]["column"], 0)                                   # 4 × C1 on the storey's plan
        self.assertGreater(one["kg"]["beam"], 0)                                     # G1 on the plan above
        self.assertEqual(by_key["3"]["kg"]["beam"], 0)                               # the roof plan has no G1
        self.assertGreater(one["kg"]["slab"], 0)
        self.assertIn("D13|SD400", one["by_bar"])
        self.assertIn("D22|SD500", one["by_bar"])
        self.assertAlmostEqual(rebar["with_loss_kg"], rebar["net_kg"] * qc.REBAR_WASTE_FACTOR, delta=0.5)
        # the unmarked walls (140 m) run longer than W1 (10 m): the 3D shows the plain wall bars
        self.assertEqual(one["draw"]["wall_vertical"], {"d": 10, "s": 0.3, "grade": "SD400"})

    def test_outer_openings_get_their_bars(self):
        m = model()
        m["buildings"][0]["plans"][1]["measure"].update(window_gaps=6.0, window_gap_count=3)
        rebar = fr.build_rebar(m, links())
        one = {r["key"]: r for r in rebar["buildings"][0]["rows"]}["1"]
        self.assertGreater(one["kg"]["opening"], 0)
        self.assertTrue(any("개구부 3곳" in n for n in one["notes"]))

    def test_peoples_fixes_change_the_rebar_too(self):
        _rebar, before = rows()
        fix = qe.clean([{"plan": "p2", "op": "add", "kind": "beam", "a": [0, 0], "b": [10, 0], "w": 0.4, "d": 0.6}])
        _rebar, after = rows(edits=fix)
        self.assertGreater(after["1"]["kg"]["beam"], before["1"]["kg"]["beam"])
        self.assertTrue(any("사람이 고친 부재" in n for n in after["1"]["notes"]))


class RebarApiTests(TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        override = override_settings(QUANTITY_STATE_DB=str(Path(tmp.name) / "state.sqlite3"))
        override.enable()
        self.addCleanup(override.disable)
        users = get_user_model().objects
        self.staff = users.create_user("staff", password="staff-pass-1", is_staff=True)
        self.other = users.create_user("other", password="other-pass-1", is_staff=True)
        self.client.force_login(self.staff)
        views._quantity_state().begin_job("fv-1", self.staff.pk, "api_quantity_floor_views", "x")
        views._result_set("fv-1", {"ok": True, "results": {"floor_views": {"model": model()}}})
        views._quantity_state().begin_job("fv-2", self.other.pk, "api_quantity_floor_views", "y")

    def post(self, body):
        return self.client.post("/api/quantity/floor-rebar/", json.dumps(body), content_type="application/json")

    def test_the_owner_gets_rebar_rows(self):
        body = self.post({"floor_job_id": "fv-1", "member_links": links()}).json()
        self.assertEqual([r["key"] for r in body["rebar"]["buildings"][0]["rows"]], ["FT", "B1", "1", "2", "3", "R"])
        self.assertEqual(self.post({"floor_job_id": "fv-2"}).status_code, 404)
        self.client.logout()
        self.assertIn(self.post({"floor_job_id": "fv-1"}).status_code, (302, 401, 403))

    def test_the_excel_gets_a_rebar_sheet(self):
        import openpyxl
        from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
        rebar = fr.build_rebar(model(), links())
        wb = openpyxl.Workbook()
        fill = PatternFill("solid", fgColor="000000")
        side = Side(style="thin")
        views._write_floor_rebar_sheet(wb.active, rebar, fill, fill, Font(), Font(), Alignment(), Alignment(),
                                       Border(left=side, right=side, top=side, bottom=side))
        texts = [c.value for row in wb.active.iter_rows() for c in row if isinstance(c.value, str)]
        self.assertIn("[101동]", texts)
        self.assertIn("직경·강종별 (근사 행 제외)", texts)


class RebarPageTests(SimpleTestCase):
    def test_concrete_is_confirmed_before_the_rebar_and_3d_colours_by_grade(self):
        html = (CORE / "templates" / "core" / "home.html").read_text()
        self.assertIn("레미콘 확정 → 철근 배근", html)
        flow = html[html.index("async function finishQuantityAnalysisFlow(data, cancelRequestedFlag) {"):][:6000]
        self.assertLess(flow.index("await showFloorTakeoff(data);"), flow.index("await showFloorRebar(data);"))
        self.assertIn('fetch("/api/quantity/floor-rebar/"', html)
        self.assertIn("mass.viewer.setRebar(rebar);", html)
        js = (CORE / "static" / "core" / "js" / "quantity_mass3d.js").read_text()
        self.assertIn("var GRADE = { SD400: 0x4ade80, SD500: 0xf59e0b, SD600: 0xf43f5e };", js)
        self.assertIn("setRebar: function (rebar) {", js)


class WallDetailTests(SimpleTestCase):
    def test_pieces_ends_and_openings_from_the_wall_faces(self):
        # one wall line 0.2 m thick: two pieces 3 m long with a 1.5 m gap (a door) between them
        walls = [[0, 0, 3, 0], [0, 0.2, 3, 0.2], [4.5, 0, 7.5, 0], [4.5, 0.2, 7.5, 0.2]]
        pieces, openings = fr.wall_pieces(walls)
        self.assertEqual(len(pieces), 2)
        self.assertEqual([round(w, 2) for w in openings], [1.5])
        tally = fr._Tally(None)
        counted = fr._wall_details(tally, walls, 2.64, "D10@300", "D10@300", {"window_gaps": 3.6, "window_gap_count": 2})
        self.assertEqual(counted, (2, 3))  # the door and two windows
        self.assertGreater(tally.kg["opening"], 0)
        self.assertIn("D16|SD400", tally.bars)
