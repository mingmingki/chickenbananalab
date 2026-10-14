"""Schedule definitions linked to plan label occurrences (CAD evidence only, never a verified count)."""
from django.test import SimpleTestCase

from core import quantity_member_links as links
from core import quantity_sheet_identity as sid
from core.test_quantity_sheet_identity import FRAME_W, evidence, form_doc, index_host, xref_host


def add_marks(doc, frame_index, marks, layer):
    msp = doc.modelspace()
    for i, mark in enumerate(marks):
        msp.add_text(mark, dxfattribs={"insert": (frame_index * FRAME_W + 100 + (i % 20) * 20, 300 + (i // 20) * 10),
                                       "height": 3, "layer": layer})


def cad_upload():
    schedules = xref_host([("S-611", "주차장 기둥 일람표"), ("S-331", "아파트 기둥 일람표")])
    add_marks(schedules, 0, ["C1", "C2", "C3", "14-D22", "500 X 500"], "0_STR_TEXT")
    add_marks(schedules, 1, ["TG1", "AG1", "5-D19"], "0_STR_TEXT")
    plans = xref_host([("S-401", "지하2층 지하주차장 구조평면도"), ("S-115", "101동 지상 3층~지상 13층 구조평면도"),
                       ("S-101", "52형 단위세대 구조평면도"), ("S-116", "101동 지붕, 옥탑, 옥탑지붕 구조평면도")])
    add_marks(plans, 0, ["C2"] * 18 + ["C3"] * 3 + ["C9"], "0_STR_COL_NAME")
    add_marks(plans, 0, ["X1", "Y1", "C1"], "Bubble")  # grid labels are not members
    add_marks(plans, 1, ["TG1"] * 12, "0_STR_GIRDER_NAME-APT")
    add_marks(plans, 1, ["66AHW1"] * 4, "0_STR_WALL_NAME")
    add_marks(plans, 2, ["66AHW1"] * 2, "0_STR_WALL_NAME")
    add_marks(plans, 3, ["TG1"] * 2, "0_STR_GIRDER_NAME-APT")
    index = index_host([[("S-001", "구조 도면목록표", "NONE"), ("S-101", "52형 단위세대 구조평면도", "1/80"),
                         ("S-115", "101동 지상3~지상13층 구조평면도", "1/200"), ("S-116", "101동 지붕, 옥탑, 옥탑지붕 구조평면도", "1/200")],
                        [("S-331", "101동 보 일람표", "NONE"), ("S-401", "지하2층 지하주차장 구조평면도", "1/400"),
                         ("S-611", "주차장 기둥 일람표", "NONE")]])
    ev = evidence(**{"XR-FORM-A(A3).dxf": form_doc()}, schedules=schedules, plans=plans, index=index)
    parsed = {name: {"source_member_sha": "sha-" + name} for name in ev}
    sid.attach_sheet_identity(parsed, ev)
    return parsed


class SheetMarkTests(SimpleTestCase):
    def test_sheet_marks_count_labels_by_layer_and_skip_specs_and_title_blocks(self):
        sheets = {s["drawing_number"]: s for s in cad_upload()["plans"]["sheets"]}
        marks = {m["mark"]: m for m in sheets["S-401"]["marks"]}
        self.assertEqual(marks["C2"]["count"], 18)
        self.assertEqual(marks["C2"]["layers"], {"0_STR_COL_NAME": 18})
        self.assertNotIn("S-401", marks)
        schedule_marks = {m["mark"] for m in {s["drawing_number"]: s for s in cad_upload()["schedules"]["sheets"]}["S-611"]["marks"]}
        self.assertEqual(schedule_marks, {"C1", "C2", "C3"})


class ScopeTests(SimpleTestCase):
    def test_titles_and_ai_zones_parse_to_levels(self):
        self.assertEqual(links.parse_sheet_scope("101동 지상 3층~지상 13층 구조평면도"),
                         {"section": "101동", "levels": [str(n) for n in range(3, 14)], "kind": "floor_plan"})
        self.assertEqual(links.parse_sheet_scope("지하2층 지하주차장 구조평면도"),
                         {"section": "지하주차장", "levels": ["B2"], "kind": "floor_plan"})
        self.assertEqual(links.parse_sheet_scope("52형 단위세대 구조평면도")["kind"], "unit_type_plan")
        self.assertIsNone(links.parse_sheet_scope("101동 지붕, 옥탑, 옥탑지붕 구조평면도")["levels"])
        self.assertEqual(links.parse_zone_levels("기준층(2~15층)"), [str(n) for n in range(2, 16)])
        self.assertEqual(links.parse_zone_levels("지하1층"), ["B1"])
        self.assertEqual(links.parse_zone_levels("1층"), ["1"])
        self.assertIsNone(links.parse_zone_levels("미상"))


class MatrixTests(SimpleTestCase):
    def matrix(self):
        return links.build_member_links(cad_upload())

    def test_marks_link_schedule_definitions_to_plan_occurrences(self):
        rows = {(r["category"], r["mark"]): r for r in self.matrix()["marks"]}
        c2 = rows[("columns", "C2")]
        self.assertEqual([d["drawing_number"] for d in c2["definitions"]], ["S-611"])
        self.assertEqual([(o["drawing_number"], o["count"], o["levels"]) for o in c2["occurrences"]], [("S-401", 18, ["B2"])])
        self.assertEqual(c2["label_total"], 18)
        tg1 = rows[("beams", "TG1")]
        self.assertEqual(tg1["label_total"], None)  # the roof sheet has no countable level range
        self.assertEqual(tg1["label_total_known_part"], 12 * 11)
        self.assertIn("floor_count_unknown", tg1["flags"])
        self.assertIn("schedule_category_differs", tg1["flags"])  # frame titled 기둥 일람표, beams on plans

    def test_grid_labels_unit_plans_and_undefined_marks(self):
        matrix = self.matrix()
        rows = {(r["category"], r["mark"]): r for r in matrix["marks"]}
        self.assertNotIn(("columns", "X1"), rows)
        self.assertEqual(rows[("columns", "C1")]["occurrences"], [])
        self.assertIn("defined_not_on_plans", rows[("columns", "C1")]["flags"])
        self.assertIn("on_plans_not_defined", rows[("columns", "C9")]["flags"])
        wall = rows[("walls", "66AHW1")]
        self.assertEqual([o["drawing_number"] for o in wall["occurrences"]], ["S-115"])
        self.assertEqual([t["drawing_number"] for t in wall["typical_plans"]], ["S-101"])


class MemberEvidenceTests(SimpleTestCase):
    def test_member_counts_are_compared_not_replaced(self):
        matrix = links.build_member_links(cad_upload())
        members = {"columns": [
            {"mark": "C2", "count": 18, "zone": "지하2층", "section": "지하주차장"},
            {"mark": "C3", "count": 4, "zone": "지하2층", "section": "주차장"},
            {"mark": "C7", "count": 2, "zone": "지하2층", "section": "지하주차장"},
            {"mark": "C2", "count": 5, "zone": "지하1층", "section": "지하주차장"}],
            "beams": [{"mark": "TG1", "count": 12, "zone": "기준층(3~13층)", "section": "101동", "floor_repeat_count": 10}]}
        result = links.attach_plan_evidence(members, matrix)
        c2, c3, c7, c2_b1 = result["columns"]
        self.assertEqual((c2["plan_evidence"]["status"], c2["plan_evidence"]["cad_count"]), ("count_matches", 18))
        self.assertEqual((c3["plan_evidence"]["status"], c3["plan_evidence"]["cad_count"], c3["count"]), ("count_differs", 3, 4))
        self.assertEqual(c7["plan_evidence"]["status"], "mark_not_on_cad_plans")
        self.assertEqual(c2_b1["plan_evidence"]["status"], "no_matching_cad_plan")
        tg1 = result["beams"][0]["plan_evidence"]
        self.assertEqual((tg1["status"], tg1["cad_floors"]), ("count_matches", 11))
        self.assertIn("floor_repeat_differs", tg1["flags"])
        self.assertFalse(tg1["quantity_verified"])

    def test_without_cad_marks_members_are_untouched(self):
        members = {"columns": [{"mark": "C2", "count": 3, "zone": "1층"}]}
        result = links.attach_plan_evidence(members, links.build_member_links({}))
        self.assertEqual(result["columns"][0]["plan_evidence"]["status"], "no_cad_plan_marks")
        self.assertEqual(result["columns"][0]["count"], 3)


class PipelineIntegrationTests(SimpleTestCase):
    def members(self):
        return {"columns": [{"mark": "C2", "count": 18, "zone": "지하2층", "section": "지하주차장"},
                            {"mark": "C3", "count": 4, "zone": "지하2층", "section": "지하주차장"}]}

    def test_extraction_attaches_evidence_and_matrix(self):
        from core import quantity_views as views
        members = views._attach_member_links(self.members(), cad_upload())
        self.assertEqual(members["columns"][1]["plan_evidence"]["status"], "count_differs")
        self.assertEqual(members["member_links"]["schema"], "quantity_member_links_v1")
        row = views._build_review_checklist(members)[1]
        self.assertEqual(row["plan_evidence"]["cad_count"], 3)

    def test_confirmed_correction_is_compared_again(self):
        from core import quantity_views as views
        members = views._attach_member_links(self.members(), cad_upload())
        members["columns"][1]["count"] = 3
        refreshed = views._refresh_member_evidence(members)
        self.assertEqual(refreshed["columns"][1]["plan_evidence"]["status"], "count_matches")

    def test_failure_to_link_never_breaks_extraction(self):
        from core import quantity_views as views
        members = views._attach_member_links(self.members(), {"broken.dxf": {"sheets": [{"bad": True}]}})
        self.assertEqual(members["columns"][0]["count"], 18)

    def test_excel_lists_mark_matrix_and_member_comparison(self):
        import io, json
        from django.test import RequestFactory
        from openpyxl import load_workbook
        from core import quantity_views as views
        members = views._attach_member_links(self.members(), cad_upload())
        data = {"member_links": members["member_links"], "_raw_members": members, "source_manifest": {}}
        request = RequestFactory().post("/api/quantity/download-excel/", json.dumps({"results": {"structural": data}}),
                                        content_type="application/json")
        workbook = load_workbook(io.BytesIO(views._build_excel_response(request).content))
        matrix = [list(r) for r in workbook["부재 배치 근거"].values]
        self.assertIn("평면 표기 합계", matrix[0])
        c2 = next(r for r in matrix if r[1] == "C2" and r[4] == "S-401")
        self.assertEqual((c2[0], c2[3]), ("기둥", "S-611"))
        self.assertIn(18, c2)
        sources = [list(r) for r in workbook["부재 출처"].values]
        self.assertIn("CAD 비교", sources[0])
        self.assertTrue(any("C3" in r and "AI·CAD 개수 다름" in r for r in sources))
        self.assertTrue(all(cell.data_type != "f" for row in workbook["부재 배치 근거"] for cell in row))
