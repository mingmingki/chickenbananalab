"""Foundation schedules: mat thickness and bars, wall footings and isolated footing bars from the
foundation schedule (기초 일람표), read from CAD and laid on the foundation storey."""
import math

from django.test import SimpleTestCase

from core import quantity_calc as qc
from core import quantity_floor_rebar as fr
from core import quantity_floor_takeoff as ft
from core import quantity_schedules as sch
from core import test_quantity_floor_takeoff as base
from core.test_quantity_floor_takeoff import level, plan


def text(value, centre, y, h=100):
    return {"text": value, "x": centre - sch._width(value, h) / 2, "y": y, "h": h, "layer": "TEXT"}


def mat_schedule():
    """부호 | 두께 | 상부근 (X방향 / Y방향) | 하부근 (X방향 / Y방향) | 보강근 | 비고, two mats."""
    texts = [text("부호", 0, 1000), text("두께", 1000, 1000), text("상부근", 2500, 1000), text("하부근", 4500, 1000),
             text("보강근", 6000, 1000), text("비고", 7000, 1000),
             text("X방향", 2000, 850), text("Y방향", 3000, 850), text("X방향", 4000, 850), text("Y방향", 5000, 850)]
    for i, (mark, thick, top, bottom, extra) in enumerate((("MF1", "1,000", "HD19@300", "HD22@300", "HD22@600"),
                                                          ("MF2", "1,500", "HD22@300", "HD25@300", "-"))):
        y = 650 - i * 200
        texts += [text(mark, 0, y), text(thick, 1000, y), text(top, 2000, y), text(top, 3000, y),
                  text(bottom, 4000, y), text(bottom, 5000, y), text(extra, 6000, y)]
    return texts


def wall_footing_schedule():
    return [text("부호", 0, 1000), text("B", 1000, 1000), text("D", 2000, 1000), text("하부근", 3000, 1000),
            text("배력근", 4000, 1000), text("비고", 5000, 1000),
            text("WF1", 0, 800), text("1,200", 1000, 800), text("600", 2000, 800), text("HD16@200", 3000, 800),
            text("HD13@300", 4000, 800)]


class FoundationScheduleReadingTests(SimpleTestCase):
    def test_a_mat_table_gives_bars_by_layer_and_direction_on_a_foundation_sheet(self):
        tables = {t["mark"]: t for t in sch.read_tables(mat_schedule(), foundation=True)}
        self.assertEqual(set(tables), {"MF1", "MF2"})
        self.assertEqual(tables["MF1"]["category"], "foundations")
        self.assertEqual(tables["MF1"]["rows"][0]["values"],
                         {"thickness": "1,000", "top_bar_x": "HD19@300", "top_bar_y": "HD19@300",
                          "bottom_bar_x": "HD22@300", "bottom_bar_y": "HD22@300", "extra_bar": "HD22@600"})

    def test_other_sheets_read_as_before(self):
        self.assertEqual(sch.read_tables(mat_schedule()), [])  # two header labels only: not a table elsewhere
        plain = {t["mark"]: t for t in sch.read_tables(wall_footing_schedule())}
        self.assertEqual(plain["WF1"]["rows"][0]["values"],
                         {"col_b": "1,200", "depth": "600", "col_하부근": "HD16@200", "col_배력근": "HD13@300"})

    def test_a_wall_footing_table_gives_width_depth_and_both_bars(self):
        table = sch.read_tables(wall_footing_schedule(), foundation=True)[0]
        self.assertEqual((table["mark"], table["category"]), ("WF1", "foundations"))
        self.assertEqual(table["rows"][0]["values"],
                         {"width": "1,200", "depth": "600", "bottom_bar": "HD16@200", "dist_bar": "HD13@300"})

    def test_a_mat_drawn_mark_by_mark_takes_the_foundation_category_and_its_thickness_row(self):
        texts = [text("부호", 0, 1000), text("MF1", 2000, 1000), text("MF2", 4000, 1000),
                 text("두께", 0, 800), text("1000", 2000, 800), text("1500", 4000, 800),
                 text("상부근", 0, 600), text("HD19@300", 2000, 600), text("HD22@300", 4000, 600),
                 text("하부근", 0, 400), text("HD22@300", 2000, 400), text("HD25@300", 4000, 400)]
        tables = {t["mark"]: t for t in sch.read_tables(texts, foundation=True)}
        self.assertEqual(tables["MF2"]["category"], "foundations")
        self.assertEqual(tables["MF2"]["rows"][0]["values"], {"thickness": "1500", "top_bar": "HD22@300", "bottom_bar": "HD25@300"})
        self.assertEqual({t["category"] for t in sch.read_tables(texts)}, {"beams"})  # unchanged elsewhere
        # a grade beam table (지중보) on the same sheet keeps its stirrups and stays a beam
        beam = texts + [text("늑근", 0, 200), text("HD10@200", 2000, 200), text("HD10@200", 4000, 200)]
        self.assertEqual({t["category"] for t in sch.read_tables(beam, foundation=True)}, {"beams"})

    def test_sheets_titled_as_foundations_read_this_way(self):
        self.assertTrue(sch.is_foundation_title("기초 일람표"))
        self.assertTrue(sch.is_foundation_title("MAT FOUNDATION SCHEDULE"))
        self.assertFalse(sch.is_foundation_title("벽체 일람표"))
        self.assertEqual(len(sch.read_sheet_content(mat_schedule(), "기초 일람표")["tables"]), 2)
        self.assertEqual(sch.read_sheet_content(mat_schedule(), "슬래브 일람표")["tables"], [])

    def test_a_mat_mark_carrying_its_thickness_gives_the_thickness(self):
        specs = sch.read_specs([text("101MAT_1.2", 0, 0), text("HD19@300 (T/B)", 0, 500)])
        self.assertEqual({s["kind"] for s in specs}, {"thickness", "rebar"})
        links = {"plans": [{"drawing_number": "S-501", "kind": "floor_plan", "section": "101동", "levels": ["FDN"], "specs": specs}]}
        self.assertEqual(ft.foundation_specs(links, "101동"), (1.2, "HD19@300 (T/B)"))


MAT_ROW = {"thickness": "1,000", "top_bar_x": "HD19@300", "top_bar_y": "HD19@300",
           "bottom_bar_x": "HD22@300", "bottom_bar_y": "HD22@300"}


def links_with(marks, rows, plan_specs=(), section="101동", schedule_area="building"):
    return {"plans": [{"drawing_number": "S-501", "kind": "floor_plan", "section": section, "levels": ["FDN"],
                       "specs": list(plan_specs)},
                      {"drawing_number": "S-601", "kind": "schedule", "section": None, "levels": None, "area": schedule_area}],
            "schedule_rows": [[{"values": values}] for values in rows], "marks": marks}


def mark(name, row, count=0):
    return {"category": "foundations", "mark": name, "specs": [{"plan": 1, "rows": row}],
            "occurrences": [{"plan": 0, "count": count}] if count else []}


def ft_row(model, links, **kw):
    for building in ft.build_takeoff(model, links, **kw)["buildings"]:
        for row in building["rows"]:
            if row["key"] == "FT":
                yield building["name"], row


def rebar_ft(model, links, extras=None):
    out = {}
    for building in fr.build_rebar(model, links, extras=extras)["buildings"]:
        for row in building["rows"]:
            if row["key"] == "FT":
                out[building["name"]] = row
    return out


def kg(size, length):
    return qc.rebar_weight(size, length)[0]


class MatScheduleTests(SimpleTestCase):
    plan_text = ({"kind": "thickness", "text": "THK. 800"}, {"kind": "rebar", "text": "HD16@300(T/B)"})

    def test_a_mat_labelled_on_the_plan_gives_the_thickness_and_its_bars(self):
        links = links_with([mark("MF1", 0, count=2)], [MAT_ROW], self.plan_text)
        (_name, row), = ft_row(base.FloorRuleTests().model(), links)
        self.assertEqual((row["foundation"]["source"], row["foundation"]["thickness"]), ("schedule", 1.0))
        self.assertFalse(row["approximate"])
        self.assertAlmostEqual(row["concrete"]["foundation"], 900 * 1.0, places=1)
        self.assertIn("기초 일람표", row["notes"][0])
        rebar = rebar_ft(base.FloorRuleTests().model(), links, extras={"chair": False})["101동"]
        self.assertEqual(set(rebar["by_bar"]), {"D19|SD400", "D22|SD400"})  # the plan's HD16 is not used
        side, laps = 30.0, 2
        for size, ways in (("D19", 2), ("D22", 2)):  # one way each per column, top X+Y and bottom X+Y
            lap = qc.get_splice_length(size, None, category="기초")[0]
            expected = kg(size, ways * 900 / 0.3 * (1 + laps * lap / side))
            self.assertAlmostEqual(rebar["by_bar"][size + "|SD400"], expected, delta=1.0)
        self.assertTrue(any("기초 일람표 값" in n for n in rebar["notes"]))

    def test_the_only_mat_in_the_schedules_follows_the_plans_own_thickness_but_gives_its_bars(self):
        links = links_with([mark("MF1", 0)], [MAT_ROW], self.plan_text)
        (_name, row), = ft_row(base.FloorRuleTests().model(), links)
        self.assertEqual((row["foundation"]["source"], row["foundation"]["thickness"]), ("drawing", 0.8))
        self.assertTrue(any("일람표에서 골랐어요" in n for n in row["notes"]))
        rebar = rebar_ft(base.FloorRuleTests().model(), links, extras={"chair": False})["101동"]
        self.assertEqual(set(rebar["by_bar"]), {"D19|SD400", "D22|SD400"})
        links = links_with([mark("MF1", 0)], [MAT_ROW])  # no plan text: the schedule's thickness
        (_name, row), = ft_row(base.FloorRuleTests().model(), links)
        self.assertEqual((row["foundation"]["source"], row["foundation"]["thickness"]), ("schedule", 1.0))

    def test_several_unlabelled_mats_are_not_guessed(self):
        links = links_with([mark("MF1", 0), mark("MF2", 1)], [MAT_ROW, dict(MAT_ROW, thickness="1,500")], self.plan_text)
        (_name, row), = ft_row(base.FloorRuleTests().model(), links)
        self.assertEqual(row["foundation"]["source"], "drawing")
        self.assertEqual(row["foundation"]["mats"], [])
        self.assertTrue(any("MF1, MF2" in n and "쓰지 않았어요" in n for n in row["notes"]))
        rebar = rebar_ft(base.FloorRuleTests().model(), links, extras={"chair": False})["101동"]
        self.assertEqual(set(rebar["by_bar"]), {"D16|SD400"})  # the plan text, as before

    def test_two_labelled_mats_share_the_area_by_their_labels(self):
        links = links_with([mark("MF1", 0, count=3), mark("MF2", 1, count=1)], [MAT_ROW, dict(MAT_ROW, thickness="1,400")])
        (_name, row), = ft_row(base.FloorRuleTests().model(), links)
        self.assertAlmostEqual(row["foundation"]["thickness"], 0.75 * 1.0 + 0.25 * 1.4, places=3)
        self.assertEqual([m["share"] for m in row["foundation"]["mats"]], [0.75, 0.25])

    def test_a_person_still_has_the_last_word(self):
        links = links_with([mark("MF1", 0, count=2)], [MAT_ROW])
        (_name, row), = ft_row(base.FloorRuleTests().model(), links, foundations={"101동": 1.3})
        self.assertEqual((row["foundation"]["source"], row["foundation"]["thickness"]), ("person", 1.3))

    def test_reinforcing_bars_in_the_schedule_cover_the_mat_and_say_so(self):
        links = links_with([mark("MF1", 0, count=1)], [dict(MAT_ROW, extra_bar="HD22@600")])
        rebar = rebar_ft(base.FloorRuleTests().model(), links, extras={"chair": False})["101동"]
        lap = qc.get_splice_length("D22", None, category="기초")[0]
        layers = kg("D22", 2 * 900 / 0.3 * (1 + 2 * lap / 30)) + kg("D22", 2 * 900 / 0.6 * (1 + 2 * lap / 30))
        self.assertAlmostEqual(rebar["by_bar"]["D22|SD400"], layers, delta=1.0)
        self.assertTrue(any("보강근" in n for n in rebar["notes"]))


WF_ROW = {"width": "1,200", "depth": "600", "bottom_bar": "HD16@200", "dist_bar": "HD13@300"}


def parking_model():
    parking = {"name": "지하주차장", "levels": [level("B2", 0, 3.5, "P-1"), level("B1", 1, 3.5, "P-2")],
               "plans": [plan(3000, 80, faces=500, outside=240), plan(3000, 80, faces=500, outside=240)]}
    return {"buildings": [base.FloorRuleTests().model()["buildings"][0], parking]}


class WallFootingTests(SimpleTestCase):
    def links(self, count=0):
        links = links_with([mark("WF1", 0, count=count)], [WF_ROW],
                           ({"kind": "thickness", "text": "THK. 400"}, {"kind": "rebar", "text": "HD19@300(T/B)"}),
                           section="지하주차장", schedule_area="parking")
        return links

    def test_unlabelled_wall_footings_run_under_the_parking_decks_outer_walls(self):
        rows = dict(ft_row(parking_model(), self.links()))
        row = rows["지하주차장"]
        self.assertEqual((row["foundation"]["source"], row["foundation"]["thickness"]), ("drawing", 0.4))
        wall_t = 2 * 80 / 500
        inside = (1.2 + wall_t) / 2
        strip = 240 * (1.2 * 0.6 - inside * 0.4)  # the part under the slab outline is in the slab already
        self.assertAlmostEqual(row["concrete"]["foundation"], 3000 * 0.4 + strip, places=1)
        footing, = row["foundation"]["wall_footings"]
        self.assertEqual((footing["mark"], footing["length"], footing["width"], footing["depth"]), ("WF1", 240.0, 1.2, 0.6))
        self.assertTrue(any("줄기초 WF1" in n and "확인해 주세요" in n for n in row["notes"]))
        self.assertEqual(rows["101동"]["foundation"]["wall_footings"], [])  # the tower stands on its mat

    def test_wall_footing_bars_from_its_row(self):
        rebar = rebar_ft(parking_model(), self.links(), extras={"chair": False})["지하주차장"]
        across = kg("D16", (240 / 0.2 + 1) * (1.2 - 2 * fr.COVER_M + fr.HOOK_M))
        self.assertAlmostEqual(rebar["by_bar"]["D16|SD400"], across, delta=0.5)
        self.assertIn("D13|SD400", rebar["by_bar"])
        self.assertFalse(any("줄기초" in n and "못 읽어" in n for n in rebar["notes"]))
        self.assertNotIn("줄기초", fr.build_rebar(parking_model(), self.links())["not_included"])

    def test_wall_footings_labelled_on_a_plan_stay_with_that_building(self):
        links = self.links(count=4)
        links["plans"][0]["section"] = "101동"  # labelled on the tower's foundation plan only
        rows = dict(ft_row(parking_model(), links))
        self.assertEqual([w["placed"] for w in rows["101동"]["foundation"]["wall_footings"]], [4])
        self.assertEqual(rows["지하주차장"]["foundation"]["wall_footings"], [])

    def test_open_outlines_fall_back_to_the_outline_and_say_so(self):
        model = parking_model()
        model["buildings"][1]["plans"][0]["measure"]["outside_faces"] = 0
        model["buildings"][1]["plans"][0]["slab_polygon"] = [[0, 0], [60, 0], [60, 50], [0, 50]]
        footing, = dict(ft_row(model, self.links()))["지하주차장"]["foundation"]["wall_footings"]
        self.assertEqual(footing["length"], 220.0)

    def test_a_wall_footing_row_without_bars_uses_the_defaults_and_says_so(self):
        links = self.links()
        links["schedule_rows"] = [[{"values": {"width": "1,200", "depth": "600"}}]]
        rebar = rebar_ft(parking_model(), links, extras={"chair": False})["지하주차장"]
        self.assertTrue(any("줄기초 WF1 하부근을 못 읽어" in n for n in rebar["notes"]))
        self.assertTrue(any("배력근을 못 읽어" in n for n in rebar["notes"]))


class IsolatedFootingBarTests(SimpleTestCase):
    def test_isolated_footings_take_their_schedule_bars(self):
        row = {"length_x": "2,000", "length_y": "2,000", "depth": "600", "rebar_1": "HD13@200", "rebar_2": "HD13@200"}
        links = links_with([mark("F1", 0, count=10)], [row], ({"kind": "rebar", "text": "HD19@300(T/B)"},))
        rebar = rebar_ft(base.FloorRuleTests().model(), links, extras={"chair": False})["101동"]
        per = 2 * qc._spacing_bar_count(2.0, 0.2) * 2.0
        self.assertAlmostEqual(rebar["by_bar"]["D13|SD400"], kg("D13", 10 * per), delta=0.5)
        self.assertFalse(any("독립기초" in n for n in rebar["notes"]))
        row.pop("rebar_1"), row.pop("rebar_2")
        rebar = rebar_ft(base.FloorRuleTests().model(), links, extras={"chair": False})["101동"]
        self.assertTrue(any("독립기초 F1는 일람표 배근을 못 읽어" in n for n in rebar["notes"]))

    def test_footing_kinds(self):
        self.assertEqual(ft.footing_kind("F1", {"length_x": "2000", "length_y": "2000", "depth": "600"}), "isolated")
        self.assertEqual(ft.footing_kind("WF1", {"size": "1200 X 600"}), "wall")
        self.assertEqual(ft.footing_kind("WF1", {"width": "1200"}), None)  # no depth: not used
        self.assertEqual(ft.footing_kind("MF1", {}), "mat")
        self.assertEqual(ft.footing_kind("FS1", {"thickness": "300"}), "mat")
        self.assertEqual(ft.footing_kind("F2", {"depth": "600"}), None)  # an isolated footing missing its size
        self.assertEqual(math.isclose(ft._value_m({"width": "B=1,200"}, "width"), 1.2), True)


class FoundationLinkPipelineTests(SimpleTestCase):
    """From drawn CAD texts: the foundation schedule sheet and the foundation plan's mat label."""

    def test_a_drawn_mat_schedule_and_plan_label_reach_the_foundation_storey(self):
        from core import quantity_member_links as member_links
        from core import quantity_sheet_identity as sid
        from core.test_quantity_member_links import add_marks
        from core.test_quantity_sheet_identity import evidence, form_doc, index_host, xref_host

        schedules = xref_host([("S-701", "기초 일람표")])
        msp = schedules.modelspace()
        for item in mat_schedule():  # the same table, drawn at 3 mm text inside the frame
            centre = 100 + (item["x"] + sch._width(item["text"], 100) / 2) * 0.03
            msp.add_text(item["text"], dxfattribs={"insert": (centre - sch._width(item["text"], 3) / 2, 300 + item["y"] * 0.03),
                                                   "height": 3, "layer": "0_STR_TEXT"})
        plans = xref_host([("S-501", "101동 기초 구조평면도")])
        add_marks(plans, 0, ["MF1", "MF1"], "0_STR_FDN_NAME")
        index = index_host([[("S-501", "101동 기초 구조평면도", "1/200"), ("S-701", "기초 일람표", "NONE")]])
        ev = evidence(**{"XR-FORM-A(A3).dxf": form_doc()}, schedules=schedules, plans=plans, index=index)
        parsed = {name: {"source_member_sha": "sha-" + name} for name in ev}
        sid.attach_sheet_identity(parsed, ev)
        matrix = member_links.build_member_links(parsed)
        mf1 = next(m for m in matrix["marks"] if m["mark"] == "MF1")
        self.assertEqual(mf1["category"], "foundations")
        self.assertEqual(sum(o["count"] for o in mf1["occurrences"]), 2)
        mats, placed, _note = ft.mat_parts(matrix, "101동", "tower")
        self.assertTrue(placed)
        self.assertEqual([(m["mark"], m["thickness"]) for m in mats], [("MF1", 1.0)])
        self.assertEqual(mats[0]["bars"], {"top_bar_x": "HD19@300", "top_bar_y": "HD19@300", "bottom_bar_x": "HD22@300",
                                           "bottom_bar_y": "HD22@300", "extra_bar": "HD22@600"})
