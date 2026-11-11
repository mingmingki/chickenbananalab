"""Floor-by-floor takeoff: wall sections measured on CAD lines, rows by the floor rule, API, Excel."""
import io
import json
import tempfile
from pathlib import Path

import numpy as np
import openpyxl
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings

from core import quantity_floor_takeoff as ft
from core import quantity_floor_views as fv
from core import quantity_views as views

HOME = Path(__file__).with_name("templates").joinpath("core", "home.html")


def box(x0, y0, x1, y1):
    return [(x0, y0, x1, y0), (x1, y0, x1, y1), (x1, y1, x0, y1), (x0, y1, x0, y0)]


class WallMeasureTests(SimpleTestCase):
    def test_a_closed_wall_gives_its_section_and_both_faces(self):
        measured = ft.measure_walls(box(0, 0, 5, 0.2))
        self.assertAlmostEqual(measured["area"], 1.0, delta=0.06)
        self.assertAlmostEqual(measured["faces"], 10.4, delta=0.5)

    def test_rooms_between_walls_are_not_counted(self):
        # a 4 x 3 m room drawn as the inner faces of four 0.2 m walls
        lines = box(0, 0, 4.4, 3.4) + box(0.2, 0.2, 4.2, 3.2)
        measured = ft.measure_walls(lines)
        self.assertAlmostEqual(measured["area"], 4.4 * 3.4 - 4.0 * 3.0, delta=0.15)

    def test_an_open_wall_end_is_closed_before_measuring(self):
        lines = [(0, 0, 5, 0), (0, 0.2, 5, 0.2), (0, 0, 0, 0.2)]  # the right end line is missing
        self.assertEqual(len(ft.wall_caps(lines)), 1)
        self.assertAlmostEqual(ft.measure_walls(lines)["area"], 1.0, delta=0.06)

    def test_collinear_wall_ends_across_a_door_are_not_joined(self):
        lines = [(0, 0, 2, 0), (3, 0, 5, 0)]
        self.assertEqual(ft.wall_caps(lines), [])

    def test_outside_faces_use_the_building_outline(self):
        lines = box(0, 0, 4.4, 3.4) + box(0.2, 0.2, 4.2, 3.2)
        outside = lambda x, y: not (0 <= x <= 4.4 and 0 <= y <= 3.4)
        measured = ft.measure_walls(lines, outside)
        self.assertAlmostEqual(measured["outside_faces"], 2 * (4.4 + 3.4), delta=0.6)
        self.assertAlmostEqual(measured["faces"], 2 * (4.4 + 3.4) + 2 * (4.0 + 3.0), delta=1.0)

    def test_an_opening_between_outer_walls_on_one_line_is_found(self):
        # a 10 x 6 m building: the bottom wall has a 1.5 m window, the slab edge runs across it
        lines = [(0, 0, 4, 0), (0, 0.2, 4, 0.2), (4, 0, 4, 0.2), (5.5, 0, 10, 0), (5.5, 0.2, 10, 0.2), (5.5, 0, 5.5, 0.2),
                 (0, 0, 0, 6), (0.2, 0.2, 0.2, 5.8), (10, 0, 10, 6), (9.8, 0.2, 9.8, 5.8),
                 (0, 6, 10, 6), (0.2, 5.8, 9.8, 5.8)]
        outside = lambda x, y: not (0 <= x <= 10 and 0 <= y <= 6)
        measured = ft.measure_walls(lines, outside)
        self.assertAlmostEqual(measured["window_gaps"], 1.5, delta=0.01)
        self.assertAlmostEqual(measured["window_gap_area"], 1.5 * 0.2, delta=0.03)
        self.assertEqual(ft.measure_walls(lines)["window_gaps"], 0)  # no outline, no openings

    def test_grid_algebra_never_overwrites_its_inputs(self):
        a = np.zeros((5, 5), bool)
        a[2, 2] = True
        before = a.copy()
        ft._or(a, ft._shift(a, 1, 0))
        ft._and(a, ft._not(a))
        ft._dil8(a)
        self.assertTrue(np.array_equal(a, before))

    def test_an_opening_cross_and_a_stair_give_hull_areas(self):
        cross = [(0, 0, 2, 2.5), (0, 2.5, 2, 0)]
        self.assertEqual(ft.cluster_areas(cross, margin=0.05, min_area=0.3), [5.0])
        treads = [(10, y / 4, 12.5, y / 4) for y in range(0, 21)]  # 2.5 x 5 m of treads
        self.assertAlmostEqual(ft.cluster_areas(treads, margin=0.4, min_area=4.0)[0], 12.5, delta=0.1)


class PlanModelTests(SimpleTestCase):
    def test_the_plan_model_carries_the_takeoff_measures(self):
        def path(category, points):
            xs, ys = [p[0] for p in points], [p[1] for p in points]
            return (category, min(xs), min(ys), max(xs), max(ys), points)
        mm = lambda pts: [(x * 1000, y * 1000) for x, y in pts]
        paths = [path("wall", mm([(0, 0), (10, 0), (10, 0.2), (0, 0.2), (0, 0)])),
                 path("wall", mm([(0, 6), (10, 6), (10, 6.2), (0, 6.2), (0, 6)])),
                 path("outline", mm([(0, 0), (0, 6.2)])), path("outline", mm([(10, 0), (10, 6.2)])),
                 path("open", mm([(4, 2), (6, 4)])), path("open", mm([(4, 4), (6, 2)]))]
        model = fv._plan_model(paths, (-1000, -1000, 11000, 7200))
        measure = model["measure"]
        self.assertAlmostEqual(measure["area"], 4.0, delta=0.2)
        self.assertEqual(measure["openings"], [4.0])
        self.assertAlmostEqual(measure["outline"], 12.4, delta=0.01)
        self.assertGreater(measure["outside_faces"], 15)

    def test_plans_drawn_apart_in_one_frame_are_measured_one_by_one(self):
        lines = [list(s) for s in box(0, 0, 10, 8) + box(30, 0, 34, 4)]
        lines += [[0, 4, 10, 4]] * 10 + [[30, 2, 34, 2]] * 10  # enough lines to count as plans
        self.assertEqual([round(a) for a in fv._separate_slabs(lines)], [84, 18])


def plan(slab_area, area, faces=100.0, outside=40.0, outline=20.0, approximate=False, openings=(), stairs=(), sub=None):
    data = {"slab_area": slab_area, "slab_approximate": approximate,
            "measure": {"area": area, "faces": faces, "outside_faces": outside, "outline": outline,
                        "col_area": 0, "col_perimeter": 0, "openings": list(openings), "stairs": list(stairs)}}
    if sub:
        data["sub_slabs"] = sub
    return data


def level(key, plan_index, height=2.85, sheet=None):
    return {"key": key, "label": key + "층", "elev": 0, "height": height, "plan": plan_index, "sheet": sheet}


class FloorRuleTests(SimpleTestCase):
    def model(self):
        plans = [plan(900, 50, approximate=True), plan(500, 30, openings=[5], stairs=[10]),
                 plan(460, 28), plan(60, 8, approximate=True, sub=[45.0, 20.0])]
        levels = [level("B1", 0, 3.5, "S-1"), level("1", 1, sheet="S-2"), level("2", 2, sheet="S-3"),
                  level("3", 2, sheet="S-3"), level("R", 3, sheet="S-4")]
        return {"buildings": [{"name": "101동", "levels": levels, "plans": plans}]}

    def rows(self, links=None):
        return {r["key"]: r for r in ft.build_takeoff(self.model(), links)["buildings"][0]["rows"]}

    def test_a_storey_takes_its_walls_and_the_slab_of_the_floor_above(self):
        rows = self.rows()
        one = rows["1"]
        self.assertAlmostEqual(one["concrete"]["wall"], 30 * (2.85 - 0.21), places=1)
        self.assertEqual(one["sheets"], {"vertical": "S-2", "horizontal": "S-3"})
        self.assertAlmostEqual(one["concrete"]["slab"], 460 * 0.21, places=1)
        self.assertAlmostEqual(one["formwork"]["gang"], 40 * 2.64 + (40 + 20) * 0.21, places=1)
        self.assertAlmostEqual(one["formwork"]["al_vertical"], 60 * 2.64, places=1)
        self.assertAlmostEqual(one["formwork"]["al_horizontal"], 460 - 30, places=1)

    def test_basements_are_approximate_and_left_out_of_the_totals(self):
        take = ft.build_takeoff(self.model())["buildings"][0]
        basement = [r for r in take["rows"] if r["key"] == "B1"][0]
        self.assertTrue(basement["approximate"])
        self.assertGreater(basement["formwork"]["euro"], 0)
        ground = sum(r["concrete"]["total"] for r in take["rows"] if not r["approximate"])
        self.assertAlmostEqual(take["totals"]["concrete"]["total"], ground, places=0)
        self.assertGreater(take["approximate_totals"]["concrete"]["total"], 0)

    def test_the_slab_under_a_roof_sheet_covers_the_storey_below(self):
        rows = self.rows()
        self.assertAlmostEqual(rows["3"]["concrete"]["slab"], 460 * 0.21, places=1)  # not 45 m² from the sheet
        self.assertAlmostEqual(rows["R"]["concrete"]["slab"], 20 * 0.21, places=1)   # penthouse roof: smallest plan

    def test_a_set_back_floor_above_keeps_this_storeys_slab(self):
        model = self.model()
        model["buildings"][0]["plans"][2]["slab_area"] = 300  # floor 2 set back against floor 1 (485 net)
        rows = {r["key"]: r for r in ft.build_takeoff(model)["buildings"][0]["rows"]}
        self.assertAlmostEqual(rows["1"]["concrete"]["slab"], (500 - 5 - 10) * 0.21, places=1)

    def test_schedules_give_beam_and_column_sizes(self):
        links = {
            "plans": [{"drawing_number": "S-3", "kind": "floor_plan", "section": "101동",
                       "geometry": {"beams": {"unlabelled": {"widths": {"400": 2000}}}}},
                      {"drawing_number": "S-2", "kind": "floor_plan", "section": "101동"}],
            "schedule_rows": [[{"values": {"size": "500 X 1,300"}}], [{"values": {"size": "400 X 900"}}]],
            "marks": [
                {"category": "beams", "mark": "TG1", "specs": [{"rows": 0}],
                 "occurrences": [{"plan": 0, "count": 1, "measured": {"widths": {"500": 10000}}}]},
                {"category": "columns", "mark": "C1", "specs": [{"rows": 1}], "occurrences": [{"plan": 1, "count": 5}]},
            ],
        }
        one = self.rows(links)["1"]
        self.assertAlmostEqual(one["concrete"]["column"], 5 * 0.4 * 0.9 * 2.64, places=1)
        self.assertAlmostEqual(one["concrete"]["wall"], (30 - 1.8) * 2.64, places=1)
        expected_beams = 0.5 * (1.3 - 0.21) * 10 + 0.4 * (0.6 - 0.21) * 2
        self.assertAlmostEqual(one["concrete"]["beam"], expected_beams, places=2)
        self.assertTrue(any("춤이 도면에 없어" in n for n in one["notes"]))

    def test_openings_add_the_wall_above_and_below_and_stairs_are_estimated(self):
        model = self.model()
        model["buildings"][0]["plans"][1]["measure"].update(window_gaps=20.0, window_gap_area=4.0)
        one = {r["key"]: r for r in ft.build_takeoff(model)["buildings"][0]["rows"]}["1"]
        left = 2.85 - 0.21 - ft.WINDOW_HEIGHT
        self.assertAlmostEqual(one["concrete"]["wall"], 30 * 2.64 + 4.0 * left, places=2)
        self.assertAlmostEqual(one["formwork"]["gang"], 40 * 2.64 + 20 * left + (40 + 20) * 0.21, places=1)
        self.assertAlmostEqual(one["concrete"]["stair"], 10 * ft.STAIR_CONCRETE_PER_M2, places=2)
        self.assertAlmostEqual(one["formwork"]["stair"], 10 * ft.STAIR_FORM_PER_M2, places=2)
        self.assertTrue(any("개구부" in n for n in one["notes"]))

    def test_schedule_sizes_read_millimetres(self):
        self.assertEqual(ft._size("500 X 1,300"), (0.5, 1.3))
        self.assertEqual(ft._size("180 X 건축치수"), (0.18, None))


class PodiumSplitTests(SimpleTestCase):
    def test_tower_outlines_on_a_podium_plan_become_regions(self):
        walls = [box(0, 0, 20, 0.3), box(30, 0, 50, 0.3)]
        walls = [[(a, b), (c, d)] for seg in walls for a, b, c, d in seg]
        above = [[(-1, -1), (10, -1)], [(10, -1), (10, 10)], [(10, 10), (-1, 10)], [(-1, 10), (-1, -1)]]
        measured = ft.measure_plan(walls, [], [], [], above=above, slab_rects=[[0, 0, 50, 20]])
        region = measured["regions"][0]
        self.assertEqual(region["hull_area"], 121.0)
        self.assertAlmostEqual(region["slab_area"], 100.0, places=1)
        # only the wall part inside the outline: half of the first of two equal walls
        self.assertAlmostEqual(region["area"], measured["area"] / 4, delta=0.1)
        self.assertAlmostEqual(measured["area"], 40 * 0.3, delta=0.9)

    def model(self):
        region = {"hull_area": 700.0, "area": 20.0, "faces": 100.0, "col_area": 0, "col_perimeter": 0, "slab_area": 650.0}
        podium = {"name": "주차장", "levels": [level("B1", 0, 3.5, "P-1"), level("R", 1, 3.0, "P-2")],
                  "plans": [plan(3000, 80, faces=500, outside=100) | {"measure": dict(plan(3000, 80, faces=500, outside=100)["measure"], regions=[region])},
                            plan(2000, 10)]}
        tower = {"name": "101동", "levels": [level("B1", 0, 5.0, "T-1"), level("1", 1, sheet="T-2"), level("2", 1, sheet="T-2")],
                 "plans": [plan(2800, 60, approximate=True), plan(600, 30) | {"hull_area": 690.0}]}
        return {"buildings": [tower, podium]}

    def test_a_tower_takes_its_part_of_the_podium_and_the_podium_keeps_the_rest(self):
        self.assertEqual(ft.tower_regions(self.model()["buildings"]), ("주차장", {"101동": 0}))
        take = {b["name"]: {r["key"]: r for r in b["rows"]} for b in ft.build_takeoff(self.model())["buildings"]}
        tower, podium = take["101동"]["B1"], take["주차장"]["B1"]
        self.assertFalse(tower["approximate"])
        self.assertAlmostEqual(tower["concrete"]["wall"], 20 * (5.0 - 0.21), places=1)  # the tower's own storey height
        self.assertAlmostEqual(podium["measures"]["vertical_area"], 80 - 20, places=1)
        self.assertFalse(podium["approximate"])
        self.assertAlmostEqual(tower["concrete"]["slab"], 600 * 0.21, places=1)  # the tower's own ground floor

    def test_without_a_matching_outline_basements_stay_approximate(self):
        model = self.model()
        model["buildings"][0]["plans"][1]["hull_area"] = 5000.0
        take = {b["name"]: b for b in ft.build_takeoff(model)["buildings"]}
        self.assertTrue(take["101동"]["rows"][0]["approximate"])


class SlabThicknessTests(SimpleTestCase):
    def texts(self):
        t = lambda text, x, y: {"text": text, "x": x, "y": y, "h": 120.0}
        return [t("MARK", 2060, 11660), t("THK.", 3620, 11776), t("(mm)", 3600, 11544),
                t("RS1", 2190, 10370), t("C", 3005, 10370), t("250", 3692, 10370),
                t("-1S2", 2166, 8750), t("C", 3005, 8750), t("150", 3692, 8750),
                t("D10@300", 4446, 8615), t("-  : THK.210", 30000, 18600), t("-  : THK.180 (욕실부분)", 30000, 18370),
                t("미표기 내수압 : FS1(THK. 300)", 30000, 18000)]

    def test_a_slab_schedule_and_its_notes_give_thickness_by_mark(self):
        from core import quantity_schedules as qs
        self.assertEqual(qs.read_slab_thickness(self.texts()), {"marks": {"RS1": 250, "-1S2": 150}, "default": 210})
        self.assertIn("slab_thickness", qs.read_sheet_content(self.texts(), "주차장 슬래브 배근도"))
        self.assertNotIn("slab_thickness", qs.read_sheet_content(self.texts(), "지하2층 구조평면도"))

    def links(self):
        return {"slab_thickness": {"marks": {"-1S2": 150, "RAS1": 250}, "default": 210},
                "plans": [{"drawing_number": "S-2", "kind": "floor_plan", "section": "101동"}],
                "marks": [{"category": "slabs", "mark": "S2", "occurrences": [{"plan": 0, "count": 3}]},
                          {"category": "slabs", "mark": "RAS1", "occurrences": [{"plan": 0, "count": 1}]}]}

    def test_plan_marks_take_the_level_prefix_and_are_weighted_by_count(self):
        t, note = ft.slab_thickness_for(self.links(), "101동", "S-2", "B1")
        self.assertAlmostEqual(t, (3 * 150 + 250) / 4 / 1000, places=3)
        self.assertIn("-1S2", note)
        self.assertIsNone(ft.slab_thickness_for(self.links(), "101동", "S-9", "B1"))

    def test_rows_use_the_drawn_thickness_and_the_notes_default_above_ground(self):
        model = FloorRuleTests().model()
        model["buildings"][0]["levels"][1]["sheet"] = "S-2"
        links = self.links()
        links["plans"][0]["drawing_number"] = "S-2x"  # the B1 row's slab (plan of floor 1) has no marks here
        rows = {r["key"]: r for r in ft.build_takeoff(model, links)["buildings"][0]["rows"]}
        self.assertTrue(any("210mm" in n for n in rows["1"]["notes"]))  # ground floor: the notes' default
        self.assertAlmostEqual(rows["1"]["concrete"]["wall"], 30 * (2.85 - 0.21), places=1)
        links["plans"][0]["drawing_number"] = "S-2"
        b1 = {r["key"]: r for r in ft.build_takeoff(model, links)["buildings"][0]["rows"]}["B1"]
        # the slab on top of B1 is drawn on the floor-1 plan: prefix "1", so only RAS1 (250) is known
        self.assertAlmostEqual(b1["concrete"]["wall"], 50 * (3.5 - 0.25), places=1)
        self.assertTrue(any("250mm" in n for n in b1["notes"]))


class _Shim:
    pass


@override_settings()
class FloorTakeoffApiTests(TestCase):
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
        model = FloorRuleTests().model()
        views._quantity_state().begin_job("fv-1", self.staff.pk, "api_quantity_floor_views", "x")
        views._result_set("fv-1", {"ok": True, "results": {"floor_views": {"model": model}}})
        views._quantity_state().begin_job("fv-2", self.other.pk, "api_quantity_floor_views", "y")
        views._result_set("fv-2", {"ok": True, "results": {"floor_views": {"model": model}}})

    def post(self, body):
        return self.client.post("/api/quantity/floor-takeoff/", json.dumps(body), content_type="application/json")

    def test_the_owner_gets_rows_by_the_floor_rule(self):
        response = self.post({"floor_job_id": "fv-1"})
        self.assertEqual(response.status_code, 200)
        takeoff = response.json()["takeoff"]
        self.assertEqual(takeoff["rule"], "N층 = N층 벽·기둥 + N+1층 바닥 슬래브·보")
        self.assertFalse(takeoff["uses_schedules"])
        self.assertEqual([r["key"] for r in takeoff["buildings"][0]["rows"]], ["B1", "1", "2", "3", "R"])

    def test_someone_elses_or_missing_job_is_not_found(self):
        self.assertEqual(self.post({"floor_job_id": "fv-2"}).status_code, 404)
        self.assertEqual(self.post({"floor_job_id": "nope"}).status_code, 404)
        self.assertEqual(self.post({}).status_code, 404)
        self.assertEqual(self.client.post("/api/quantity/floor-takeoff/", "{", content_type="application/json").status_code, 400)

    def test_member_links_switch_the_schedules_on(self):
        links = {"marks": [{"category": "columns", "mark": "C1", "specs": [], "occurrences": []}], "plans": [], "schedule_rows": []}
        self.assertTrue(self.post({"floor_job_id": "fv-1", "member_links": links}).json()["takeoff"]["uses_schedules"])

    def test_only_staff_may_ask(self):
        self.client.logout()
        self.assertIn(self.post({"floor_job_id": "fv-1"}).status_code, (302, 401, 403))

    def test_the_excel_gets_a_floor_sheet(self):
        takeoff = self.post({"floor_job_id": "fv-1"}).json()["takeoff"]
        response = self.client.post("/api/quantity/download-excel/", json.dumps({"results": {"floor_takeoff": takeoff}}),
                                    content_type="application/json")
        self.assertEqual(response.status_code, 200)
        workbook = openpyxl.load_workbook(io.BytesIO(response.content))
        self.assertIn("층별 집계(N층 기준)", workbook.sheetnames)
        sheet = workbook["층별 집계(N층 기준)"]
        texts = [c.value for row in sheet.iter_rows() for c in row if isinstance(c.value, str)]
        self.assertIn("[101동]", texts)
        self.assertIn("콘크리트 계(㎥)", texts)
        self.assertIn("근사 행 합계(참고)", texts)


class TakeoffPageTests(SimpleTestCase):
    def test_the_takeoff_card_follows_the_3d_review_and_rides_along_to_excel(self):
        html = HOME.read_text()
        finish = html.index("async function finishQuantityAnalysisFlow(data, cancelRequestedFlag) {")
        body = html[finish:finish + 5000]
        self.assertLess(body.index("await showMassStage();"), body.index("await showFloorTakeoff(data);"))
        self.assertLess(body.index("data.results.floor_takeoff = takeoff"), body.index("lastAnalysisResults = data.results || {};"))
        self.assertIn('fetch("/api/quantity/floor-takeoff/"', html)
        self.assertIn("quantity_mass3d.js' %}?v=20261111-fix1", html)
        self.assertIn("quantity_deck.css' %}?v=20261111-fix1", html)

    def test_the_3d_view_puts_the_slab_of_plan_n_plus_1_on_top_of_storey_n(self):
        js = Path(__file__).with_name("static").joinpath("core", "js", "quantity_mass3d.js").read_text()
        self.assertIn("function topPlans(b, index)", js)
        self.assertIn("var next = index + 1 < b.levels.length ? b.plans[b.levels[index + 1].plan] : null;", js)
