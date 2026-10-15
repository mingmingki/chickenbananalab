"""Floor levels and storey heights from CAD, height evidence and the CAD-based concrete reference."""
import io
import json

from django.test import RequestFactory, SimpleTestCase

from core import quantity_cad_estimate as estimate_mod
from core import quantity_levels as ql
from core import quantity_member_links as links
from core import quantity_sheet_identity as sid
from core.test_quantity_geometry import upload as structural_upload
from core.test_quantity_sheet_identity import FRAME_W, evidence, form_doc, xref_host


def put(msp, frame, items, height=3.0):
    for text, x, y in items:
        msp.add_text(text, dxfattribs={"insert": (frame * FRAME_W + x, y), "height": height, "layer": "T"})


def architecture_doc():
    doc = xref_host([("A-410", "101동 입면도-1"), ("A-703", "지하주차장 종단면도")])
    msp = doc.modelspace()
    levels = [("G.L", "(EL+16.20)", 16.2), ("1층 바닥", "(EL+16.60)", 16.6), ("2층 바닥", "(EL+19.45)", 19.45),
              ("3층 바닥", "(EL+22.30)", 22.30), ("4층 바닥", "(EL+25.15)", 25.15), ("지붕층 바닥", "(EL+28.10)", 28.10)]
    for i, (name, el, _v) in enumerate(levels):
        put(msp, 0, [(name, 100, 100 + 25 * i), (el, 140, 100 + 25 * i)])
    # a parking section with several roof elevations in one drawing: that roof stays open
    for i, (name, el) in enumerate([("지하2층 바닥", "(EL+7.40)"), ("지하1층 바닥", "(EL+10.90)"),
                                    ("지붕", "(EL+14.00)"), ("지붕", "(EL+15.00)")]):
        put(msp, 1, [(name, 100 + 200 * (i // 3), 100 + 25 * (i % 3)), (el, 140 + 200 * (i // 3), 100 + 25 * (i % 3))])
    return doc


def detail_doc():
    """Basement wall details: storey heights are the drawn distances between level lines (mm)."""
    doc = xref_host([("S-341", "주동 지하외벽 일람표-1"), ("S-621", "주차장 지하외벽 일람표-1")], scale=30)
    msp = doc.modelspace()
    for frame, marks in ((0, [("지하2층 SL", 4970), ("지하1층 SL", 8470), ("주동 1층 SL", 14170)]),
                         (1, [("지하2층 SL", 4970), ("지하1층 SL", 8470), ("주차장 지붕 SL", 12570), ("GL.", 13770)])):
        for text, y in marks:
            msp.add_text(text, dxfattribs={"insert": (frame * FRAME_W * 30 + 2000, y), "height": 120, "layer": "T"})
    return doc


def parsed(**docs):
    ev = evidence(**{"XR-FORM-A(A3).dxf": form_doc()}, **docs)
    result = {name: {"source_member_sha": "sha-" + name} for name in ev}
    sid.attach_sheet_identity(result, ev)
    return result


def table():
    return ql.build_level_table(parsed(details=detail_doc()), parsed(arch=architecture_doc()))


class ReadTests(SimpleTestCase):
    def test_level_names_and_elevations(self):
        self.assertEqual([ql.level_key(t) for t in ("3층 바닥", "지하1층 SL", "지붕층 바닥", "옥탑지붕 바닥", "G.L", "B2F", "12F")],
                         ["3", "B1", "R", "PHR", "GL", "B2", "12"])
        self.assertEqual(ql.parse_elevation("(EL+22.30)"), 22.3)
        self.assertEqual(ql.parse_elevation("EL+16,600"), 16.6)
        self.assertEqual(ql.parse_elevation("- GL±0=EL+16.20"), 16.2)
        self.assertEqual(ql.level_key("- GL±0=EL+16.20"), "GL")

    def test_sheet_levels_pair_names_with_elevations_and_measure_details(self):
        arch = {s["drawing_number"]: s for s in parsed(arch=architecture_doc())["arch"]["sheets"]}
        self.assertEqual(arch["A-410"]["levels"]["elevations"]["3"], 22.3)
        self.assertEqual(arch["A-703"]["levels"]["elevation_conflicts"], {"R": [14.0, 15.0]})
        details = {s["drawing_number"]: s for s in parsed(details=detail_doc())["details"]["sheets"]}
        rows = details["S-621"]["levels"]["section_heights"]
        self.assertEqual([(r["from"], r["to"], r["height_m"]) for r in rows], [("B2", "B1", 3.5), ("B1", "R", 4.1)])


class TableTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.table = table()

    def test_storey_heights_from_elevations_and_details(self):
        heights = {k: v["height_m"] for k, v in self.table["sections"]["101동"]["heights"].items()}
        self.assertEqual(heights, {"1": 2.85, "2": 2.85, "3": 2.85, "4": 2.95})
        self.assertEqual(self.table["sections"]["주동"]["heights"]["B1"]["height_m"], 5.7)
        self.assertEqual(self.table["sections"]["주차장"]["heights"]["B1"]["height_m"], 4.1)

    def test_a_level_with_several_values_in_one_drawing_is_not_used(self):
        self.assertIn("R", self.table["sections"]["지하주차장"]["uncertain"])
        self.assertNotIn("B1", self.table["sections"]["지하주차장"]["heights"])
        self.assertIn("several_elevations_in_one_drawing", {c["reason"] for c in self.table["conflicts"]})

    def test_lookup_falls_back_to_building_details_and_needs_agreement_without_a_section(self):
        self.assertEqual(ql.storey_height(self.table, "101동", "B1")[0], 5.7)  # 주동 detail
        self.assertEqual(ql.storey_height(self.table, "지하주차장", "B1")[0], 4.1)  # parking detail, not the open roof
        self.assertEqual(ql.storey_height(self.table, "미상", "2")[0], 2.85)
        self.assertIsNone(ql.storey_height(self.table, "미상", "B1")[0])  # 5.7 and 4.1 disagree


class HeightEvidenceTests(SimpleTestCase):
    def test_heights_are_compared_not_replaced(self):
        members = {"walls": [{"mark": "W1", "height_m": 2.85, "zone": "2층", "section": "101동"},
                             {"mark": "W2", "height_m": 2.65, "zone": "기준층(2~4층)", "section": "101동"},
                             {"mark": "W3", "height_m": 3.4, "zone": "지하1층", "section": "지하주차장"},
                             {"mark": "W4", "zone": "9층", "section": "101동"}],
                   "columns": [{"mark": "C2", "zone": "지하2층", "section": "지하주차장"}]}
        result = ql.attach_height_evidence(members, table())
        w1, w2, w3, w4 = result["walls"]
        self.assertEqual(w1["height_evidence"]["status"], "height_matches")
        self.assertEqual((w2["height_evidence"]["status"], w2["height_evidence"]["cad_height_m"]), ("height_within_slab", 2.85))
        self.assertIn("4층 2.95m", w2["height_evidence"]["note"])
        self.assertEqual(w3["height_evidence"]["status"], "height_differs")
        self.assertEqual(w3["height_m"], 3.4)
        self.assertEqual(w4["height_evidence"]["status"], "no_cad_height")
        column = result["columns"][0]["height_evidence"]
        self.assertEqual((column["status"], column["cad_height_m"]), ("cad_height_only", 3.5))
        self.assertIn("지하주차장 입면·단면 EL", column["note"])


class EstimateTests(SimpleTestCase):
    def test_gross_concrete_from_lengths_counts_sections_and_heights(self):
        matrix = links.build_member_links(structural_upload())
        level_table = {"sections": {"101동": {"heights": {str(n): {"height_m": 2.85, "source": "elevation", "sheets": ["A-410"]}
                                                       for n in range(3, 14)}}}}
        result = estimate_mod.estimate(matrix, level_table)
        rows = {(r["category"], r["mark"]): r for r in result["rows"]}
        self.assertEqual(rows[("beams", "G1")]["concrete_m3"], round(0.5 * 0.6 * 11.4, 2))  # schedule 500 X 600
        self.assertEqual(rows[("walls", "CW1")]["concrete_m3"], round(0.18 * 6.1 * 2.85 * 11, 2))  # 3~13층
        self.assertEqual(rows[("walls", "CW2")]["notes"], ["measured_thickness"])  # no schedule row: the drawn thickness
        self.assertFalse(result["deductions_applied"])
        self.assertEqual(result["left_out"], {"no_schedule_section": 3, "no_cad_length": 1})  # G2, G3, B2; CW3

    def test_pipeline_and_excel(self):
        from openpyxl import load_workbook
        from core import quantity_views as views
        members = {"walls": [{"mark": "CW1", "count": 1, "length_m": 6.1, "height_m": 2.85, "zone": "3층", "section": "101동"}]}
        members = views._attach_member_links(members, structural_upload(), parsed(arch=architecture_doc()))
        self.assertEqual(members["walls"][0]["height_evidence"]["status"], "height_matches")
        self.assertEqual(views._build_review_checklist(members)[0]["height_evidence"]["status"], "height_matches")
        self.assertNotIn("cad_estimate", members["member_links"])  # rebuilt for Excel, not stored
        members["walls"][0]["height_m"] = 3.6
        self.assertEqual(views._refresh_member_evidence(members)["walls"][0]["height_evidence"]["status"], "height_differs")
        data = {"member_links": members["member_links"], "_raw_members": members, "source_manifest": {},
                "items": [{"category": "콘크리트", "sub_category": "보", "quantity": 3.0, "unit": "m³"}],
                "_elevation_section": {"floor_heights": [{"level": "기준층(2~4층)", "height_m": 2.8, "repeat_count": 3}]}}
        request = RequestFactory().post("/api/quantity/download-excel/", json.dumps({"results": {"structural": data}}),
                                        content_type="application/json")
        workbook = load_workbook(io.BytesIO(views._build_excel_response(request).content))
        level_rows = [list(r) for r in workbook["CAD 층고"].values]
        third = next(r for r in level_rows if r[1] == "101동" and r[2] == "3층")
        self.assertEqual((third[3], third[4], third[7]), (22.3, 2.85, 2.8))
        estimate_rows = [list(r) for r in workbook["CAD 기반 물량(참고)"].values]
        beams_total = next(r for r in estimate_rows if r[1] == "보" and r[3] == 3.0)
        self.assertEqual(beams_total[2], round(0.5 * 0.6 * 11.4 + 0.4 * 0.6 * 6.0, 2))
        for name in ("CAD 층고", "CAD 기반 물량(참고)"):
            self.assertTrue(all(cell.data_type != "f" for row in workbook[name] for cell in row))
