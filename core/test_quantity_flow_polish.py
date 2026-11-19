"""The whole flow, walked end to end in a browser: the fixes it needed (drawings inside sheet-set
files, the 3D library shipped with the site, the free path first without PDFs, the result summary
card and the Excel summary sheet)."""
import io
import zipfile
from pathlib import Path

import ezdxf
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase

from core import quantity_floor_rebar as fr
from core import quantity_floor_takeoff as ft
from core import quantity_views as views
from core import test_quantity_floor_takeoff as base

ROOT = Path(__file__).parent
HOME = ROOT / "templates" / "core" / "home.html"


def dxf_bytes(*titles):
    doc = ezdxf.new()
    for i, title in enumerate(titles):
        doc.modelspace().add_text(title, dxfattribs={"insert": (i * 1000, 0), "height": 50})
    stream = io.StringIO()
    doc.write(stream)
    return stream.getvalue().encode("utf-8")


def zipped(files):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    return SimpleUploadedFile("project.zip", buffer.getvalue())


class SheetSetPrecheckTests(SimpleTestCase):
    def test_drawings_inside_sheet_set_files_are_found_by_their_titles(self):
        upload = zipped({
            "구조/S-101~106 101동 구조평면도.dxf": dxf_bytes("101동 2~5층 구조평면도", "101동 기초 구조평면도"),
            "구조/S-301~305 구조 일람표.dxf": dxf_bytes("기둥 일람표", "보 일람표", "주차장 부분은 지하주차장 구조평면도 참조"),
        })
        result = views._build_cad_precheck([upload])
        items = {item["key"]: item for item in result["structural_checklist"]}
        self.assertEqual(items["building_foundation_plan"]["status"], "confirmed")
        self.assertIn("101동 기초 구조평면도", items["building_foundation_plan"]["reason"])
        # a title naming no area is the common schedule of the towers and the parking deck
        self.assertEqual(items["building_column_schedule"]["status"], "confirmed")
        self.assertEqual(items["parking_beam_schedule"]["status"], "confirmed")
        # a note that refers to another plan is not a drawing title
        self.assertEqual(items["parking_structure_plan"]["status"], "missing")
        self.assertEqual(items["general_spec"]["status"], "missing")


class FlowPageTests(SimpleTestCase):
    def setUp(self):
        self.html = HOME.read_text(encoding="utf-8")

    def test_the_3d_library_ships_with_the_site_and_its_absence_is_no_dead_end(self):
        self.assertTrue((ROOT / "static" / "core" / "vendor" / "three-r128.min.js").exists())
        self.assertIn('var THREE_SOURCES = ["{% static \'core/vendor/three-r128.min.js\' %}"', self.html)
        self.assertNotIn("3D 뷰어 라이브러리를 불러오지 못했어요. 네트워크 상태를 확인해 주세요.", self.html)
        self.assertIn("state.skippedThree = true;", self.html)

    def test_without_pdfs_the_free_path_is_the_main_button(self):
        self.assertIn('var cadFirst = !uploadCount("pdf") && uploadCount("cad") > 0;', self.html)

    def test_the_check_card_hides_architectural_noise_and_opens_notes_on_demand(self):
        self.assertIn("var archCad = (state.elevCadFiles || []).length > 0;", self.html)
        self.assertIn('class="qd-btn link qd-item-note"', self.html)

    def test_the_summary_opens_its_own_card(self):
        self.assertIn("function resultSummary(takeoff, rebar, assumedOpen, arch)", self.html)
        self.assertIn('deck.close();  // the rebar card is done', self.html)
        self.assertIn('label: "Summary"', self.html)

    def test_an_empty_list_of_missing_items_says_nothing(self):
        self.assertNotIn("'아직 빠진 항목: ' + esc((rebar.not_included || []).join", self.html)

    def test_the_download_keeps_its_name(self):
        self.assertIn("setTimeout(function () { URL.revokeObjectURL(url); }, 60000);", self.html)


class ExcelSummaryTests(SimpleTestCase):
    def test_the_summary_sheet_adds_the_buildings_up(self):
        import openpyxl
        from openpyxl.styles import Alignment, Border, Font, PatternFill
        model = base.FloorRuleTests().model()
        takeoff, rebar = ft.build_takeoff(model, foundations={"101동": 1.0}), fr.build_rebar(model)
        workbook = openpyxl.Workbook()
        views._write_summary_sheet(workbook.active, takeoff, rebar, PatternFill(), Font(), Font(), Alignment(), Alignment(), Border())
        rows = [[c.value for c in row] for row in workbook.active.iter_rows()]
        building = next(r for r in rows if r[0] == "101동")
        total = next(r for r in rows if r[0] == "합계")
        self.assertAlmostEqual(building[1], takeoff["buildings"][0]["totals"]["concrete"]["total"], places=1)
        self.assertEqual(building[1:4], total[1:4])
        self.assertTrue(any(isinstance(r[0], str) and "임의로 정한 값" in r[0] for r in rows))
