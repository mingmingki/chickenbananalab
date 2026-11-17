"""Penthouse storeys: roof, penthouse and penthouse roof sheets stack as their own storeys."""
from django.test import SimpleTestCase

from core import quantity_floor_rebar as fr
from core import quantity_floor_takeoff as ft
from core import quantity_floor_views as fv
from core.test_quantity_floor_takeoff import level, plan


class PenthouseTitleTests(SimpleTestCase):
    def test_roof_group_titles_name_their_level(self):
        cases = [
            ("101동 지붕층 구조평면도", "지붕·옥탑", ["R"]),
            ("101동 옥탑층 구조평면도", "옥탑층", ["PH"]),
            ("101동 옥탑1층 구조평면도", "옥탑층", ["PH"]),      # never the first floor
            ("101동 옥탑지붕층 구조평면도", "옥탑지붕", ["PHR"]),
            ("101동 옥탑 지붕 구조평면도", "옥탑지붕", ["PHR"]),
            ("101동 지붕, 옥탑층, 옥탑지붕 평면도", "지붕·옥탑", ["R"]),  # one sheet: the lowest it names
            ("101동 옥탑층 및 옥탑지붕 구조평면도", "옥탑층", ["PH"]),
        ]
        for title, floor, keys in cases:
            found = fv.floor_of(title)
            self.assertEqual((found["building"], found["floor"], found["group"]), ("101동", floor, "roof"), title)
            self.assertEqual(fv._storey_keys(found), keys, title)

    def test_separate_sheets_stack_above_the_roof(self):
        plans = [{"floor": fv.floor_of(title), "sheet": sheet, "model": {"walls": []}} for title, sheet in (
            ("101동 지상 2층~지상 3층 구조평면도", "S-115"), ("101동 지붕층 구조평면도", "S-116"),
            ("101동 옥탑층 구조평면도", "S-117"), ("101동 옥탑지붕층 구조평면도", "S-118"))]
        table = {"sections": {"101동": {"elevations": {"2": 3.0, "R": 8.7, "PH": 11.7, "PHR": 14.5}}}}
        levels = fv.build_model(plans, table, site=False)["buildings"][0]["levels"]
        self.assertEqual([(l["key"], l["label"], l["plan"]) for l in levels],
                         [("2", "2층", 0), ("3", "3층", 0), ("R", "지붕·옥탑", 1), ("PH", "옥탑층", 2), ("PHR", "옥탑지붕", 3)])
        self.assertEqual([l["height"] for l in levels[2:]], [3.0, 2.8, fv.PARAPET_M])


class PenthouseTakeoffTests(SimpleTestCase):
    def model(self):
        plans = [plan(500, 30), plan(500, 30), plan(60, 8), plan(55, 2, faces=12, outside=12)]
        levels = [level("1", 0, sheet="S-2"), level("R", 1, 3.0, "S-4"), level("PH", 2, 2.8, "S-5"),
                  level("PHR", 3, fv.PARAPET_M, "S-6")]
        return {"buildings": [{"name": "101동", "levels": levels, "plans": plans}]}

    def rows(self):
        return {r["key"]: r for r in ft.build_takeoff(self.model())["buildings"][0]["rows"]}

    def test_the_roof_storey_carries_the_penthouse_slab_not_the_roofs_size(self):
        rows = self.rows()
        self.assertAlmostEqual(rows["R"]["measures"]["slab_area"], 60.0)
        self.assertAlmostEqual(rows["R"]["concrete"]["slab"], 60 * 0.21, places=2)
        self.assertTrue(any("평면 크기로 봤어요" in n for n in rows["R"]["notes"]))
        self.assertAlmostEqual(rows["PH"]["concrete"]["slab"], 55 * 0.21, places=2)
        self.assertAlmostEqual(rows["PH"]["concrete"]["wall"], 8 * (2.8 - 0.21), places=2)

    def test_the_penthouse_roof_keeps_only_its_parapets(self):
        top = self.rows()["PHR"]
        self.assertAlmostEqual(top["concrete"]["wall"], 2 * fv.PARAPET_M, places=2)  # full height: no slab above
        self.assertEqual(top["concrete"]["slab"], 0)
        self.assertTrue(any("난간벽" in n for n in top["notes"]))
        self.assertFalse(any("위층 평면이 없어" in n for n in top["notes"]))

    def test_rebar_follows_the_penthouse_storeys(self):
        rebar = fr.build_rebar(self.model())
        keys = [r["key"] for r in rebar["buildings"][0]["rows"]]
        self.assertEqual(keys[-3:], ["R", "PH", "PHR"])
        self.assertNotIn("옥탑 지붕", rebar["not_included"])
        top = rebar["buildings"][0]["rows"][-1]
        self.assertGreater(top["kg"]["wall"], 0)
