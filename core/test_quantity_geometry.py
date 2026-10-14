"""Beam and wall lengths measured from CAD plan linework and matched to plan labels."""
import io
import json

import ezdxf
from django.test import RequestFactory, SimpleTestCase

from core import quantity_geometry as geo
from core import quantity_member_links as links
from core import quantity_sheet_identity as sid
from core.test_quantity_sheet_identity import FRAME_W, evidence, form_doc, index_host, xref_host

SCALE = 100
X = FRAME_W * SCALE  # second frame offset (frames are placed side by side)


def line(msp, a, b, layer):
    msp.add_line(a, b, dxfattribs={"layer": layer})


def label(msp, text, x, y, layer, height=300):
    msp.add_text(text, dxfattribs={"insert": (x, y), "height": height, "layer": layer})


def plans_doc():
    doc = xref_host([("S-402", "지하1층 지하주차장 구조평면도"), ("S-115", "101동 지상 3층~지상 13층 구조평면도")], scale=SCALE)
    msp = doc.modelspace()
    # G1: two spans of one girder, both faces broken at a column (16000-16600).
    for y in (30000, 30400):
        line(msp, (10000, y), (16000, y), "0_STR_GIRDER")
        line(msp, (16600, y), (22000, y), "0_STR_GIRDER")
    label(msp, "G1", 13000, 31500, "0_STR_GIRDER_NAME")
    label(msp, "G1", 19000, 31500, "0_STR_GIRDER_NAME")
    # B1: one vertical beam.
    line(msp, (40000, 20000), (40000, 26000), "0_STR_BEAM")
    line(msp, (40400, 20000), (40400, 26000), "0_STR_BEAM")
    label(msp, "B1", 41300, 23000, "0_STR_BEAM_NAME")
    # G2 and G3 along one continuous girder: the run is split between them.
    for y in (10000, 10500):
        line(msp, (10000, y), (20000, y), "0_STR_GIRDER")
    label(msp, "G2", 12000, 11300, "0_STR_GIRDER_NAME")
    label(msp, "G3", 18000, 11300, "0_STR_GIRDER_NAME")
    # An unlabelled girder piece.
    for y in (50000, 50500):
        line(msp, (60000, y), (64000, y), "0_STR_GIRDER")
    # B2 is drawn inside a pasted block.
    block = doc.blocks.new("A$C0001")
    line(block, (0, 0), (5000, 0), "0_STR_BEAM")
    line(block, (0, 450), (5000, 450), "0_STR_BEAM")
    msp.add_blockref("A$C0001", (10000, 40000))
    label(msp, "B2", 12000, 41500, "0_STR_BEAM_NAME")

    # Walls on the second frame.
    for y in (20000, 20180):  # CW1 with a 900 door gap; one face drawn twice
        line(msp, (X + 10000, y), (X + 13000, y), "0_STR_CONC")
        line(msp, (X + 13900, y), (X + 17000, y), "0_STR_CONC")
    line(msp, (X + 10000, 20000), (X + 13000, 20000), "0_STR_CONC")
    label(msp, "CW1", X + 12000, 20800, "0_STR_WALL_NAME")
    for y in (20400, 20500):  # a dry wall beside it is not structural
        line(msp, (X + 10000, y), (X + 17000, y), "0_STR_DRY_WALL")
    for y in (40000, 40200):  # CW2 named through a leader
        line(msp, (X + 10000, y), (X + 15000, y), "0_STR_CONC")
    label(msp, "CW2", X + 20000, 45000, "0_STR_WALL_NAME")
    msp.add_lwpolyline([(X + 20050, 45100), (X + 16000, 43000), (X + 12000, 40100)], dxfattribs={"layer": "0_STR_WALL_NAME"})
    for x in (30000, 30350):  # BW1 named by an extent line drawn along it
        line(msp, (X + x, 10000), (X + x, 18000), "0_STR_CONC")
    line(msp, (X + 31500, 10000), (X + 31500, 18000), "0_STR_WALL_NAME")
    label(msp, "BW1", X + 31600, 18200, "0_STR_WALL_NAME")
    x0, y0 = X + 50000, 30000  # a column outline next to a wall label is not a wall
    for a, b in (((x0, y0), (x0 + 500, y0)), ((x0 + 500, y0), (x0 + 500, y0 + 500)),
                 ((x0 + 500, y0 + 500), (x0, y0 + 500)), ((x0, y0 + 500), (x0, y0))):
        line(msp, a, b, "0_STR_CONC")
    label(msp, "CW3", X + 50200, 31000, "0_STR_WALL_NAME")
    return doc


def schedules_doc():
    doc = xref_host([("S-631", "주차장 보 일람표"), ("S-311", "101동 벽체 일람표-1")])
    msp = doc.modelspace()
    for text, x, y in (("부     호", 70, 500), ("G1", 180, 500), ("B1", 380, 500), ("SIZE", 72, 440),
                       ("500 X 600", 175, 440), ("400 X 600", 375, 440), ("늑근", 70, 410),
                       ("D10 @ 250", 175, 410), ("D10 @ 250", 375, 410)):
        msp.add_text(text, dxfattribs={"insert": (x, y), "height": 3, "layer": "T"})
    header = (("층별", 60), ("fck(MPa)", 90), ("두께(mm)", 125), ("수직근", 160), ("수평근", 195), ("비고", 230))
    msp.add_text("CW1", dxfattribs={"insert": (FRAME_W + 100, 520), "height": 3, "layer": "T"})
    for text, x in header:
        msp.add_text(text, dxfattribs={"insert": (FRAME_W + x, 510), "height": 3, "layer": "T"})
    for text, x in zip(("1F - 13F", "30", "180", "D10@450", "D10@350", "-"), (57, 93, 128, 162, 197, 233)):
        msp.add_text(text, dxfattribs={"insert": (FRAME_W + x, 500), "height": 3, "layer": "T"})
    return doc


def upload():
    index = index_host([[("S-115", "101동 지상3~지상13층 구조평면도", "1/100"), ("S-311", "101동 벽체 일람표-1", "NONE"),
                         ("S-402", "지하1층 지하주차장 구조평면도", "1/100"), ("S-631", "주차장 보 일람표", "NONE")]])
    ev = evidence(**{"XR-FORM-A(A3).dxf": form_doc()}, plans=plans_doc(), schedules=schedules_doc(), index=index)
    parsed = {name: {"source_member_sha": "sha-" + name} for name in ev}
    sid.attach_sheet_identity(parsed, ev)
    return parsed


class MeasureTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        sheets = {s["drawing_number"]: s for s in upload()["plans"]["sheets"]}
        cls.beams = sheets["S-402"]["geometry"]["beams"]
        cls.walls = sheets["S-115"]["geometry"]["walls"]

    def test_beam_spans_follow_paired_edges_and_labels(self):
        g1 = self.beams["marks"]["G1"]
        self.assertEqual((g1["runs"], g1["length_mm"], g1["widths"]), (2, 11400, {"400": 11400}))
        self.assertEqual(self.beams["marks"]["B1"]["length_mm"], 6000)
        self.assertEqual(self.beams["marks"]["B2"], {"runs": 1, "length_mm": 5000, "widths": {"450": 5000}})

    def test_one_run_is_split_between_two_marks(self):
        g2, g3 = self.beams["marks"]["G2"]["length_mm"], self.beams["marks"]["G3"]["length_mm"]
        self.assertEqual(g2 + g3, 10000)  # split at the midpoint between the two label centres
        self.assertTrue(4500 <= g2 <= 5500 and 4500 <= g3 <= 5500, (g2, g3))

    def test_unlabelled_length_is_reported_not_guessed(self):
        self.assertEqual(self.beams["unlabelled"], {"runs": 1, "length_mm": 4000, "widths": {"500": 4000}})
        self.assertEqual(self.beams["measured_mm"], 11400 + 6000 + 10000 + 4000 + 5000)

    def test_wall_runs_skip_openings_dry_walls_duplicates_and_columns(self):
        cw1 = self.walls["marks"]["CW1"]
        self.assertEqual((cw1["length_mm"], cw1["widths"]), (6100, {"180": 6100}))
        self.assertEqual(self.walls["marks"]["CW2"]["length_mm"], 5000)  # through the leader
        self.assertEqual(self.walls["marks"]["BW1"], {"runs": 1, "length_mm": 8000, "widths": {"350": 8000}})
        self.assertNotIn("CW3", self.walls["marks"])  # a column outline is not a wall
        self.assertEqual(self.walls["unlabelled"]["length_mm"], 0)

    def test_family_of_layers(self):
        self.assertEqual(geo.family_of("0_STR_GIRDER_NAME-APT"), ("beams", True))
        self.assertEqual(geo.family_of("0_STR_CONC"), ("walls", False))
        self.assertEqual(geo.family_of("0_STR_DRY_WALL"), (None, False))
        self.assertEqual(geo.family_of("0_STR_COL_NAME"), (None, False))

    def test_drawings_without_member_name_layers_collect_nothing(self):
        doc = ezdxf.new()
        doc.modelspace().add_line((0, 0), (1000, 0), dxfattribs={"layer": "0_STR_CONC"})
        self.assertEqual(geo.collect_segments(doc)["segments"], [])


class EvidenceTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.matrix = links.build_member_links(upload())

    def compare(self, members):
        return links.attach_geometry_evidence(members, self.matrix)

    def test_placements_carry_measured_lengths(self):
        rows = {(r["category"], r["mark"]): r for r in self.matrix["marks"]}
        self.assertEqual(rows[("beams", "G1")]["occurrences"][0]["measured"]["length_mm"], 11400)
        plan = next(p for p in self.matrix["plans"] if p["drawing_number"] == "S-402")
        self.assertEqual(plan["geometry"]["beams"]["unlabelled"]["length_mm"], 4000)

    def test_lengths_and_widths_are_compared_not_replaced(self):
        result = self.compare({
            "beams": [{"mark": "G1", "count": 2, "length_m": 5.7, "zone": "지하1층", "section": "지하주차장"},
                      {"mark": "B1", "count": 2, "length_m": 6.0, "zone": "지하1층", "section": "지하주차장"},
                      {"mark": "B2", "zone": "지하1층", "section": "지하주차장"}],
            "walls": [{"mark": "CW1", "count": 1, "length_m": 6.1, "zone": "5층", "section": "101동"},
                      {"mark": "CW9", "count": 1, "length_m": 2.0, "zone": "5층", "section": "101동"}]})
        g1, b1, b2 = result["beams"]
        self.assertEqual((g1["geometry_evidence"]["status"], g1["geometry_evidence"]["cad_length_m"]), ("length_matches", 11.4))
        self.assertIn("잰 길이의 100%가 일람표 폭 500mm와 다릅니다", g1["geometry_evidence"]["note"])
        self.assertTrue(g1["geometry_evidence"]["width_differs_from_schedule"])
        self.assertEqual((b1["geometry_evidence"]["status"], b1["geometry_evidence"]["difference_pct"]), ("length_differs", 100))
        self.assertEqual(b1["length_m"], 6.0)
        self.assertEqual(b2["geometry_evidence"]["status"], "cad_length_only")
        cw1, cw9 = result["walls"]
        self.assertEqual(cw1["geometry_evidence"]["status"], "length_matches")
        self.assertEqual(cw1["geometry_evidence"]["schedule_width_mm"], [180])
        self.assertNotIn("다릅니다", cw1["geometry_evidence"]["note"])
        self.assertEqual(cw9["geometry_evidence"]["status"], "no_cad_length")
        self.assertFalse(cw1["geometry_evidence"]["quantity_verified"])

    def test_pipeline_excel_and_review_rows(self):
        from openpyxl import load_workbook
        from core import quantity_views as views
        members = views._attach_member_links({"beams": [{"mark": "G1", "count": 2, "length_m": 5.7, "zone": "지하1층",
                                                          "section": "지하주차장"}]}, upload())
        self.assertEqual(views._build_review_checklist(members)[0]["geometry_evidence"]["status"], "length_matches")
        members["beams"][0]["length_m"] = 3.0
        self.assertEqual(views._refresh_member_evidence(members)["beams"][0]["geometry_evidence"]["status"], "length_differs")
        data = {"member_links": members["member_links"], "_raw_members": members, "source_manifest": {}}
        request = RequestFactory().post("/api/quantity/download-excel/", json.dumps({"results": {"structural": data}}),
                                        content_type="application/json")
        workbook = load_workbook(io.BytesIO(views._build_excel_response(request).content))
        rows = [list(r) for r in workbook["CAD 실측 치수"].values]
        self.assertIn("한 층 길이(m)", rows[0])
        g1 = next(r for r in rows if r[7] == "G1")
        self.assertEqual((g1[1], g1[8], g1[9]), ("S-402", 2, 11.4))
        self.assertTrue(any(r[7] == "(부호 없음)" and r[9] == 4.0 for r in rows))
        sources = [list(r) for r in workbook["부재 출처"].values]
        self.assertIn("CAD 실측 비교", sources[0])
        self.assertTrue(all(cell.data_type != "f" for row in workbook["CAD 실측 치수"] for cell in row))
