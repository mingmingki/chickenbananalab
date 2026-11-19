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
        for key in ("plain_wall_bars", "slab_bars", "laps", "foundation_bars", "chair_bars", "stair_bars"):
            self.assertIn(key, found)
        self.assertEqual(found["slab_bars"]["unit"], "kg")
        self.assertNotIn("grade", found)  # only its own defaults: their grade is part of each default
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
        self.assertIn("function assumedBox(items, what, closed)", html)
        self.assertIn('var takeoffAssumed = assumedBox(takeoff.assumed, "콘크리트를");', html)
        self.assertIn('var rebarAssumed = assumedBox(rebar.assumed, "철근을");', html)
        self.assertIn("Object.keys(fresh || {}).forEach(function (k) { takeoff[k] = fresh[k]; });", html)


class GradeAssumedTests(SimpleTestCase):
    def test_only_drawn_bars_without_a_grade_count(self):
        tally = fr._Tally(None)
        tally.add("wall", "D10@300", 10, 100.0)        # the plain wall default
        self.assertEqual((tally.guessed_kg, tally.guessed_grade), (0.0, False))
        tally.add("wall", "HD13@200", 13, 100.0)       # drawn, with its grade
        self.assertEqual(tally.guessed_kg, 0.0)
        tally.add("wall", "D13@200", 13, 100.0)        # drawn without one
        self.assertGreater(tally.guessed_kg, 0)
        used = []
        tally.assumed_common(used)
        self.assertEqual([u[0] for u in used], ["grade"])


# ── values a person sets in place of an assumption ──

import json  # noqa: E402

from django.contrib.auth import get_user_model  # noqa: E402
from django.test import TestCase  # noqa: E402

from core import quantity_assumed as qa  # noqa: E402
from core import test_quantity_foundation_schedule as fdn  # noqa: E402
from core.test_quantity_cad_path import state_db  # noqa: E402


class CleanOverridesTests(SimpleTestCase):
    def test_only_known_values_in_their_bounds_are_kept(self):
        clean = qa.clean_overrides({"slab_thickness": "250", "beam_depth": 9000, "plain_wall_bars": " hd13 @ 200 ",
                                    "slab_bars": "D10@20", "grade": "SD700", "mat_extra_bars": "no",
                                    "wall_footing_place": False, "stair_bars": True, "nonsense": 1})
        self.assertEqual(clean, {"slab_thickness": 250.0, "plain_wall_bars": "HD13@200", "wall_footing_place": False})
        self.assertEqual(qa.clean_overrides("not a dict"), {})


class TakeoffOverrideTests(SimpleTestCase):
    def rows(self, **kw):
        return {r["key"]: r for r in ft.build_takeoff(base.FloorRuleTests().model(), **kw)["buildings"][0]["rows"]}

    def test_a_set_slab_thickness_replaces_the_assumed_one_and_says_so(self):
        plain, thick = self.rows(), self.rows(overrides={"slab_thickness": 250})
        self.assertAlmostEqual(thick["1"]["concrete"]["slab"], plain["1"]["concrete"]["slab"] / 0.21 * 0.25, places=1)
        item = {a["key"]: a for a in ft.build_takeoff(base.FloorRuleTests().model(), overrides={"slab_thickness": 250})["assumed"]}["slab_thickness"]
        self.assertTrue(item["set"])
        self.assertIn("(입력값)", item["value"])
        self.assertEqual(item["edit"]["value"], 250)

    def test_other_jobs_values_are_the_starting_point_but_still_listed(self):
        item = {a["key"]: a for a in ft.build_takeoff(base.FloorRuleTests().model(), learned={"override:slab_thickness": 230})["assumed"]}["slab_thickness"]
        self.assertEqual((item["value"], item["set"], item["edit"]["value"]), ("230mm (다른 현장 입력값)", False, 230))

    def test_an_unlabelled_wall_footing_can_be_left_out(self):
        links = fdn.WallFootingTests().links()
        rows = dict(fdn.ft_row(fdn.parking_model(), links, overrides={"wall_footing_place": False}))
        self.assertEqual(rows["지하주차장"]["foundation"]["wall_footings"], [])
        self.assertAlmostEqual(rows["지하주차장"]["concrete"]["foundation"], 3000 * 0.4, places=1)
        item = {a["key"]: a for a in ft.build_takeoff(fdn.parking_model(), links, overrides={"wall_footing_place": False})["assumed"]}["wall_footing_place"]
        self.assertEqual((item["set"], item["edit"]["value"]), (True, False))

    def test_a_value_used_nowhere_still_shows_so_it_can_be_changed_back(self):
        item = {a["key"]: a for a in ft.build_takeoff(base.FloorRuleTests().model(), overrides={"parapet": 1.5})["assumed"]}["parapet"]
        self.assertTrue(item["set"])
        self.assertIn("쓰인 곳이 없어요", item["value"])


class RebarOverrideTests(SimpleTestCase):
    def build(self, overrides):
        return fr.build_rebar(base.FloorRuleTests().model(), overrides=overrides)

    def test_plain_wall_slab_and_stair_values(self):
        plain = self.build({})
        set_ = self.build({"plain_wall_bars": "D13@200", "slab_bars": "D13@200", "stair_bars": 50})
        kinds = lambda r: {k: sum(row["kg"][k] for b in r["buildings"] for row in b["rows"]) for k in ("wall", "slab", "stair")}
        before, after = kinds(plain), kinds(set_)
        self.assertGreater(after["wall"], before["wall"])
        self.assertGreater(after["slab"], before["slab"])
        self.assertAlmostEqual(after["stair"], before["stair"] / 35 * 50, places=0)
        items = {a["key"]: a for a in set_["assumed"]}
        self.assertTrue(all(items[k]["set"] for k in ("plain_wall_bars", "slab_bars", "stair_bars")))

    def test_the_grade_of_unmarked_bars(self):
        rebar = self.build({"grade": "SD500"})
        self.assertTrue(any(key.endswith("|SD500") for key in rebar["by_bar"]))
        self.assertFalse(any(key.endswith("|SD400") for key in rebar["by_bar"]))
        self.assertEqual({a["key"]: a for a in rebar["assumed"]}["grade"]["edit"]["value"], "SD500")

    def test_mat_bars_and_its_reinforcing_bars(self):
        links = fdn.links_with([fdn.mark("MF1", 0, count=1)], [dict(fdn.MAT_ROW, extra_bar="HD22@600")])
        model = base.FloorRuleTests().model()
        on = fr.build_rebar(model, links, extras={"chair": False})
        off = fr.build_rebar(model, links, extras={"chair": False}, overrides={"mat_extra_bars": False})
        kg = lambda r: r["buildings"][0]["rows"][0]["kg"]["foundation"]
        self.assertLess(kg(off), kg(on))
        self.assertTrue({a["key"]: a for a in off["assumed"]}["mat_extra_bars"]["set"])
        default = fr.build_rebar(model, extras={"chair": False}, overrides={"foundation_bars": "D19@250"})
        self.assertIn("D19|SD400", default["buildings"][0]["rows"][0]["by_bar"])


class OverrideApiTests(TestCase):
    def setUp(self):
        state_db(self)
        self.staff = get_user_model().objects.create_user("staff", password="staff-pass-1", is_staff=True)
        self.client.force_login(self.staff)
        views._quantity_state().begin_job("fv-1", self.staff.pk, "api_quantity_floor_views", "x")
        views._result_set("fv-1", {"ok": True, "results": {"floor_views": {"model": base.FloorRuleTests().model()}}})

    def post(self, url, body):
        return self.client.post(url, json.dumps(body), content_type="application/json")

    def test_values_are_kept_with_the_job_used_by_takeoff_and_rebar_and_learned(self):
        saved = self.post("/api/quantity/floor-edits/", {"floor_job_id": "fv-1", "overrides": {"slab_thickness": 250, "bad": 1}}).json()
        self.assertEqual(saved["overrides"], {"slab_thickness": 250.0})
        self.assertEqual(self.post("/api/quantity/floor-edits/", {"floor_job_id": "fv-1"}).json()["overrides"], {"slab_thickness": 250.0})
        takeoff = self.post("/api/quantity/floor-takeoff/", {"floor_job_id": "fv-1"}).json()["takeoff"]
        self.assertEqual(takeoff["overrides"], {"slab_thickness": 250.0})
        rebar = self.post("/api/quantity/floor-rebar/", {"floor_job_id": "fv-1", "overrides": {"grade": "SD600"}}).json()["rebar"]
        self.assertTrue(any(key.endswith("|SD600") for key in rebar["by_bar"]))
        self.assertEqual(views._learned_values("another-job").get("override:slab_thickness"), 250.0)
        # saving the foundations alone keeps the values
        self.post("/api/quantity/floor-edits/", {"floor_job_id": "fv-1", "foundations": {"101동": 1.1}})
        self.assertEqual(self.post("/api/quantity/floor-edits/", {"floor_job_id": "fv-1"}).json()["overrides"], {"slab_thickness": 250.0})


class OverridePageTests(SimpleTestCase):
    def test_the_lists_take_edits_and_one_recalculation_redraws_every_card(self):
        html = HOME.read_text(encoding="utf-8")
        self.assertIn('data-override="\' + esc(a.key) + \'"', html)
        self.assertIn("function applyOverrides(changes, status)", html)
        self.assertIn("async function recalcQuantities(status, saved)", html)
        self.assertIn("state.quantityCards.rebar = { data: rebar, refresh: function (fresh) {", html)
        self.assertIn("state.quantityCards.summary = { refresh: function () {", html)
        self.assertIn('[["go", "결과 요약 보기 →", true]]', html)  # the card stays until the person moves on
        self.assertIn("function quantityFlash(before, after)", html)
