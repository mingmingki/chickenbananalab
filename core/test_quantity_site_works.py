"""Earthwork (excavation, backfill, surplus soil, lean concrete, shoring) and the outside of the
buildings (outer wall finish, basement wall waterproofing, roof waterproofing and insulation)."""
import io
import json

from django.test import SimpleTestCase

from core import quantity_floor_takeoff as ft
from core import quantity_site_works as works
from core.test_quantity_floor_takeoff import level, plan


def model(basement=True):
    """A 20 x 15 m tower: B1 (3.5 m), 1~3층, the roof; its lowest plan's outline is 70 m."""
    plans = [plan(300, 20, outside=70.0, outline=70.0), plan(300, 15, outside=70.0, outline=70.0),
             plan(300, 15, outside=70.0, outline=70.0), plan(80, 5, outside=40.0, outline=40.0)]
    levels = ([level("B1", 0, 3.5, "S-1")] if basement else []) + [
        level("1", 1, 3.0, "S-2"), level("2", 2, 2.85, "S-3"), level("3", 2, 2.85, "S-3"), level("R", 3, 2.85, "S-4")]
    if not basement:
        plans = plans[1:]
        for item in levels:
            item["plan"] -= 1
    return {"buildings": [{"name": "101동", "levels": levels, "plans": plans}]}


class EarthworkTests(SimpleTestCase):
    def foundation(self, takeoff):
        return next(r for r in takeoff["buildings"][0]["rows"] if r["key"] == "FT")["foundation"]

    def test_shored_and_open_cut_excavation(self):
        shored = ft.build_takeoff(model(), excavation="CIP등 흙막이공사")
        found = self.foundation(shored)
        area, perimeter, thickness = found["area"], found["perimeter"], found["thickness"]
        depth = 3.5 + thickness + 0.06
        group, = shored["earthwork"]["groups"]
        self.assertEqual(shored["earthwork"]["method"], "흙막이 (수직 굴착)")
        self.assertAlmostEqual(group["depth"], depth, places=2)
        widened = area + perimeter * 1.0 + 4.0
        self.assertAlmostEqual(group["excavation"], depth * widened, delta=0.2)
        built, lean = area * (3.5 + thickness), (area + perimeter * 0.1) * 0.06
        self.assertAlmostEqual(group["backfill"], depth * widened - built - lean, delta=0.3)
        self.assertAlmostEqual(group["surplus"], built + lean, delta=0.3)
        self.assertAlmostEqual(group["surplus_loose"], (built + lean) * 1.25, delta=0.5)
        self.assertAlmostEqual(group["shoring_length"], perimeter + 8.0, places=1)
        open_cut = ft.build_takeoff(model(), excavation="오픈컷")["earthwork"]["groups"][0]
        slope = depth * depth * (perimeter + 8.0) / 2 + 4 * depth ** 3 / 3  # 1 : 1.0
        self.assertAlmostEqual(open_cut["excavation"], depth * widened + slope, delta=0.3)
        self.assertEqual(open_cut["shoring_length"], 0.0)
        self.assertAlmostEqual(open_cut["surplus"], group["surplus"], places=1)  # the same soil leaves the site

    def test_the_method_and_the_working_space_can_be_changed(self):
        changed = ft.build_takeoff(model(), excavation="오픈컷", overrides={"excavation_method": "흙막이 (수직 굴착)", "excavation_margin": 0.5})
        earth = changed["earthwork"]
        self.assertEqual((earth["method"], earth["margin"]), ("흙막이 (수직 굴착)", 0.5))
        items = {a["key"]: a for a in changed["assumed"]}
        self.assertTrue(items["excavation_method"]["set"])
        self.assertIn("excavation_margin", items)
        self.assertNotIn("excavation_slope", items)  # no slope behind shoring
        unknown = ft.build_takeoff(model())["earthwork"]  # no answer: 4.6 m deep, an open cut
        self.assertEqual(unknown["method"], "오픈컷")

    def test_a_building_without_a_basement_digs_to_its_foundation(self):
        takeoff = ft.build_takeoff(model(basement=False))
        group, = takeoff["earthwork"]["groups"]
        thickness = self.foundation(takeoff)["thickness"]
        self.assertAlmostEqual(group["depth"], max(thickness + 0.06, 0.9), places=2)
        self.assertIn("frost_depth", {a["key"] for a in takeoff["assumed"]})


class ExteriorTests(SimpleTestCase):
    def test_outer_walls_basement_waterproofing_and_the_roof(self):
        m = model()
        m["buildings"][0]["plans"][1]["measure"].update(window_gaps=10.0, window_gap_area=2.0)
        takeoff = ft.build_takeoff(m)
        outside, = takeoff["exterior"]["buildings"]
        rows = {r["key"]: r for r in takeoff["buildings"][0]["rows"]}
        opening = rows["1"]["measures"]["opening_height"]
        self.assertEqual(opening, ft.WINDOW_HEIGHT)
        gross = 70 * 3.0 + 70 * 2.85 + 70 * 2.85 + 40 * 2.85  # 1~3층 and the roof storey (its parapets)
        self.assertAlmostEqual(outside["wall"], gross - 10.0 * opening, delta=0.3)
        self.assertAlmostEqual(outside["basement_waterproof"], 70 * 3.5, places=1)
        roof = outside["roof"]
        self.assertEqual(roof["storey"], "3층")  # the slab over the top storey below the roof
        self.assertAlmostEqual(roof["area"], rows["3"]["measures"]["slab_area"], places=1)
        self.assertAlmostEqual(roof["waterproof"], roof["area"] + roof["perimeter"] * 0.3, places=1)
        self.assertEqual(takeoff["exterior"]["finish"], "외부 수성페인트")
        stone = ft.build_takeoff(m, overrides={"exterior_finish": "석재"})
        self.assertEqual(stone["exterior"]["finish"], "석재")
        self.assertTrue({a["key"]: a for a in stone["assumed"]}["exterior_finish"]["set"])


class SiteSheetTests(SimpleTestCase):
    def test_the_excel_and_the_page(self):
        import openpyxl
        from pathlib import Path
        from django.contrib.auth import get_user_model
        from django.test import RequestFactory
        from core import quantity_views as views
        takeoff = json.loads(json.dumps(ft.build_takeoff(model(), excavation="CIP등 흙막이공사")))
        request = RequestFactory().post("/api/quantity/download-excel/", json.dumps({"results": {"floor_takeoff": takeoff}}),
                                        content_type="application/json")
        request.user = get_user_model()(is_staff=True)
        workbook = openpyxl.load_workbook(io.BytesIO(views._build_excel_response(request).content))
        self.assertEqual(workbook.sheetnames[:3], ["요약", "층별 집계(N층 기준)", "토공·외부"])
        rows = list(workbook["토공·외부"].iter_rows(values_only=True))
        self.assertTrue(any(r[0] == "101동" and r[4] for r in rows))
        summary = [c for r in workbook["요약"].iter_rows(values_only=True) for c in r if c]
        self.assertIn("굴착(㎥)", summary)
        html = (Path(views.__file__).parent / "templates" / "core" / "home.html").read_text(encoding="utf-8")
        self.assertIn("function siteWorksTable(takeoff) {", html)
        self.assertIn("excavation: (state.answers || {}).excavationType || undefined", html)
        self.assertIn('<span>토공 (굴착) · 외부</span>', html)
        self.assertEqual(views._excavation_answer({"excavation": " 오픈컷 "}), "오픈컷")
