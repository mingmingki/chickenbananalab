"""Every page of an architectural set is read in batches, and the elevation review reads the pages
that the CAD sheet titles name as elevations and sections."""
import re
from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from core import quantity_pdf_pages as pages_mod
from core import quantity_views as views


def titles(**named):
    return {number.replace("_", "-"): title for number, title in named.items()}


class PageMapTests(SimpleTestCase):
    def test_printed_numbers_are_known_and_gaps_are_filled_only_when_counts_match(self):
        texts = {1: "A1=1/NONE A-001", 4: "A-004", 6: "A-010"}
        sheets = titles(A_001="표지", A_002="배치도", A_003="1층 평면도", A_004="2층 평면도", A_007="입면도-1",
                        A_008="입면도-2", A_010="단면도")
        mapped = pages_mod.map_pages(texts, sheets, 6)
        self.assertEqual(mapped, {1: "A-001", 2: "A-002", 3: "A-003", 4: "A-004", 6: "A-010"})  # page 5: two sheets, one page

    def test_lists_stray_marks_and_out_of_order_numbers_do_not_shift_pages(self):
        texts = {1: "A-001", 2: "A-901", 3: "A-002 A-003 A-004 (도면목록)", 4: "AW-12 창호", 5: "A-005", 6: "A-006"}
        sheets = titles(A_001="표지", A_002="목록", A_003="개요", A_004="배치도", A_005="입면도", A_006="단면도",
                        A_901="경비실")
        mapped = pages_mod.map_pages(texts, sheets, 6)
        self.assertEqual(mapped, {1: "A-001", 2: "A-002", 3: "A-003", 4: "A-004", 5: "A-005", 6: "A-006"})

    def test_leading_and_trailing_pages_and_labels(self):
        sheets = titles(A_001="표지", A_002="목록", A_003="입면도", A_004="단면도")
        mapped = pages_mod.map_pages({2: "A-002", 3: "A-003"}, sheets, 4)
        self.assertEqual(mapped, {1: "A-001", 2: "A-002", 3: "A-003", 4: "A-004"})
        self.assertEqual(pages_mod.pages_titled(mapped, sheets, re.compile("입면|단면")), [3, 4])
        self.assertEqual(pages_mod.page_label(3, mapped, sheets), "3쪽 · A-003 입면도")
        self.assertEqual(pages_mod.page_label(9, mapped, sheets), "9쪽")
        self.assertEqual(pages_mod.map_pages({1: "A-001"}, {}, 4), {1: "A-001"})  # no CAD: printed numbers only


def cad(**named):
    return {"A.dwg": {"sheets": [{"drawing_number": number, "drawing_title": title}
                                 for number, title in titles(**named).items()]}}


class BatchReadTests(SimpleTestCase):
    total = 20
    dwg = cad(A_005="101동 입면도-1", A_006="101동 주동단면도", A_017="지하주차장 종단면도",
              **{f"A_{n:03d}": "평면도" for n in range(1, 21) if n not in (5, 6, 17)})

    def run_readers(self, texts=None, dwg=None, analyze=None, elevation=None, cancel_cb=None, total=None):
        texts = {p: f"A-{p:03d}" for p in range(1, (total or self.total) + 1)} if texts is None else texts
        self.analyze = analyze or Mock(side_effect=lambda dwg, images, prompt, kind, page_labels=None, batch=None: {
            "items": [{"category": "창호", "quantity": len(images), "note": "근거"}], "summary": "ok",
            "warnings": [], "missing_info": []})
        self.elevation = elevation or Mock(side_effect=lambda dwg, images, page_labels=None, batch=None: {
            "floor_heights": [{"level": "3층", "height_m": 2.85, "repeat_count": 1}],
            "openings": [{"location": "정면", "width_m": 1.5, "height_m": 1.8, "count": 2}], "notes": []})
        with patch.object(views, "_pdf_page_count", return_value=total or self.total), \
                patch.object(views, "_extract_pdf_page_texts", return_value=texts), \
                patch.object(views, "_render_pdf_page_range", side_effect=lambda pdf, a, b, **k: [f"page {a}"]), \
                patch.object(views, "analyze_with_gemini", self.analyze), \
                patch.object(views, "extract_elevation_section_data", self.elevation):
            plan = views._architectural_page_plan(b"pdf", self.dwg if dwg is None else dwg)
            architectural = views.analyze_architectural_drawings(self.dwg if dwg is None else dwg, b"pdf", plan=plan,
                                                                 cancel_cb=cancel_cb)
            elevations = views.review_elevations_and_sections(self.dwg if dwg is None else dwg, b"pdf", plan=plan)
        return plan, architectural, elevations

    def test_every_page_is_read_eight_at_a_time_with_its_drawing_name(self):
        plan, architectural, _ = self.run_readers()
        self.assertEqual([len(c.args[1]) for c in self.analyze.call_args_list], [8, 8, 4])
        first = self.analyze.call_args_list[0].kwargs
        self.assertEqual(first["page_labels"][4], "5쪽 · A-005 101동 입면도-1")
        self.assertEqual(first["batch"], (1, 3, "1~8쪽"))
        self.assertEqual(architectural["items"][0]["note"], "[1~8쪽] 근거")
        self.assertEqual((architectural["pages_read"], architectural["batches"]), (20, 3))
        self.assertIn("중복될 수 있으니", architectural["summary"])
        self.assertEqual(plan["identified_pages"], 20)

    def test_the_elevation_review_reads_only_elevation_and_section_pages(self):
        plan, _, elevations = self.run_readers()
        self.assertEqual((plan["elevation_pages"], plan["elevation_source"]), ([5, 6, 17], "cad_titles"))
        self.assertEqual(self.elevation.call_count, 1)
        self.assertEqual(self.elevation.call_args.kwargs["page_labels"],
                         ["5쪽 · A-005 101동 입면도-1", "6쪽 · A-006 101동 주동단면도", "17쪽 · A-017 지하주차장 종단면도"])
        self.assertIn("CAD 도면 제목으로 찾은 입면·단면도 3쪽", elevations["notes"][0])

    def test_floors_read_twice_are_kept_once_and_differences_are_reported(self):
        answers = iter([
            {"floor_heights": [{"level": "3층", "height_m": 2.85}, {"level": "지하1층", "height_m": 5.7}],
             "openings": [{"location": "정면", "width_m": 1.5, "height_m": 1.8, "count": 2}], "notes": []},
            {"floor_heights": [{"level": "3 층", "height_m": 2.85}, {"level": "지하1층", "height_m": 4.1}],
             "openings": [{"location": "정면", "width_m": 1.5, "height_m": 1.8, "count": 2},
                          {"location": "배면", "width_m": 1.2, "height_m": 1.5, "count": 4}], "notes": []}])
        elevation = Mock(side_effect=lambda *a, **k: next(answers))
        dwg = cad(**{f"A_{n:03d}": ("입면도" if n <= 10 else "평면도") for n in range(1, 21)})
        _, _, elevations = self.run_readers(dwg=dwg, elevation=elevation)
        self.assertEqual([(r["level"], r["height_m"]) for r in elevations["floor_heights"]], [("3층", 2.85), ("지하1층", 5.7)])
        self.assertIn("지하1층 5.7m / 4.1m(9~10쪽)", elevations["notes"][1])
        self.assertEqual([o["location"] for o in elevations["openings"]], ["정면", "배면"])  # the same row once

    def test_without_cad_titles_the_pdf_drawing_names_then_every_page_are_used(self):
        texts = {3: "1층 입면도", 7: "A-A' 단면도", 9: "평면도"}
        plan, _, _ = self.run_readers(texts=texts, dwg={})
        self.assertEqual((plan["elevation_pages"], plan["elevation_source"]), ([3, 7], "pdf_text"))
        plan, _, elevations = self.run_readers(texts={}, dwg={})
        self.assertEqual((len(plan["elevation_pages"]), plan["elevation_source"]), (20, "all_pages"))
        self.assertIn("따로 찾지 못해 전체 20쪽", elevations["notes"][0])

    def test_pages_past_the_ceiling_and_failed_or_cancelled_batches_are_reported(self):
        failing = Mock(side_effect=lambda dwg, images, *a, batch=None, **k: (
            {"items": [], "summary": "Gemini 분석 요청 중 오류가 발생했습니다: 503", "warnings": [], "missing_info": []}
            if batch[0] == 2 else {"items": [{"category": "미장"}], "summary": "ok", "warnings": [], "missing_info": []}))
        with patch.object(views, "MAX_PDF_PAGES_TO_GEMINI", 20):
            _, architectural, _ = self.run_readers(analyze=failing, total=30)
        self.assertIn("처리 한도(20쪽)를 넘는 21~30쪽은 읽지 않았습니다", architectural["missing_info"][0])
        self.assertIn("[9~16쪽] 건축 수량산출을 읽지 못했습니다: Gemini 분석 요청 중 오류", architectural["missing_info"][1])
        self.assertEqual(len(architectural["items"]), 2)
        calls = iter([False, True])
        _, architectural, _ = self.run_readers(cancel_cb=lambda: next(calls, True))
        self.assertEqual(self.analyze.call_count, 1)
        self.assertEqual(architectural["missing_info"], ["사용자가 취소를 요청하여 9~16쪽은 처리하지 않았습니다.",
                                                         "사용자가 취소를 요청하여 17~20쪽은 처리하지 않았습니다."])

    def test_cad_only_set_asks_once_without_images(self):
        analyze = Mock(return_value={"items": [], "summary": "ok", "warnings": [], "missing_info": []})
        with patch.object(views, "analyze_with_gemini", analyze), patch.object(views, "extract_elevation_section_data") as elevation:
            views.analyze_architectural_drawings(self.dwg, None)
            self.assertEqual(views.review_elevations_and_sections(self.dwg, None), views._EMPTY_ELEVATION_SECTION)
        self.assertEqual(analyze.call_args.args[1], [])
        elevation.assert_not_called()

    def test_each_request_names_its_batch_and_pages(self):
        client = Mock()
        client.models.generate_content.return_value = Mock()
        with patch.object(views, "get_gemini_client", return_value=client), \
                patch.object(views, "_extract_text_from_gemini_response", return_value='{"items": []}'), \
                patch.object(views, "image_to_jpeg_bytes", return_value=b"jpg"):
            views.analyze_with_gemini({}, ["image"], "prompt", "건축도면", page_labels=["117쪽 · A-410 주동단면도"],
                                      batch=(2, 5, "113~120쪽"))
        contents = client.models.generate_content.call_args.kwargs["contents"]
        self.assertIn("2/5번째 묶음(113~120쪽)", contents[0])
        self.assertEqual(contents[1], "[도면 117쪽 · A-410 주동단면도]")
        with patch.object(views, "get_gemini_client", return_value=client), \
                patch.object(views, "_extract_text_from_gemini_response", return_value='{"floor_heights": []}'), \
                patch.object(views, "image_to_jpeg_bytes", return_value=b"jpg"):
            views.extract_elevation_section_data({}, ["image"], page_labels=["117쪽"], batch=(1, 2, "117쪽"))
        self.assertIn("동/건물 이름과 입면 이름", client.models.generate_content.call_args.kwargs["contents"][0])
