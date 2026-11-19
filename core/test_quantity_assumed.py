"""What a run decided on its own (임의로 정한 것): collected from the takeoff and the rebar, shown on
the cards and written to Excel, so a person sees every assumed value in one place."""
from pathlib import Path

import openpyxl
from django.test import SimpleTestCase
from openpyxl.styles import Alignment, Border, Font, PatternFill

from core import quantity_floor_rebar as fr
from core import quantity_floor_takeoff as ft
from core import quantity_views as views
from core import test_quantity_floor_takeoff as base
from core.quantity_assumed import Assumed

HOME = Path(__file__).with_name("templates").joinpath("core", "home.html")


class AssumedListTests(SimpleTestCase):
    def test_uses_gather_by_key_with_where_and_how_much(self):
        found = Assumed()
        for storey in ("3층", "4층", "5층", "6층"):
            found.add("slab_thickness", "슬래브 두께", "210mm", "101동", storey, 10.0, "㎥")
        found.add("slab_thickness", "슬래브 두께", "210mm", "102동", "3층", 5.0, "㎥")
        found.add("foundation_thickness", "기초 두께", "1.00m (가정)", "101동", "기초", 2.0, "㎥", counted=False)
        first, second = found.items()
        self.assertEqual(first["key"], "foundation_thickness")  # waiting for a person: listed first
        self.assertEqual((first["amount"], first["left_out"]), (2.0, 2.0))
        self.assertEqual(second["where"], ["101동 3층~6층 등 4개 층", "102동 3층"])
        self.assertEqual((second["amount"], second["left_out"]), (45.0, 0.0))


class TakeoffAssumedTests(SimpleTestCase):
    def items(self, **kw):
        return {a["key"]: a for a in ft.build_takeoff(base.FloorRuleTests().model(), **kw)["assumed"]}

    def test_the_takeoff_lists_what_it_assumed(self):
        found = self.items()
        self.assertEqual(found["slab_thickness"]["value"], "210mm")
        self.assertGreater(found["slab_thickness"]["amount"], 0)
        self.assertEqual(found["foundation_thickness"]["left_out"], found["foundation_thickness"]["amount"])
        self.assertIn("기초 두께 칸", found["foundation_thickness"]["change"])
        self.assertEqual(found["stair_rate"]["where"], ["101동 1층"])

    def test_what_a_person_gave_is_no_longer_assumed(self):
        self.assertNotIn("foundation_thickness", self.items(foundations={"101동": 1.2}))


class RebarAssumedTests(SimpleTestCase):
    def test_the_rebar_lists_default_bars_and_the_extras_it_added(self):
        rebar = fr.build_rebar(base.FloorRuleTests().model())
        found = {a["key"]: a for a in rebar["assumed"]}
        for key in ("plain_wall_bars", "slab_bars", "grade", "laps", "foundation_bars", "chair_bars", "stair_bars"):
            self.assertIn(key, found)
        self.assertEqual(found["slab_bars"]["unit"], "kg")
        self.assertEqual(found["grade"]["value"], "SD400")
        off = {a["key"] for a in fr.build_rebar(base.FloorRuleTests().model(), extras={"stair": False, "chair": False})["assumed"]}
        self.assertFalse({"stair_bars", "chair_bars"} & off)


class AssumedExcelAndPageTests(SimpleTestCase):
    def test_the_excel_sheet_lists_both_steps(self):
        model = base.FloorRuleTests().model()
        workbook = openpyxl.Workbook()
        thin = Border()
        views._write_assumed_sheet(workbook.active, ft.build_takeoff(model), fr.build_rebar(model), PatternFill(), Font(), Font(),
                                   Alignment(), thin)
        rows = [[c.value for c in row] for row in workbook.active.iter_rows()]
        self.assertEqual(rows[0][:3], ["구분", "항목", "정한 값"])
        groups = {row[0] for row in rows[1:]}
        self.assertEqual(groups, {"콘크리트", "철근"})
        self.assertTrue(any(row[1] == "기초 두께" and row[5] for row in rows[1:]))  # its part left out of the totals

    def test_the_cards_show_the_list_and_redraw_it_after_a_recalculation(self):
        html = HOME.read_text(encoding="utf-8")
        self.assertIn("function assumedBox(items, what)", html)
        self.assertIn('var takeoffAssumed = assumedBox(takeoff.assumed, "콘크리트를");', html)
        self.assertIn('var rebarAssumed = assumedBox(rebar.assumed, "철근을");', html)
        self.assertIn("takeoff.assumed = fresh.takeoff.assumed;", html)
