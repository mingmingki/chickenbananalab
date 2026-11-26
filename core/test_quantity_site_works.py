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


class GroundLevelTests(SimpleTestCase):
    def test_the_ground_level_from_the_sections_sets_the_depth(self):
        m = model()
        levels = m["buildings"][0]["levels"]
        for item, elev in zip(levels, (6.5, 10.0, 13.0, 15.85, 18.7)):
            item["elev"] = elev  # B1 at EL+6.5, 1층 at EL+10.0
        m["buildings"][0]["ground_elev"] = 9.5  # GL half a metre under the ground floor
        takeoff = ft.build_takeoff(m)
        thickness = next(r for r in takeoff["buildings"][0]["rows"] if r["key"] == "FT")["foundation"]["thickness"]
        group, = takeoff["earthwork"]["groups"]
        self.assertAlmostEqual(group["depth"], 9.5 - (6.5 - thickness - 0.06), places=2)
        self.assertEqual(group["ground_from"], "drawing")
        self.assertNotIn("ground_level", {a["key"] for a in takeoff["assumed"]})
        del m["buildings"][0]["ground_elev"]
        assumed = ft.build_takeoff(m)
        self.assertAlmostEqual(assumed["earthwork"]["groups"][0]["depth"], 10.0 - (6.5 - thickness - 0.06), places=2)
        self.assertIn("ground_level", {a["key"] for a in assumed["assumed"]})

    def test_the_model_takes_the_ground_level_only_in_the_floors_datum(self):
        from core import quantity_floor_views as fv
        table = {"sections": {"101동": {"elevations": {"GL": 9.5, "1": 10.0, "B1": 6.5}}}}
        self.assertEqual(fv._ground_elev(table, "101동", ["B1", "1", "2"]), 9.5)
        self.assertIsNone(fv._ground_elev({"sections": {"공통": {"elevations": {"GL": 9.5}}}}, "101동", ["B1", "1"]))

    def test_a_legend_pairs_each_buildings_ground_with_its_ground_floor(self):
        from core import quantity_floor_views as fv
        from core import quantity_levels as lv
        texts = [{"text": t, "x": 0.0, "y": 0.0, "h": 1.0} for t in
                 ("GL = EL+ 9.70 / 1FL+10.30", "GL = EL+10.40 / 1FL+11.00", "GL = EL+11.10 / 1FL+11.70", "- GL±0=EL+16.20")]
        self.assertEqual(lv.ground_pairs(texts), [(9.7, 10.3), (10.4, 11.0), (11.1, 11.7)])
        table = lv.build_level_table({"A-421.dwg": {"sheets": [{"drawing_title": "동 주단면도", "levels": lv.read_sheet_levels(texts)}]}})
        self.assertEqual(table["ground_pairs"], [[9.7, 10.3], [10.4, 11.0], [11.1, 11.7]])
        # the plans give the slab (SL+10.2), the legend the finished floor (1FL+10.30): within 0.2 m
        table["sections"] = {"103동": {"elevations": {"GL": 10.4, "1": 10.2}}}  # a GL read off a neighbour's section
        self.assertEqual(fv._ground_elev(table, "103동", ["B1", "1", "2"], {"B1": 4.7, "1": 10.2}), 9.7)
        self.assertEqual(fv._ground_elev(table, "101동", ["B1", "1"], {"B1": 4.7, "1": 11.6}), 11.1)
        self.assertIsNone(lv.ground_for_floor(table, 13.0))


class DeckEarthworkTests(SimpleTestCase):
    def deck_and_tower(self, deck_ground=None):
        takeoff = [{"name": "지하주차장", "rows": [{"key": "FT", "foundation": {"area": 2000.0, "perimeter": 200.0, "thickness": 0.6}}]},
                   {"name": "101동", "rows": [{"key": "FT", "foundation": {"area": 400.0, "perimeter": 80.0, "thickness": 1.0}}]}]
        model = [{"name": "지하주차장", "ground_elev": deck_ground, "site": {"placed": True, "host": True},
                  "levels": [{"key": "B2", "elev": 1.1, "height": 3.6}, {"key": "B1", "elev": 4.7, "height": 3.5}, {"key": "R", "elev": 8.2, "height": 3.0}]},
                 {"name": "101동", "ground_elev": 11.1, "site": {"placed": True, "on": "지하주차장"},
                  "levels": [{"key": "B2", "elev": 1.1, "height": 3.6}, {"key": "B1", "elev": 4.7, "height": 6.9}, {"key": "1", "elev": 11.6, "height": 4.1}]}]
        return takeoff, model

    def test_a_tower_placed_on_the_deck_is_dug_with_it_from_the_towers_ground(self):
        takeoff, model = self.deck_and_tower()
        earth = works.earthwork(takeoff, model, None, {}, excavation="CIP등 흙막이공사")  # no outlines matched on the deck's plans
        group, = earth["groups"]
        self.assertEqual((group["name"], group["parts"]), ("지하주차장", ["지하주차장", "101동"]))
        self.assertEqual((group["ground"], group["ground_from"]), (11.1, "towers"))  # not the deck's roof (8.2)
        self.assertAlmostEqual(group["depth"], 11.1 - (1.1 - 0.6 - 0.06), places=2)
        self.assertAlmostEqual(group["pits"], 400.0 * 0.4, places=1)  # the tower's mat 0.4 m deeper than the deck's
        self.assertAlmostEqual(group["shoring_length"], 200.0 + 8.0, places=1)  # one shoring line round the deck
        # a GL the deck's own details give that disagrees with its towers': the towers' wins
        takeoff, model = self.deck_and_tower(deck_ground=13.5)
        self.assertEqual(works.earthwork(takeoff, model, None, {})["groups"][0]["ground"], 11.1)

    def test_a_deck_roof_is_waterproofed_not_insulated_and_its_towers_basements_are_its_own(self):
        def row(key, faces, height, slab=0.0):
            return {"key": key, "label": key, "height": height, "measures": {"outside_faces": faces, "slab_area": slab}}
        buildings = [{"name": "지하주차장", "rows": [row("B2", 180.0, 3.6, 2000.0), row("B1", 180.0, 3.5, 2000.0), row("R", 0.0, 3.0)]},
                     {"name": "101동", "rows": [row("B1", 80.0, 6.9, 400.0), row("1", 80.0, 4.1, 380.0), row("R", 60.0, 2.8)]}]
        model = [{"name": "지하주차장"}, {"name": "101동", "site": {"placed": True, "on": "지하주차장"}}]
        outside = {b["name"]: b for b in works.exterior(buildings, {}, model_buildings=model)["buildings"]}
        deck, tower = outside["지하주차장"], outside["101동"]
        self.assertEqual(deck["roof"]["insulation"], 0.0)
        self.assertAlmostEqual(deck["roof"]["waterproof"], 2000.0 + 180.0 * 0.3, places=1)
        self.assertAlmostEqual(deck["basement_waterproof"], 180.0 * (3.6 + 3.5), places=1)
        self.assertEqual(tower["basement_waterproof"], 0.0)  # the deck's walls
        self.assertEqual(tower["roof"]["insulation"], 380.0)

    def test_a_foundation_sheet_naming_its_storey_sets_the_bottom_of_the_dig(self):
        from core import quantity_floor_views as fv
        self.assertEqual(fv.floor_of("지하2층 기초구조평면도")["storey"], "B2")
        self.assertNotIn("storey", fv.floor_of("101동 기초 구조평면도"))
        plans = [{"floor": fv.floor_of(t), "sheet": s, "model": {"id": s}} for t, s in
                 (("지하주차장 지하2층 기초구조평면도", "S-621"), ("지하주차장 지하1층 구조평면도", "S-631"), ("지하주차장 지붕 구조평면도", "S-641"))]
        table = {"sections": {"지하주차장": {"elevations": {"B2": 45.5, "B1": 49.0}}, "공통": {"elevations": {"GL": 54.0}}}}
        deck, = fv.build_model(plans, table, site=False)["buildings"]
        self.assertEqual([l["key"] for l in deck["levels"]], ["B1", "R"])  # no storey of the takeoff added
        self.assertEqual(deck["mat_elev"], 45.5)
        self.assertNotIn("mat_elev", fv.build_model(plans, {"sections": {"지하주차장": {"elevations": {"B1": 49.0}}}}, site=False)["buildings"][0])
        takeoff = [{"name": "지하주차장", "rows": [{"key": "FT", "foundation": {"area": 1000.0, "perimeter": 130.0, "thickness": 1.0}}]}]
        group, = works.earthwork(takeoff, [dict(deck, ground_elev=54.0)], None, {})["groups"]
        self.assertAlmostEqual(group["depth"], 54.0 - (45.5 - 1.0 - 0.06), places=2)


class PenthouseRoofTests(SimpleTestCase):
    def test_a_penthouse_without_a_plan_above_gets_the_slab_its_walls_close(self):
        m = model()
        roof = m["buildings"][0]["plans"][3]
        roof.update(slab_area=40.0, slab_approximate=False)  # the penthouse outline on the roof sheet
        m["buildings"][0]["plans"][2].update(slab_area=300.0, slab_approximate=False)
        takeoff = ft.build_takeoff(m)
        rows = {r["key"]: r for r in takeoff["buildings"][0]["rows"]}
        t = takeoff["assumptions"]["slab_thickness"]
        self.assertAlmostEqual(rows["R"]["concrete"]["slab"], 40.0 * t, places=2)
        self.assertTrue(any("옥탑 지붕 슬래브" in n for n in rows["R"]["notes"]))
        deck = {"buildings": [{"name": "지하주차장", "levels": [level("B1", 0, 3.5, "S-1"), level("R", 1, 3.0, "S-2")],
                               "plans": [plan(300, 20, outside=70.0, outline=70.0), dict(plan(80, 5, outside=40.0, outline=40.0), slab_area=40.0)]}]}
        rows = {r["key"]: r for r in ft.build_takeoff(deck)["buildings"][0]["rows"]}
        self.assertEqual(rows["R"]["concrete"].get("slab", 0.0), 0.0)  # a parking deck's roof is no penthouse
