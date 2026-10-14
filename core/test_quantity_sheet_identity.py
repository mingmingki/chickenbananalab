"""Per-sheet drawing identity from CAD title blocks and PDF title-block text."""
import io
import ezdxf
from django.test import SimpleTestCase

from core import quantity_sheet_identity as sid

FRAME_W, FRAME_H = 841.0, 594.0
# Cell labels of the office A3 form (template-local coordinates).
LABELS = [("도면명  DRAWING TITLE", 761, 84), ("축척  SCALE", 761, 59), ("작성일자  DATE", 791, 59),
          ("도면번호  DRAWING NO.", 761, 44), ("승인  APPROVED BY", 791, 114)]


def add_form(layout, origin=(0, 0)):
    ox, oy = origin
    layout.add_lwpolyline([(ox, oy), (ox + FRAME_W, oy), (ox + FRAME_W, oy + FRAME_H), (ox, oy + FRAME_H)], close=True)
    for text, x, y in LABELS:
        layout.add_text(text, dxfattribs={"height": 2.0, "insert": (ox + x, oy + y), "layer": "a-form"})


def form_doc():
    doc = ezdxf.new()
    add_form(doc.modelspace())
    return doc


def local_to_world(insert, scale, rotation, point):
    import math
    a = math.radians(rotation)
    x, y = point[0] * scale, point[1] * scale
    return (insert[0] + x * math.cos(a) - y * math.sin(a), insert[1] + x * math.sin(a) + y * math.cos(a))


def add_sheet_values(msp, insert, number, title, *, scale=1.0, rotation=0.0, layer="TEXT"):
    for text, point, height in ((title, (772, 71), 3.0), ("A1:NONE", (767, 54), 2.5),
                                ("A3:NONE", (767, 49), 2.5), (number, (784, 34), 3.0)):
        if text is None:
            continue
        msp.add_text(text, dxfattribs={"height": height * scale, "layer": layer, "rotation": rotation,
                                       "insert": local_to_world(insert, scale, rotation, point)})


def xref_host(sheets, *, name="XR-FORM-A(A3)", scale=1.0, rotation=0.0, step=FRAME_W):
    doc = ezdxf.new()
    doc.add_xref_def(name + ".dwg", name)
    msp = doc.modelspace()
    for i, (number, title) in enumerate(sheets):
        insert = (i * step * scale, 0.0)
        msp.add_blockref(name, insert, dxfattribs={"xscale": scale, "yscale": scale, "rotation": rotation})
        add_sheet_values(msp, insert, number, title, scale=scale, rotation=rotation)
    return doc


def bound_host(sheets, *, scale=200.0):
    doc = ezdxf.new()
    block = doc.blocks.new("XR-FORM-A(A3)")
    add_form(block)
    msp = doc.modelspace()
    for i, (number, title) in enumerate(sheets):
        insert = (i * FRAME_W * scale, 0.0)
        msp.add_blockref("XR-FORM-A(A3)", insert, dxfattribs={"xscale": scale, "yscale": scale})
        add_sheet_values(msp, insert, number, title, scale=scale)
    return doc


def evidence(**docs):
    return {name: sid.extract_sheet_evidence(doc, source_member_sha="sha-" + name) for name, doc in docs.items()}


class TemplateCellTests(SimpleTestCase):
    def test_cells_come_from_the_forms_own_labels(self):
        template = sid.extract_sheet_evidence(form_doc())["self_template"]
        cells = template["cells"]
        self.assertTrue(cells["number"][1] <= 34 <= cells["number"][3] and cells["number"][0] <= 784 <= cells["number"][2])
        self.assertTrue(cells["title"][1] <= 71 <= cells["title"][3])
        # The date cell to the right bounds the scale cell; the scale cell stays above the number label.
        self.assertLess(cells["scale"][2], 791)
        self.assertGreater(cells["scale"][1], 44)
        self.assertGreater(cells["title"][1], 59)

    def test_drawing_without_number_label_is_not_a_template(self):
        doc = ezdxf.new()
        doc.modelspace().add_text("도면명  DRAWING TITLE", dxfattribs={"insert": (761, 84), "height": 2})
        self.assertIsNone(sid.extract_sheet_evidence(doc)["self_template"])


class CadSheetTests(SimpleTestCase):
    def test_xref_frames_borrow_the_form_from_the_same_upload(self):
        sheets = sid.resolve_cad_sheets(evidence(
            **{"구조/XR-FORM-A(A3).dwg": form_doc(),
               "구조/S-311~324 벽체일람표.dwg": xref_host([("S-311", "101동 벽체 일람표-1"), ("S-312", "101동 벽체 일람표-2")])}))
        rows = sheets["구조/S-311~324 벽체일람표.dwg"]
        self.assertEqual([r["drawing_number"] for r in rows], ["S-311", "S-312"])
        self.assertEqual([r["drawing_title"] for r in rows], ["101동 벽체 일람표-1", "101동 벽체 일람표-2"])
        self.assertTrue(all(r["identity_status"] == "identified" and r["method"] == "template_labels" for r in rows))
        self.assertEqual(rows[0]["scale"], "A1:NONE A3:NONE")
        self.assertEqual(sheets["구조/XR-FORM-A(A3).dwg"], [])

    def test_mtext_title_value_is_read(self):
        doc = xref_host([("S-401", None)])
        doc.modelspace().add_mtext("지하2층 지하주차장 구조평면도", dxfattribs={"insert": (772, 71), "char_height": 3, "layer": "TEXT"})
        rows = sid.resolve_cad_sheets(evidence(**{"XR-FORM-A(A3).dwg": form_doc()}, host=doc))["host"]
        self.assertEqual((rows[0]["drawing_number"], rows[0]["drawing_title"]), ("S-401", "지하2층 지하주차장 구조평면도"))

    def test_bound_scaled_block_frames(self):
        rows = sid.resolve_cad_sheets(evidence(host=bound_host([("A-030", "지상층 방화구획도-1"), ("A-031", "지상층 방화구획도-2")])))["host"]
        self.assertEqual([(r["drawing_number"], r["drawing_title"]) for r in rows],
                         [("A-030", "지상층 방화구획도-1"), ("A-031", "지상층 방화구획도-2")])

    def test_rotated_scaled_xref_without_form_learns_the_number_cell(self):
        host = xref_host([("A-505", "코아 단면도-1"), ("A-510", "코아 단면도-2"), ("A-525", "코아 단면도-3")],
                         name="XR-FORM(GBA3)", scale=75.09, rotation=270)
        rows = sid.resolve_cad_sheets(evidence(host=host))["host"]
        self.assertEqual([r["drawing_number"] for r in rows], ["A-505", "A-510", "A-525"])
        self.assertTrue(all(r["method"] == "learned_number_cell" for r in rows))
        self.assertTrue(all(r["drawing_title"] is None for r in rows))

    def test_learned_cell_borrows_title_cell_from_a_matching_known_form(self):
        rows = sid.resolve_cad_sheets(evidence(
            form=form_doc(),
            host=xref_host([("A-505", "코아 단면도-1"), ("A-510", "코아 단면도-2")], name="XR-FORM(GBA3)", scale=75.09, rotation=270),
        ))["host"]
        self.assertEqual([r["drawing_title"] for r in rows], ["코아 단면도-1", "코아 단면도-2"])

    def test_inconsistent_numbers_do_not_create_learned_frames(self):
        doc = ezdxf.new()
        doc.add_xref_def("XR-PLAN.dwg", "XR-PLAN")
        msp = doc.modelspace()
        for i, (x, y) in enumerate([(784, 34), (300, 300)]):
            msp.add_blockref("XR-PLAN", (i * 5000, 0))
            msp.add_text(f"A-10{i}", dxfattribs={"insert": (i * 5000 + x, y), "height": 3})
        self.assertEqual(sid.resolve_cad_sheets(evidence(host=doc))["host"], [])

    def test_missing_and_conflicting_numbers_stay_visible(self):
        doc = xref_host([("S-001", "도면목록표"), (None, "도면목록표")])
        doc.modelspace().add_text("S-099", dxfattribs={"insert": (784, 30), "height": 3, "layer": "TEXT"})
        rows = sid.resolve_cad_sheets(evidence(**{"XR-FORM-A(A3).dwg": form_doc()}, host=doc))["host"]
        self.assertEqual(rows[0]["identity_status"], "conflict")
        self.assertIsNone(rows[0]["drawing_number"])
        self.assertEqual(sorted(rows[0]["number_candidates"]), ["S-001", "S-099"])
        self.assertEqual(rows[1]["identity_status"], "title_only")

    def test_registry_flags_duplicate_numbers_and_orders_sheets(self):
        sheets = sid.resolve_cad_sheets(evidence(
            **{"XR-FORM-A(A3).dwg": form_doc()},
            b=xref_host([("S-112", "b"), ("S-101", "a")]),
            a=xref_host([("S-011", "x"), ("S-011", "y")]),
        ))
        registry = sid.build_sheet_registry(sheets)
        self.assertEqual([r["drawing_number"] for r in registry["sheets"]], ["S-011", "S-011", "S-101", "S-112"])
        self.assertIn({"reason": "cad_duplicate_number", "drawing_number": "S-011", "count": 2}, registry["issues"])


def numbered_pdf(numbers):
    """A3 portrait pages: the number in the title-block corner, a member mark in the body."""
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
    writer = PdfWriter()
    for number in numbers:
        page = writer.add_blank_page(width=842, height=1191)
        font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"),
                                 NameObject("/BaseFont"): NameObject("/Helvetica")})
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})})
        body = "BT /F1 9 Tf 100 900 Td (C1 G101) Tj ET"
        corner = f" BT /F1 9 Tf 760 40 Td ({number}) Tj ET" if number else " BT /F1 9 Tf 760 40 Td (NONE) Tj ET"
        stream = DecodedStreamObject()
        stream.set_data((body + corner).encode("ascii"))
        page[NameObject("/Contents")] = writer._add_object(stream)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def index_host(rows_by_frame):
    """Index sheets: each frame lists (number, title, scale) rows in two columns."""
    doc = xref_host([("S-001", "도면목록표") for _ in rows_by_frame])
    msp = doc.modelspace()
    for f, rows in enumerate(rows_by_frame):
        for i, (number, title, scale) in enumerate(rows):
            column, row = divmod(i, 3)
            x, y = f * FRAME_W + 60 + column * 350, 500 - row * 12
            msp.add_text(number, dxfattribs={"insert": (x, y), "height": 3, "layer": "T"})
            msp.add_text(title, dxfattribs={"insert": (x + 40, y), "height": 3, "layer": "T"})
            msp.add_text(scale, dxfattribs={"insert": (x + 250, y), "height": 3, "layer": "T"})
    return doc


class IndexOrderTests(SimpleTestCase):
    def registry(self, **docs):
        ev = evidence(**{"XR-FORM-A(A3).dwg": form_doc()}, **docs)
        return sid.build_sheet_registry(sid.resolve_cad_sheets(ev), ev)

    def test_index_rows_are_read_in_column_order(self):
        ev = evidence(**{"XR-FORM-A(A3).dwg": form_doc()}, idx=index_host([
            [("S-001", "구조 도면목록표-1", "NONE"), ("S-002", "구조 도면목록표-2", "NONE"), ("S-011", "구조일반사항-1", "NONE"),
             ("S-111", "74P2(상부)형 단위세대 구조평면도", "1/80"), ("S-111", "101동 지하2층 구조평면도", "1/200")]]))
        entries = sid.parse_drawing_index(sid.resolve_cad_sheets(ev), ev)
        self.assertEqual([(e["drawing_number"], e["drawing_title"]) for e in entries],
                         [("S-001", "구조 도면목록표-1"), ("S-002", "구조 도면목록표-2"), ("S-011", "구조일반사항-1"),
                          ("S-111", "74P2(상부)형 단위세대 구조평면도"), ("S-111", "101동 지하2층 구조평면도")])

    def test_index_orders_same_number_sheets_by_title(self):
        registry = self.registry(
            idx=index_host([[("S-001", "구조 도면목록표-1", "NONE"), ("S-111", "74P2(상부)형 단위세대 구조평면도", "1/80"),
                             ("S-112", "84P1형 단위세대 구조평면도", "1/80"), ("S-111", "101동 지하2층 구조평면도", "1/200"),
                             ("S-112", "101동 지하1층 구조평면도", "1/200")]]),
            unit=xref_host([("S-111", "74P2(상부)형 단위세대 구조평면도"), ("S-112", "84P1형 단위세대 구조평면도")]),
            dong=xref_host([("S-111", "101동 지하 2층 구조평면도"), ("S-112", "101동 지하 1층 구조평면도")]))
        sequence = registry["sequence"]["S"]
        self.assertEqual([(slot["drawing_number"], slot["cad_sheet"]["file"]) for slot in sequence],
                         [("S-001", "idx"), ("S-111", "unit"), ("S-112", "unit"), ("S-111", "dong"), ("S-112", "dong")])

    def test_index_entry_matches_mislabeled_duplicate_by_title(self):
        registry = self.registry(idx=index_host([
            [("S-001", "구조 도면목록표-1", "NONE"), ("S-002", "구조 도면목록표-2", "NONE")], [("S-011", "구조일반사항-1", "NONE")]]),
            notes=xref_host([("S-011", "구조일반사항-1")]))
        sequence = registry["sequence"]["S"]
        self.assertEqual([slot["drawing_number"] for slot in sequence], ["S-001", "S-002", "S-011"])
        self.assertEqual(sequence[1]["cad_sheet"]["drawing_number"], "S-001")
        self.assertIn("cad_number_differs_from_index", sequence[1]["warnings"])

    def test_revisions_with_same_title_collapse_to_one_slot(self):
        registry = self.registry(**{"A-017 배치도.dwg": xref_host([("A-017", "건물 배치도")]),
                                    "A-017 배치도(2차보완).dwg": xref_host([("A-017", "건물 배치도")]),
                                    "A-018 단면도.dwg": xref_host([("A-018", "대지 단면도")])})
        sequence = registry["sequence"]["A"]
        self.assertEqual([slot["drawing_number"] for slot in sequence], ["A-017", "A-018"])
        self.assertIn("cad_multiple_versions", sequence[0]["warnings"])
        self.assertEqual(len(sequence[0]["alternates"]), 1)

    def test_frame_title_differing_from_index_is_flagged(self):
        registry = self.registry(idx=index_host([[("S-331", "아파트 보 일람표-1", "NONE")]]),
                                 beams=xref_host([("S-331", "아파트 기둥 일람표")]))
        slot = [s for s in registry["sequence"]["S"] if s["drawing_number"] == "S-331"][0]
        self.assertIn("cad_title_differs_from_index", slot["warnings"])
        self.assertEqual(slot["index_title"], "아파트 보 일람표-1")

    def test_frame_missing_from_index_is_reported_not_aligned(self):
        registry = self.registry(idx=index_host([[("S-011", "구조일반사항-1", "NONE"), ("S-012", "구조일반사항-2", "NONE")]]),
                                 notes=xref_host([("S-011", "구조일반사항-1"), ("S-011", "옛 일반사항"), ("S-012", "구조일반사항-2")]))
        self.assertEqual([slot["drawing_number"] for slot in registry["sequence"]["S"]], ["S-011", "S-012"])
        self.assertIn({"reason": "cad_sheet_not_in_index", "drawing_number": "S-011", "file": "notes"}, registry["issues"])

    def test_spacing_and_punctuation_are_not_title_differences(self):
        registry = self.registry(idx=index_host([[("S-115", "101동 지상3~지상13층 구조평면도", "1/200"),
                                                  ("S-213", "실내.외재료마감표(아파트)", "NONE")]]),
                                 plans=xref_host([("S-115", "101동 지상 3층~지상 13층 구조평면도"),
                                                  ("S-213", "실내,외재료마감표 (아파트)")]))
        warned = [slot["drawing_number"] for slot in registry["sequence"]["S"] if "cad_title_differs_from_index" in slot["warnings"]]
        self.assertEqual(warned, [])

    def test_pdf_alignment_follows_index_for_duplicate_numbers(self):
        registry = self.registry(
            idx=index_host([[("S-001", "구조 도면목록표-1", "NONE"), ("S-111", "74P2(상부)형 단위세대 구조평면도", "1/80"),
                             ("S-112", "84P1형 단위세대 구조평면도", "1/80"), ("S-111", "101동 지하2층 구조평면도", "1/200"),
                             ("S-112", "101동 지하1층 구조평면도", "1/200"), ("S-113", "101동 지상1층 구조평면도", "1/200")]]),
            unit=xref_host([("S-111", "74P2(상부)형 단위세대 구조평면도"), ("S-112", "84P1형 단위세대 구조평면도")]),
            dong=xref_host([("S-111", "101동 지하 2층 구조평면도"), ("S-112", "101동 지하 1층 구조평면도"), ("S-113", "101동 지상1층 구조평면도")]))
        pages = [{"page": i + 1, "drawing_number": n, "text_available": True}
                 for i, n in enumerate([None, None, None, "S-111", "S-112", "S-113"])]
        rows = sid.assign_pdf_identities(pages, registry)
        self.assertEqual([(r["drawing_number"], (r["cad_sheet"] or {}).get("file")) for r in rows],
                         [("S-001", "idx"), ("S-111", "unit"), ("S-112", "unit"), ("S-111", "dong"), ("S-112", "dong"), ("S-113", "dong")])


class LearnedFrameDedupTests(SimpleTestCase):
    def test_second_xref_at_the_same_offset_does_not_duplicate_frames(self):
        doc = xref_host([("A-505", "코아 단면도-1"), ("A-510", "코아 단면도-2")], name="XR-FORM(GBA3)")
        doc.add_xref_def("XR-PLAN.dwg", "XR-PLAN")
        for i in range(2):
            doc.modelspace().add_blockref("XR-PLAN", (i * FRAME_W, 0))
        rows = sid.resolve_cad_sheets(evidence(host=doc))["host"]
        self.assertEqual([r["drawing_number"] for r in rows], ["A-505", "A-510"])


def bbox_page(words, width=842, height=1191):
    rows = "".join(f'<word xMin="{x}" yMin="{y}" xMax="{x + 20}" yMax="{y + 6}">{text}</word>' for text, x, y in words)
    return f'<page width="{width}" height="{height}">{rows}</page>'


class PdfIdentityTests(SimpleTestCase):
    def test_dominant_title_block_position_is_learned_per_document(self):
        pages = [bbox_page([(f"A-0{10 + i}", 1110, 785)]) for i in range(4)]
        pages.append(bbox_page([("A-099", 300, 200)]))  # body reference elsewhere
        pages.append(bbox_page([("A-020", 1110, 785), ("A-021", 1110, 785)]))  # two numbers in the cell
        pages.append(bbox_page([]))
        result = sid.parse_pdf_title_block_numbers("<doc>" + "".join(pages) + "</doc>")
        self.assertEqual([p["drawing_number"] for p in result], ["A-010", "A-011", "A-012", "A-013", None, None, None])
        self.assertEqual(result[5]["reason"], "multiple_numbers_in_title_block")
        self.assertEqual(result[6]["reason"], "no_text_layer")

    def test_real_pdf_through_poppler(self):
        result = sid.pdf_title_block_numbers(numbered_pdf(["S-101", "S-102", "S-103", None]))
        self.assertEqual([p["drawing_number"] for p in result], ["S-101", "S-102", "S-103", None])
        self.assertEqual(result[3]["reason"], "no_number_in_title_block")


def sheets_seq(numbers, prefix_file="f"):
    return {"sheets": [{"drawing_number": n, "drawing_title": f"title {n}", "identity_status": "identified",
                        "file": prefix_file, "frame_handle": str(i), "role": "other"} for i, n in enumerate(numbers)],
            "issues": []}


class AlignmentTests(SimpleTestCase):
    def pages(self, values):
        return [{"page": i + 1, "drawing_number": v, "text_available": True} for i, v in enumerate(values)]

    def test_gap_with_matching_count_is_filled_by_order(self):
        registry = sheets_seq(["A-001", "A-002", "A-003", "A-004", "A-005"])
        rows = sid.assign_pdf_identities(self.pages(["A-001", None, None, "A-004", None]), registry)
        self.assertEqual([r["drawing_number"] for r in rows], ["A-001", "A-002", "A-003", "A-004", "A-005"])
        self.assertEqual([r["identity_status"] for r in rows],
                         ["identified", "inferred_order", "inferred_order", "identified", "inferred_order"])
        self.assertEqual(rows[1]["drawing_title"], "title A-002")
        self.assertEqual(rows[1]["source_method"], "page_order_between_title_block_numbers")

    def test_gap_with_different_count_stays_unresolved(self):
        registry = sheets_seq(["A-001", "A-002", "A-003", "A-004"])
        rows = sid.assign_pdf_identities(self.pages(["A-001", None, "A-004"]), registry)
        self.assertEqual([r["identity_status"] for r in rows], ["identified", "unidentified", "identified"])
        self.assertEqual(rows[1]["reason"], "page_count_differs_from_cad_sheets")

    def test_out_of_order_anchors_disable_order_inference(self):
        registry = sheets_seq(["A-001", "A-002", "A-003", "A-004"])
        rows = sid.assign_pdf_identities(self.pages(["A-003", None, "A-001", None]), registry)
        self.assertEqual([r["identity_status"] for r in rows], ["identified", "unidentified", "identified", "unidentified"])

    def test_number_without_cad_sheet_is_number_only(self):
        rows = sid.assign_pdf_identities(self.pages(["A-777"]), sheets_seq(["A-001"]))
        self.assertEqual(rows[0]["identity_status"], "number_only")

    def test_other_discipline_sheets_are_not_used(self):
        registry = sheets_seq(["A-001", "S-001", "S-002", "S-003"])
        rows = sid.assign_pdf_identities(self.pages(["S-001", None, "S-003"]), registry)
        self.assertEqual([r["drawing_number"] for r in rows], ["S-001", "S-002", "S-003"])

    def test_duplicate_number_anchor_is_not_used_but_counts_as_a_sheet(self):
        registry = sheets_seq(["S-001", "S-001", "S-011", "S-012"])
        rows = sid.assign_pdf_identities(self.pages([None, None, "S-011", "S-012"]), registry)
        self.assertEqual([r["drawing_number"] for r in rows], ["S-001", "S-001", "S-011", "S-012"])
        self.assertEqual(rows[0]["identity_status"], "inferred_order")
        self.assertIn("cad_duplicate_number", rows[0]["warnings"])
