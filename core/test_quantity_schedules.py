"""Schedule tables, plan notes, labels kept in blocks and their member comparisons (CAD evidence only)."""
import io
import json
import zipfile

import ezdxf
from django.test import RequestFactory, SimpleTestCase

from core import quantity_member_links as links
from core import quantity_schedules as qs
from core import quantity_sheet_identity as sid
from core.test_quantity_sheet_identity import FRAME_W, bound_host, evidence, form_doc, index_host, xref_host


def put(target, frame, items, layer="0_STR_TEXT", height=3.0):
    for text, x, y in items:
        target.add_text(text, dxfattribs={"insert": (frame * FRAME_W + x, y), "height": height, "layer": layer})


def pasted(doc, name, frame, items, layer="0"):
    """Texts kept inside a block placed on a frame, as when plan content is pasted as a block."""
    block = doc.blocks.new(name)
    for text, x, y, text_layer in items:
        block.add_text(text, dxfattribs={"insert": (x, y), "height": 3.0, "layer": text_layer})
    doc.modelspace().add_blockref(name, (frame * FRAME_W, 0), dxfattribs={"layer": layer})


def labels(count, mark, x, y):
    return [(mark, x + 12 * i, y) for i in range(count)]


SCHEDULES = [("S-611", "주차장 기둥 일람표"), ("S-631", "주차장 보 일람표"), ("S-331", "아파트 기둥 일람표"),
             ("S-311", "101동 벽체 일람표-1"), ("S-601", "주차장 기초 일람표")]
PLANS = [("S-112", "101동 지하 1층 구조평면도"), ("S-402", "지하1층 지하주차장 구조평면도"), ("S-403", "지하주차장 지붕 구조평면도"),
         ("S-201", "101동 지하 2층 기초 구조평면도"), ("S-501", "지하주차장 기초 구조평면도")]
WALL_HEADER = (("층별", 60), ("fck(MPa)", 90), ("두께(mm)", 125), ("수직근", 160), ("수평근", 195), ("비고", 230))


def schedules_doc():
    doc = xref_host(SCHEDULES)
    msp = doc.modelspace()
    # Column schedule: row labels kept in a block, marks and values in model space.
    pasted(doc, "_COL-TABLE", 0, [("부     호", 70, 500, "0_STR_TEXT"), ("SIZE", 72, 440, "0_STR_TEXT"),
                                  ("MAIN BAR", 68, 430, "0_STR_TEXT"), ("HOOP BAR(MID)", 62, 420, "0_STR_TEXT"),
                                  ("HOOP BAR(END)", 62, 410, "0_STR_TEXT")])
    put(msp, 0, [("C1", 150, 500), ("C2", 250, 500), ("400 X 900", 145, 440), ("500 X 500", 245, 440),
                 ("12 - D19", 146, 430), ("14 - D19", 246, 430), ("D10@300", 146, 420), ("D10@300", 246, 420),
                 ("D10@300", 146, 410), ("D10@150", 246, 410)])
    # Beam schedule: level-prefixed marks; the position row splits a mark into sub-columns.
    put(msp, 1, [("부     호", 70, 500), ("-1G1", 180, 500), ("RB1", 380, 500),
                 ("양단부", 130, 490), ("중앙부", 230, 490), ("전단면", 380, 490),
                 ("SIZE", 72, 440), ("500 X 600", 125, 440), ("500 X 600", 225, 440), ("600 X 900", 375, 440),
                 ("상부근", 70, 430), ("4-D16", 130, 430), ("3-D16", 230, 430), ("4-D19", 380, 430),
                 ("하부근", 70, 420), ("3-D16", 130, 420), ("4-D16", 230, 420), ("3-D19", 380, 420),
                 ("늑근", 70, 410), ("D10 @ 250", 125, 410), ("D10 @ 250", 225, 410), ("D10 @ 300", 375, 410)])
    # Apartment beams in a frame titled as a column schedule.
    put(msp, 2, [("부     호", 70, 500), ("AG1", 180, 500), ("ALL", 180, 490), ("SIZE", 72, 440), ("500 X 900", 175, 440),
                 ("상부근", 70, 430), ("4-D19", 180, 430), ("하부근", 70, 420), ("3-D19", 180, 420),
                 ("늑근", 70, 410), ("D10 @ 300", 175, 410)])
    # Wall schedule: a header row per mark; "↑" in the top row continues the value below.
    for dx, mark, rows in ((0, "CW1", [("1F - 13F", "30", "180", "↑", "↑", "-"),
                                       ("B2F - B1F", "30", "200", "D10@450", "D10@350", "-")]),
                           (270, "CW2", [("B1F - 13F", "30", "250", "D13@300", "D13@300", "-")])):
        put(msp, 3, [(mark, 100 + dx, 520)] + [(label, x + dx, 510) for label, x in WALL_HEADER])
        for r, values in enumerate(rows):
            put(msp, 3, [(v, x + dx, 500 - 7 * r) for v, x in zip(values, (57, 93, 128, 162, 197, 233))])
    # Footing schedule: a stacked header (FOOTING SIZE over D/Lx/Ly, RE-BAR SCHEDULE over 1/2).
    put(msp, 4, [("FOOTING SIZE", 150, 520), ("RE-BAR SCHEDULE", 300, 520), ("H", 420, 520),
                 ("NO.", 60, 513), ("TYPE", 100, 513), ("REMARK", 470, 513),
                 ("D(mm)", 140, 506), ("Lx(mm)", 175, 506), ("Ly(mm)", 210, 506), ("1", 310, 506), ("2", 350, 506),
                 ("(mm)", 418, 506),
                 ("F1", 62, 495), ("A", 102, 495), ("600", 142, 495), ("3,400", 176, 495), ("3,400", 211, 495),
                 ("D16 @ 300", 300, 495), ("D16 @ 300", 340, 495), ("-", 420, 495), ("-", 472, 495)])
    return doc


def plans_doc():
    doc = xref_host(PLANS)
    msp = doc.modelspace()
    # 101동 B1: the parking part is pasted as a block and drawn for reference only (note 2).
    pasted(doc, "A$C0001", 0, [(t, x, y, "0_STR_GIRDER_NAME") for t, x, y in labels(5, "G1", 100, 300)]
           + [(t, x, y, "0_STR_COL_NAME") for t, x, y in labels(2, "C2", 100, 280)]
           + [("1. 재료강도는 구조일반사항 참조", 650, 400, "0_STR_TEXT_NOTE"),
              ("2. 주차장 부분은 \"주차장 구조", 650, 393, "0_STR_TEXT_NOTE"),
              ("평면도\" 참고할 것.", 650, 386, "0_STR_TEXT_NOTE")])
    put(msp, 0, labels(3, "AG1", 100, 250), layer="0_STR_GIRDER_NAME-APT")
    put(msp, 0, labels(4, "CW1", 100, 230), layer="0_STR_WALL_NAME")
    put(msp, 1, labels(7, "G1", 100, 300), layer="0_STR_GIRDER_NAME")
    put(msp, 1, labels(2, "AG1", 100, 250), layer="0_STR_GIRDER_NAME-APT")
    put(msp, 1, labels(6, "C2", 100, 280), layer="0_STR_COL_NAME")
    put(msp, 1, [("3. 미표기 기둥 : C1", 650, 400), ("4. 아파트 영역은 아파트 구조", 650, 393), ("평면도 참조.", 650, 386)],
        layer="0_STR_TEXT_NOTE")
    put(msp, 2, labels(4, "B1", 100, 300), layer="0_STR_BEAM_NAME")
    # 101동 mat foundation: no member labels, only rebar and pit texts.
    put(msp, 3, [("D16@300(T/B, EW)", 300, 300)], layer="0_STR_SYM")
    put(msp, 3, [("집수정", 400, 310), ("1200X1200X1200(H)", 400, 303)], layer="DEFPOINT")
    put(msp, 3, [("TC1:500x900", 200, 200)], layer="Defpoints")
    # Parking footings, mostly kept inside a pasted block.
    put(msp, 4, labels(1, "F1", 100, 300), layer="0_STR_FDN_NAME")
    pasted(doc, "A$C0002", 4, [(t, x, y, "0_STR_FDN_NAME") for t, x, y in labels(2, "F1", 100, 280)])
    return doc


def index_doc():
    rows = [("S-112", "101동 지하1층 구조평면도", "1/200"), ("S-201", "101동 지하2층 기초 구조평면도", "1/200"),
            ("S-311", "101동 벽체 일람표-1", "NONE"), ("S-331", "101동 보 일람표", "NONE"),
            ("S-402", "지하1층 지하주차장 구조평면도", "1/400"), ("S-403", "지하주차장 지붕 구조평면도", "1/400"),
            ("S-501", "지하주차장 기초 구조평면도", "1/400"), ("S-601", "주차장 기초 일람표", "NONE"),
            ("S-611", "주차장 기둥 일람표", "NONE"), ("S-631", "주차장 보 일람표", "NONE")]
    return index_host([rows[:6], rows[6:]])


def upload():
    ev = evidence(**{"XR-FORM-A(A3).dxf": form_doc()}, schedules=schedules_doc(), plans=plans_doc(), index=index_doc())
    parsed = {name: {"source_member_sha": "sha-" + name} for name in ev}
    sid.attach_sheet_identity(parsed, ev)
    return parsed


def sheets(parsed, file):
    return {s["drawing_number"]: s for s in parsed[file]["sheets"]}


def tables(sheet):
    return {t["mark"]: t for t in sheet["tables"]}


class BlockTextTests(SimpleTestCase):
    def test_nested_block_texts_are_placed_and_take_the_insert_layer(self):
        doc = ezdxf.new()
        inner = doc.blocks.new("INNER")
        inner.add_text("AG1", dxfattribs={"insert": (1, 2), "height": 2, "layer": "0"})
        outer = doc.blocks.new("OUTER")
        outer.add_text("G1", dxfattribs={"insert": (0, 0), "height": 2, "layer": "0_STR_GIRDER_NAME"})
        outer.add_blockref("INNER", (10, 0))
        doc.modelspace().add_blockref("OUTER", (100, 50), dxfattribs={"xscale": 2, "yscale": 2,
                                                                       "layer": "0_STR_GIRDER_NAME-APT"})
        found = {t["text"]: t for t in sid.extract_sheet_evidence(doc)["block_texts"]}
        self.assertEqual((found["G1"]["x"], found["G1"]["y"], found["G1"]["layer"]), (100, 50, "0_STR_GIRDER_NAME"))
        self.assertEqual((found["AG1"]["x"], found["AG1"]["y"], found["AG1"]["h"]), (122, 54, 4))
        self.assertEqual(found["AG1"]["layer"], "0_STR_GIRDER_NAME-APT")

    def test_title_block_forms_and_xrefs_are_not_opened(self):
        self.assertEqual(sid.extract_sheet_evidence(bound_host([("S-101", "평면도")]))["block_texts"], [])
        self.assertEqual(sid.extract_sheet_evidence(xref_host([("S-101", "평면도")]))["block_texts"], [])

    def test_identity_ignores_block_texts_and_marks_count_them(self):
        parsed = upload()
        plans = sheets(parsed, "plans")
        self.assertEqual([plans[n]["drawing_title"] for n in ("S-112", "S-501")],
                         ["101동 지하 1층 구조평면도", "지하주차장 기초 구조평면도"])
        f1 = {m["mark"]: m for m in plans["S-501"]["marks"]}["F1"]
        self.assertEqual((f1["count"], f1["in_blocks"]), (3, 2))
        g1 = {m["mark"]: m for m in plans["S-112"]["marks"]}["G1"]
        self.assertEqual((g1["count"], g1["layers"]), (5, {"0_STR_GIRDER_NAME": 5}))


class TableTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.parsed = upload()
        cls.schedules = sheets(cls.parsed, "schedules")

    def test_column_schedule_with_row_labels_in_a_block(self):
        found = tables(self.schedules["S-611"])
        self.assertEqual(set(found), {"C1", "C2"})
        self.assertEqual((found["C2"]["category"], found["C2"]["layout"]), ("columns", "label_column"))
        self.assertEqual(found["C2"]["rows"][0]["values"], {"size": "500 X 500", "main_bar": "14 - D19",
                                                            "hoop_mid": "D10@300", "hoop_end": "D10@150"})

    def test_beam_schedule_position_row_splits_marks(self):
        found = tables(self.schedules["S-631"])
        self.assertEqual(set(found), {"-1G1", "RB1"})
        self.assertEqual([(r["position"], r["values"]["top_bar"]) for r in found["-1G1"]["rows"]],
                         [("양단부", "4-D16"), ("중앙부", "3-D16")])
        self.assertEqual(found["RB1"]["rows"][0]["values"], {"size": "600 X 900", "top_bar": "4-D19",
                                                             "bottom_bar": "3-D19", "stirrup": "D10 @ 300"})
        self.assertEqual(found["-1G1"]["category"], "beams")
        self.assertEqual(tables(self.schedules["S-331"])["AG1"]["category"], "beams")  # the frame title says 기둥

    def test_wall_schedule_rows_per_floor_range_and_ditto(self):
        found = tables(self.schedules["S-311"])
        self.assertEqual(set(found), {"CW1", "CW2"})
        top, basement = found["CW1"]["rows"]
        self.assertEqual(top["levels_text"], "1F - 13F")
        self.assertEqual(top["levels"], [str(n) for n in range(1, 14)])
        self.assertEqual(top["values"], {"fck": "30", "thickness": "180", "vertical_bar": "D10@450",
                                         "horizontal_bar": "D10@350", "remark": "-"})
        self.assertEqual(top["ditto"], ["vertical_bar:from_below", "horizontal_bar:from_below"])
        self.assertEqual((basement["levels"], basement["values"]["thickness"]), (["B2", "B1"], "200"))
        self.assertEqual(found["CW2"]["rows"][0]["levels"], ["B1"] + [str(n) for n in range(1, 14)])

    def test_footing_schedule_with_stacked_header(self):
        f1 = tables(self.schedules["S-601"])["F1"]
        self.assertEqual(f1["category"], "foundations")
        self.assertEqual(f1["rows"][0]["values"], {"type": "A", "depth": "600", "length_x": "3,400", "length_y": "3,400",
                                                   "rebar_1": "D16 @ 300", "rebar_2": "D16 @ 300", "height": "-",
                                                   "remark": "-"})

    def test_plans_have_no_tables(self):
        self.assertTrue(all(not s["tables"] for s in self.parsed["plans"]["sheets"]))


class NoteAndSpecTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.plans = sheets(upload(), "plans")

    def test_multi_line_reference_note_from_a_block(self):
        self.assertEqual(self.plans["S-112"]["notes"], [{"kind": "reference", "area": "parking",
                                                        "text": "2. 주차장 부분은 \"주차장 구조 평면도\" 참고할 것."}])

    def test_unlabelled_default_and_reference_notes(self):
        notes = {n["kind"]: n for n in self.plans["S-402"]["notes"]}
        self.assertEqual((notes["unlabelled_default"]["mark"], notes["unlabelled_default"]["category"]), ("C1", "columns"))
        self.assertEqual(notes["reference"]["area"], "building")

    def test_foundation_plan_specs_skip_defpoints(self):
        specs = {s["text"]: s for s in self.plans["S-201"]["specs"]}
        self.assertEqual(set(specs), {"D16@300(T/B, EW)", "1200X1200X1200(H)"})
        self.assertEqual((specs["1200X1200X1200(H)"]["kind"], specs["1200X1200X1200(H)"]["label"]), ("size", "집수정"))
        self.assertEqual(self.plans["S-112"]["specs"], [])  # only foundation plans keep spec texts

    def test_value_parsers(self):
        self.assertEqual(qs.parse_level_text("B2F - B1F"), ["B2", "B1"])
        self.assertEqual(qs.parse_level_text("B1F~2F"), ["B1", "1", "2"])
        self.assertEqual(qs.level_prefix("-1G1"), ("B1", "G1"))
        self.assertEqual(qs.level_prefix("RG1"), ("R", "G1"))
        self.assertIsNone(qs.level_prefix("AG1"))
        self.assertEqual(qs.schedule_mark("-1WG1"), "-1WG1")
        self.assertEqual(qs.parse_size("500 X 1,450"), (500, 1450))
        self.assertEqual(qs.parse_bars("8-D22+4-D19"), [(8, 22), (4, 19)])
        self.assertEqual(qs.parse_spaced("D10 @ 300"), (10, 300))


class MatrixTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.matrix = links.build_member_links(upload())
        cls.rows = {(r["category"], r["mark"]): r for r in cls.matrix["marks"]}

    def test_reference_copies_are_listed_not_counted(self):
        g1 = self.rows[("beams", "G1")]
        self.assertEqual([(o["drawing_number"], o["count"]) for o in g1["occurrences"]], [("S-402", 7)])
        self.assertEqual([(c["drawing_number"], c["count"]) for c in g1["reference_copies"]], [("S-112", 5)])
        self.assertEqual(g1["label_total"], 7)
        self.assertIn("reference_copies_not_counted", g1["flags"])
        ag1 = self.rows[("beams", "AG1")]  # apartment beams drawn on the parking plan for reference
        self.assertEqual([(o["drawing_number"], o["count"]) for o in ag1["occurrences"]], [("S-112", 3)])
        self.assertEqual([c["drawing_number"] for c in ag1["reference_copies"]], ["S-402"])
        self.assertEqual([c["drawing_number"] for c in self.rows[("columns", "C2")]["reference_copies"]], ["S-112"])
        cw1 = self.rows[("walls", "CW1")]
        self.assertEqual([(o["drawing_number"], o["count"]) for o in cw1["occurrences"]], [("S-112", 4)])

    def test_level_prefixed_schedule_marks_define_plain_labels(self):
        g1 = self.rows[("beams", "G1")]
        self.assertEqual([(d["drawing_number"], d.get("schedule_mark"), d.get("levels")) for d in g1["definitions"]],
                         [("S-631", "-1G1", ["B1"])])
        b1 = self.rows[("beams", "B1")]
        self.assertEqual([(s["schedule_mark"], s["levels"]) for s in b1["specs"]], [("RB1", ["R"])])
        self.assertNotIn(("beams", "-1G1"), self.rows)

    def test_plan_statuses_notes_and_defaults(self):
        plans = {p["drawing_number"]: p for p in self.matrix["plans"]}
        self.assertEqual(plans["S-201"]["status"], "specs_only")
        self.assertEqual((plans["S-112"]["status"], plans["S-112"]["reference_copy_labels"]), ("labelled", 7))
        self.assertEqual(plans["S-403"]["levels"], ["R"])
        self.assertEqual(plans["S-201"]["levels"], ["FDN"])
        c1 = self.rows[("columns", "C1")]
        self.assertIn("unlabelled_default_on_plans", c1["flags"])
        self.assertEqual([d["drawing_number"] for d in c1["unlabelled_default_plans"]], ["S-402"])
        f1 = self.rows[("foundations", "F1")]
        self.assertEqual([(o["drawing_number"], o["count"], o["in_blocks"]) for o in f1["occurrences"]], [("S-501", 3, 2)])
        self.assertTrue(all(isinstance(s["rows"], int) for r in self.matrix["marks"] for s in r["specs"]))
        self.assertLess(len(self.matrix["schedule_rows"]), sum(len(r["specs"]) for r in self.matrix["marks"]) + 1)


class MemberComparisonTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.matrix = links.build_member_links(upload())

    def compare(self, members):
        members = links.attach_plan_evidence(members, self.matrix)
        return links.attach_schedule_evidence(members, self.matrix)

    def test_plan_evidence_for_copies_defaults_and_mat_foundations(self):
        result = self.compare({
            "beams": [{"mark": "G1", "count": 5, "zone": "지하1층", "section": "101동"},
                      {"mark": "G1", "count": 7, "zone": "지하1층", "section": "지하주차장"}],
            "columns": [{"mark": "C1", "count": 9, "zone": "지하1층", "section": "지하주차장"}],
            "foundations": [{"mark": "MF1", "count": 1, "zone": "기초", "section": "101동",
                             "rebar_size": "D16", "rebar_spacing_m": 0.3}]})
        copy, parking = result["beams"]
        self.assertEqual(copy["plan_evidence"]["status"], "reference_copy_only")
        self.assertIn("S-112", copy["plan_evidence"]["note"])
        self.assertIn("S-402", copy["plan_evidence"]["note"])
        self.assertEqual((parking["plan_evidence"]["status"], parking["plan_evidence"]["cad_count"]), ("count_matches", 7))
        c1 = result["columns"][0]["plan_evidence"]
        self.assertEqual(c1["status"], "mark_not_on_cad_plans")
        self.assertIn("unlabelled_default", c1["flags"])
        self.assertIn("미표기 기둥 : C1", c1["note"])
        mat = result["foundations"][0]["plan_evidence"]
        self.assertEqual(mat["status"], "foundation_plan_specs")
        self.assertIn("D16@300(T/B, EW)", mat["note"])
        self.assertIn("같은 문구", mat["note"])
        self.assertEqual(result["beams"][0]["count"], 5)

    def test_schedule_values_are_compared_not_replaced(self):
        result = self.compare({
            "columns": [{"mark": "C2", "width_m": 0.5, "depth_m": 0.5, "main_rebar_size": "D19", "main_rebar_count": 14,
                         "tie_rebar_size": "D10", "tie_spacing_m": 0.15, "zone": "지하1층", "section": "지하주차장"},
                        {"mark": "C1", "width_m": 0.4, "depth_m": 0.8, "zone": "지하1층", "section": "지하주차장"},
                        {"mark": "C9", "width_m": 0.4, "depth_m": 0.4, "zone": "지하1층", "section": "지하주차장"}],
            "beams": [{"mark": "G1", "width_m": 0.5, "depth_m": 0.6, "main_rebar_size": "D16", "main_rebar_count": 4,
                       "stirrup_size": "D10", "stirrup_spacing_m": 0.25, "zone": "지하1층", "section": "지하주차장"},
                      {"mark": "B1", "width_m": 0.6, "depth_m": 0.9, "stirrup_size": "D10", "stirrup_spacing_m": 0.3,
                       "zone": "지붕", "section": "지하주차장"},
                      {"mark": "-1G1", "width_m": 0.5, "depth_m": 0.6, "zone": "지하1층", "section": "지하주차장"}],
            "walls": [{"mark": "CW1", "thickness_m": 0.18, "rebar_size": "D10", "rebar_spacing_m": 0.45,
                       "zone": "5층", "section": "101동"},
                      {"mark": "CW1", "thickness_m": 0.18, "zone": "지하1층", "section": "101동"}],
            "foundations": [{"mark": "F1", "thickness_m": 0.6, "length_m": 3.4, "width_m": 3.4, "rebar_size": "D16",
                             "rebar_spacing_m": 0.3, "zone": "기초", "section": "지하주차장"}]})
        status = lambda item: item["schedule_evidence"]["status"]
        c2, c1, c9 = result["columns"]
        self.assertEqual(status(c2), "schedule_matches")
        self.assertEqual(status(c1), "schedule_differs")
        self.assertIn("단면 AI 400×800 / CAD 400 X 900", c1["schedule_evidence"]["note"])
        self.assertEqual(c1["width_m"], 0.4)
        self.assertEqual(status(c9), "not_in_schedule")
        self.assertEqual([status(b) for b in result["beams"]], ["schedule_matches"] * 3)
        self.assertEqual(result["beams"][0]["schedule_evidence"]["sheets"], ["S-631"])
        upper, basement = result["walls"]
        self.assertEqual(status(upper), "schedule_matches")
        self.assertIn("ditto_from_below", upper["schedule_evidence"]["flags"])
        self.assertEqual(status(basement), "schedule_differs")
        self.assertEqual(status(result["foundations"][0]), "schedule_matches")
        self.assertFalse(upper["schedule_evidence"]["quantity_verified"])

    def test_results_saved_before_this_change_are_still_compared(self):
        old = {"schema": "quantity_member_links_v1", "plans": [{"drawing_number": "S-401", "labelled_marks": 18}],
               "marks": [{"category": "columns", "mark": "C2", "definitions": [], "flags": [], "typical_plans": [],
                          "occurrences": [{"plan": 0, "drawing_number": "S-401", "count": 18, "section": "지하주차장",
                                           "levels": ["B2"]}]}]}
        members = {"columns": [{"mark": "C2", "count": 18, "zone": "지하2층", "section": "지하주차장"}]}
        members = links.attach_schedule_evidence(links.attach_plan_evidence(members, old), old)
        self.assertEqual(members["columns"][0]["plan_evidence"]["status"], "count_matches")
        self.assertEqual(members["columns"][0]["schedule_evidence"]["status"], "no_schedule_tables")


class PipelineTests(SimpleTestCase):
    def members(self):
        return {"walls": [{"mark": "CW1", "count": 4, "thickness_m": 0.2, "zone": "지하1층", "section": "101동"}]}

    def test_extraction_and_confirmation_compare_schedule_values(self):
        from core import quantity_views as views
        members = views._attach_member_links(self.members(), upload())
        self.assertEqual(members["walls"][0]["schedule_evidence"]["status"], "schedule_matches")
        self.assertEqual(views._build_review_checklist(members)[0]["schedule_evidence"]["status"], "schedule_matches")
        members["walls"][0]["thickness_m"] = 0.25
        refreshed = views._refresh_member_evidence(members)
        self.assertEqual(refreshed["walls"][0]["schedule_evidence"]["status"], "schedule_differs")

    def test_zip_parse_reads_tables_and_block_labels(self):
        from core import quantity_views as views
        from core.test_quantity_sheet_identity_integration import dxf_bytes
        bundle = io.BytesIO()
        with zipfile.ZipFile(bundle, "w") as archive:
            archive.writestr("구조/XR-FORM-A(A3).dxf", dxf_bytes(form_doc()))
            archive.writestr("구조/일람표.dxf", dxf_bytes(schedules_doc()))
            archive.writestr("구조/평면도.dxf", dxf_bytes(plans_doc()))
        parsed = views.parse_dwg_from_zip(bundle.getvalue())
        self.assertEqual(set(tables(sheets(parsed, "구조/일람표.dxf")["S-611"])), {"C1", "C2"})
        self.assertEqual({m["mark"]: m["count"] for m in sheets(parsed, "구조/평면도.dxf")["S-501"]["marks"]}["F1"], 3)
        summary = json.dumps(views._ai_cad_summary(parsed), ensure_ascii=False)
        self.assertNotIn('"tables"', summary)  # parsed tables, notes and block texts stay local
        self.assertNotIn('"notes"', summary)
        self.assertNotIn("주차장 부분은", summary)

    def test_excel_lists_schedule_rows_notes_and_comparison(self):
        from openpyxl import load_workbook
        from core import quantity_views as views
        members = views._attach_member_links(self.members(), upload())
        data = {"member_links": members["member_links"], "_raw_members": members, "source_manifest": {}}
        request = RequestFactory().post("/api/quantity/download-excel/", json.dumps({"results": {"structural": data}}),
                                        content_type="application/json")
        workbook = load_workbook(io.BytesIO(views._build_excel_response(request).content))
        schedule = [list(r) for r in workbook["일람표 정의"].values]
        self.assertIn("층 범위", schedule[0])
        cw1 = [r for r in schedule if r[1] == "CW1"]
        self.assertEqual([(r[6], r[schedule[0].index("두께")]) for r in cw1], [("1F - 13F", "180"), ("B2F - B1F", "200")])
        self.assertIn("↑", cw1[0][-1])
        g1 = next(r for r in schedule if r[1] == "G1")
        self.assertEqual((g1[2], g1[3], g1[4]), ("S-631", "'-1G1", "B1"))  # a leading "-" is guarded against formulas
        notes = [list(r) for r in workbook["도면 주기·문구"].values]
        self.assertTrue(any("주차장 부분은" in str(r[5]) for r in notes))
        self.assertTrue(any(r[4] == "크기 문구" and "집수정" in str(r[5]) for r in notes))
        sources = [list(r) for r in workbook["부재 출처"].values]
        self.assertIn("일람표 비교", sources[0])
        self.assertTrue(any("일치" in r and "CW1" in r for r in sources[1:]))
        placements = [list(r) for r in workbook["부재 배치 근거"].values]
        self.assertTrue(any(r[1] == "G1" and r[2] == "다른 영역 참고 표기(합계 제외)" for r in placements))
        for name in ("일람표 정의", "도면 주기·문구"):
            self.assertTrue(all(cell.data_type != "f" for row in workbook[name] for cell in row))
