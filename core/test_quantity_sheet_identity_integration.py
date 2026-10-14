"""Sheet identity through the real quantity paths: ZIP parse, manifest, AI summary, Excel."""
import io
import json
import zipfile

from django.test import RequestFactory, SimpleTestCase

from core import quantity_sources, quantity_views as views
from core.test_quantity_sheet_identity import form_doc, index_host, numbered_pdf, xref_host


def dxf_bytes(doc):
    stream = io.StringIO()
    doc.write(stream)
    return stream.getvalue().encode("utf-8")


def structural_zip():
    bundle = io.BytesIO()
    with zipfile.ZipFile(bundle, "w") as archive:
        archive.writestr("구조/XR-FORM-A(A3).dxf", dxf_bytes(form_doc()))
        archive.writestr("구조/S-001 도면목록표.dxf", dxf_bytes(index_host([[
            ("S-001", "구조 도면목록표", "NONE"), ("S-011", "구조일반사항-1", "NONE"), ("S-012", "구조일반사항-2", "NONE")]])))
        archive.writestr("구조/S-011~012 구조일반사항.dxf", dxf_bytes(xref_host([
            ("S-011", "구조일반사항-1"), ("S-012", "구조일반사항-2")])))
    return bundle.getvalue()


class ZipParseTests(SimpleTestCase):
    def test_zip_parse_keeps_sheets_and_index_rows(self):
        parsed = views.parse_dwg_from_zip(structural_zip())
        notes = parsed["구조/S-011~012 구조일반사항.dxf"]["sheets"]
        self.assertEqual([(s["drawing_number"], s["drawing_title"]) for s in notes],
                         [("S-011", "구조일반사항-1"), ("S-012", "구조일반사항-2")])
        self.assertEqual(notes[0]["source_member_sha"], parsed["구조/S-011~012 구조일반사항.dxf"]["source_member_sha"])
        entries = parsed["구조/S-001 도면목록표.dxf"]["index_entries"]
        self.assertEqual([e["drawing_number"] for e in entries], ["S-001", "S-011", "S-012"])
        self.assertEqual(parsed["구조/XR-FORM-A(A3).dxf"]["sheets"], [])

    def test_ai_summary_does_not_carry_sheet_evidence(self):
        summary = views._ai_cad_summary(views.parse_dwg_from_zip(structural_zip()))
        for data in summary.values():
            self.assertNotIn("sheets", data)
            self.assertNotIn("index_entries", data)
            self.assertNotIn("source_texts", data)


class ManifestTests(SimpleTestCase):
    def manifest(self, numbers):
        cad = views.parse_dwg_from_zip(structural_zip())
        pdf = numbered_pdf(numbers)
        return views._build_source_manifest(cad, pdf)

    def test_pages_get_cad_titles_roles_and_bounded_order(self):
        manifest = self.manifest(["S-001", None, "S-012"])
        pages = manifest["pdf_pages"]
        self.assertEqual([(p["drawing_number"], p["identity_status"]) for p in pages[:3]],
                         [("S-001", "identified"), ("S-011", "inferred_order"), ("S-012", "identified")])
        self.assertEqual(pages[1]["drawing_title"], "구조일반사항-1")
        self.assertEqual(pages[1]["role"], "general_notes")
        self.assertEqual(pages[1]["source_method"], "page_order_between_title_block_numbers")
        self.assertEqual(pages[0]["role"], "drawing_index")
        link = next(l for l in manifest["links"] if l["pdf_page"] == 2)
        self.assertEqual((link["cad_file"], link["drawing_number"], link["identity_status"]),
                         ("구조/S-011~012 구조일반사항.dxf", "S-011", "inferred_order"))
        self.assertTrue(link["cad_frame_handle"])
        self.assertFalse(link["quantity_verified"])
        reasons = {(i.get("page"), i["reason"]) for i in manifest["issues"]}
        self.assertIn((2, "pdf_identity_inferred_order"), reasons)
        self.assertFalse(manifest["identity_complete"])
        cad = {row["file"]: row for row in manifest["cad_files"]}
        self.assertEqual(cad["구조/S-011~012 구조일반사항.dxf"]["identity_status"], "multi_sheet")
        self.assertEqual(cad["구조/S-011~012 구조일반사항.dxf"]["drawing_numbers"], ["S-011", "S-012"])
        self.assertEqual(len(manifest["cad_sheets"]), 3)

    def test_explicit_text_fields_still_win(self):
        manifest = quantity_sources.build_source_manifest(
            {1: "DRAWING NO: S-777\nDRAWING TITLE: COLUMN SCHEDULE"}, document_sha="d", total_pages=1,
            pdf_title_numbers=[{"page": 1, "drawing_number": "S-001", "text_available": True}])
        self.assertEqual(manifest["pdf_pages"][0]["drawing_number"], "S-777")
        self.assertEqual(manifest["pdf_pages"][0]["source_method"], "explicit_text_fields")

    def test_member_refs_carry_inferred_status_and_title(self):
        manifest = self.manifest(["S-001", None, "S-012"])
        members = quantity_sources.attach_member_sources(
            {"walls": [{"mark": "W1", "bbox": {"page": 2, "x": 0.1, "y": 0.1, "w": 0.1, "h": 0.1}}]}, manifest)
        ref = members["walls"][0]["source_refs"][0]
        self.assertEqual((ref["drawing_number"], ref["drawing_title"], ref["identity_status"]),
                         ("S-011", "구조일반사항-1", "inferred_order"))
        self.assertEqual(members["walls"][0]["source_review"]["status"], "definition_only")


class ExcelTests(SimpleTestCase):
    def test_source_sheet_shows_korean_identity_and_cad_frames(self):
        from openpyxl import load_workbook
        cad = views.parse_dwg_from_zip(structural_zip())
        manifest = views._build_source_manifest(cad, numbered_pdf(["S-001", None, "S-012"]))
        request = RequestFactory().post("/api/quantity/download-excel/",
                                        json.dumps({"results": {"structural": {"source_manifest": manifest}}}),
                                        content_type="application/json")
        workbook = load_workbook(io.BytesIO(views._build_excel_response(request).content))
        rows = [list(row) for row in workbook["도면 출처"].values]
        self.assertIn("식별 근거", rows[0])
        page2 = next(r for r in rows if r[1] == "PDF" and r[2] == "2")
        self.assertIn("순서로 추정", page2)
        self.assertIn("S-011", page2)
        self.assertTrue(any(r[1] == "CAD 도곽" and r[3] == "S-012" for r in rows))
        self.assertTrue(all(cell.data_type != "f" for row in workbook["도면 출처"] for cell in row))
