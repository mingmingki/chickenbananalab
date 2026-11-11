"""The free CAD-only path: the floor job also reads the schedules from the structural CAD (no AI),
and the takeoff, the rebar and the Excel use them when no AI reading is there."""
import io
import json
import os
import tempfile
from pathlib import Path
from unittest import mock

import ezdxf
import openpyxl
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase, override_settings

from core import quantity_floor_rebar as fr
from core import quantity_floor_takeoff as ft
from core import quantity_views as views
from core.test_quantity_floor_rebar import links, model

HOME = Path(__file__).parent / "templates" / "core" / "home.html"


def state_db(test):
    tmp = tempfile.TemporaryDirectory()
    test.addCleanup(tmp.cleanup)
    override = override_settings(QUANTITY_STATE_DB=str(Path(tmp.name) / "state.sqlite3"))
    override.enable()
    test.addCleanup(override.disable)
    return tmp.name


class FloorInputsTests(SimpleTestCase):
    def test_structural_sheets_ride_along_with_the_plans(self):
        names = ["S-111 1층 구조평면도.dwg", "S-311~324 벽체일람표.dwg", "S-211 단위세대 슬래브배근도.dwg",
                 "A-501 창호도.dwg", "readme.txt"]
        kept = [name for name, _data in views._floor_view_inputs([SimpleUploadedFile(n, b"x") for n in names])]
        self.assertEqual(kept, names[:3])


class CadLinksTests(TestCase):
    def setUp(self):
        self.dir = state_db(self)

    def dxf(self, name, text):
        doc = ezdxf.new()
        doc.modelspace().add_text(text)
        path = os.path.join(self.dir, name)
        doc.saveas(path)
        return path

    def test_structural_files_are_read_once_and_linked(self):
        plan = self.dxf("plan.dxf", "PLAN")
        schedule = self.dxf("schedule.dxf", "SCHEDULE")
        copy = os.path.join(self.dir, "copy.dxf")
        Path(copy).write_bytes(Path(schedule).read_bytes())
        broken = os.path.join(self.dir, "broken.dxf")
        Path(broken).write_text("not a dxf")
        dwg = os.path.join(self.dir, "wall.dwg")
        Path(dwg).write_bytes(b"dwg")
        files = [{"name": "S-111 1층 구조평면도.dwg", "path": os.path.join(self.dir, "plan.dwg")},
                 {"name": "S-311 벽체일람표.dxf", "path": schedule},
                 {"name": "S-311 벽체일람표 (2).dxf", "path": copy},        # the same sheet from another folder
                 {"name": "S-321 보일람표.dxf", "path": broken},
                 {"name": "S-331 기둥일람표.dwg", "path": dwg},
                 {"name": "A-101 1층 평면도.dxf", "path": plan}]           # architectural: not read
        Path(files[0]["path"]).write_bytes(b"plan dwg")
        manifest = [{"name": "S-111 1층 구조평면도.dwg", "dxf": plan}]       # already opened for the pictures
        converted = ezdxf.new()
        converted.modelspace().add_text("CONVERTED")
        stream = io.StringIO()
        converted.write(stream)
        seen = {}

        def build(dwg_data):
            seen.update(dwg_data)
            return {"marks": [{"mark": "W1"}]}

        with mock.patch.object(views, "_cbl_free_dwg_to_dxf_text_v1", return_value=(stream.getvalue(), {})) as convert, \
                mock.patch.object(views.quantity_member_links, "build_member_links", side_effect=build):
            out = views._cad_member_links("job-1", files, manifest)
        self.assertEqual(out, {"marks": [{"mark": "W1"}]})
        self.assertEqual(convert.call_count, 1)  # only the DWG nobody opened yet
        self.assertEqual(sorted(seen), ["S-111 1층 구조평면도.dwg", "S-311 벽체일람표.dxf", "S-331 기둥일람표.dwg"])
        self.assertTrue(all(data.get("source_member_sha") for data in seen.values()))

    def test_no_structural_files_means_no_links(self):
        self.assertIsNone(views._cad_member_links("job-1", [{"name": "A-101 1층 평면도.dxf", "path": "x"}], []))

    def test_the_floor_job_keeps_the_links_for_the_takeoff(self):
        plan = self.dxf("S-111 1층 구조평면도.dxf", "PLAN")
        with mock.patch.object(views, "_cad_member_links", return_value=links()), \
                mock.patch.object(views, "_render_floor_views") as render, \
                mock.patch.object(views, "_keep_floor_inputs"):
            views._run_floor_views_job("fv-9", [("S-111 1층 구조평면도.dxf", Path(plan).read_bytes())])
        render.assert_called_once()
        self.assertEqual(views._floor_links("fv-9"), links())

    def test_a_schedule_failure_does_not_stop_the_pictures(self):
        plan = self.dxf("S-111 1층 구조평면도.dxf", "PLAN")
        with mock.patch.object(views, "_cad_member_links", side_effect=RuntimeError("x")), \
                mock.patch.object(views, "_render_floor_views") as render, \
                mock.patch.object(views, "_keep_floor_inputs"):
            views._run_floor_views_job("fv-9", [("S-111 1층 구조평면도.dxf", Path(plan).read_bytes())])
        render.assert_called_once()
        self.assertIsNone(views._floor_links("fv-9"))


class CadOnlyApiTests(TestCase):
    def setUp(self):
        state_db(self)
        self.staff = get_user_model().objects.create_user("staff", password="staff-pass-1", is_staff=True)
        self.client.force_login(self.staff)
        views._quantity_state().begin_job("fv-1", self.staff.pk, "api_quantity_floor_views", "x")
        views._result_set("fv-1", {"ok": True, "results": {"floor_views": {"model": model()}}})

    def post(self, url, body):
        return self.client.post(url, json.dumps(body), content_type="application/json")

    def test_takeoff_and_rebar_use_the_schedules_read_with_the_floor_job(self):
        bare = self.post("/api/quantity/floor-rebar/", {"floor_job_id": "fv-1"}).json()["rebar"]
        views._quantity_state().put("floor_links", "fv-1", {"links": links()}, ttl=3600)
        takeoff = self.post("/api/quantity/floor-takeoff/", {"floor_job_id": "fv-1"}).json()["takeoff"]
        self.assertTrue(takeoff["uses_schedules"])
        rebar = self.post("/api/quantity/floor-rebar/", {"floor_job_id": "fv-1"}).json()["rebar"]
        expected = fr.build_rebar(model(), links(), ft.build_takeoff(model(), links()))
        total = lambda r: sum(row["kg"]["total"] for b in r["buildings"] for row in b["rows"])
        self.assertAlmostEqual(total(rebar), total(expected), places=3)
        self.assertGreater(total(rebar), total(bare))

    def test_the_ai_reading_still_wins_when_it_is_sent(self):
        views._quantity_state().put("floor_links", "fv-1", {"links": {"marks": [{"category": "columns", "mark": "X"}]}}, ttl=3600)
        sent = self.post("/api/quantity/floor-rebar/", {"floor_job_id": "fv-1", "member_links": links()}).json()["rebar"]
        expected = fr.build_rebar(model(), links(), ft.build_takeoff(model(), links()))
        total = lambda r: sum(row["kg"]["total"] for b in r["buildings"] for row in b["rows"])
        self.assertAlmostEqual(total(sent), total(expected), places=3)

    def test_the_excel_is_made_without_any_ai_result(self):
        views._quantity_state().put("floor_links", "fv-1", {"links": links()}, ttl=3600)
        takeoff = self.post("/api/quantity/floor-takeoff/", {"floor_job_id": "fv-1"}).json()["takeoff"]
        rebar = self.post("/api/quantity/floor-rebar/", {"floor_job_id": "fv-1"}).json()["rebar"]
        response = self.post("/api/quantity/download-excel/", {"results": {"floor_takeoff": takeoff, "floor_rebar": rebar}})
        self.assertEqual(response.status_code, 200)
        workbook = openpyxl.load_workbook(io.BytesIO(response.content))
        # the floor sheets lead; no "구조도면 데이터 없음" page and no empty AI evidence sheets
        self.assertEqual(workbook.sheetnames, ["층별 집계(N층 기준)", "층별 철근(N층 기준)", "검토 사항"])
        texts = [c.value for row in workbook["층별 집계(N층 기준)"].iter_rows() for c in row if isinstance(c.value, str)]
        self.assertIn("[101동]", texts)

    def test_an_ai_run_keeps_every_sheet(self):
        takeoff = self.post("/api/quantity/floor-takeoff/", {"floor_job_id": "fv-1"}).json()["takeoff"]
        structural = {"items": [{"category": "콘크리트", "sub_category": "벽", "quantity": 1, "unit": "m3"}]}
        response = self.post("/api/quantity/download-excel/", {"results": {"structural": structural, "floor_takeoff": takeoff}})
        names = openpyxl.load_workbook(io.BytesIO(response.content)).sheetnames
        self.assertEqual(names[0], "구조 수량산출")
        self.assertIn("층별 집계(N층 기준)", names)
        self.assertIn("도면 출처", names)


class CadOnlyPageTests(SimpleTestCase):
    def test_the_start_card_offers_the_free_path_first(self):
        html = HOME.read_text()
        card = html[html.index("async function showAiStartCard() {"):][:3500]
        self.assertIn('id="qty-cad-go">AI 없이 CAD로만 진행 (무료)</button>', card)
        self.assertIn('id="qty-ai-go">AI 판독 시작 (유료)</button>', card)
        self.assertIn("runCadOnlyFlow();", card)

    def test_the_free_path_goes_3d_then_takeoff_then_rebar_then_excel(self):
        html = HOME.read_text()
        flow = html[html.index("async function runCadOnlyFlow() {"):][:3000]
        order = [flow.index(step) for step in ("job.status !== \"running\"", "await showMassStage();",
                                               "await showFloorTakeoff(data);", "await showFloorRebar(data);",
                                               "lastAnalysisResults = data.results;", "downloadExcel")]
        self.assertEqual(order, sorted(order))
        self.assertIn('state.cadOnly ? "CAD 도면을 다 읽었습니다.', html)

    def test_the_pdf_step_can_be_skipped_when_cad_is_there(self):
        html = HOME.read_text()
        card = html[html.index("async function showPdfUploadCard() {"):][:2500]
        self.assertIn('(uploadCount("cad") ? \'<button type="button" class="qd-btn" id="qty-pdf-skip">PDF 없이 진행 (CAD만)</button>\' : \'\')', card)
        self.assertIn('addUser("PDF 없이 진행");', card)
        self.assertIn('"어떻게 읽어 갈까요?"', html)


class CadOnlyNotesTests(SimpleTestCase):
    def test_what_the_cad_only_check_found_is_in_the_brain(self):
        from core import quantity_brain
        notes = {n["id"]: n for n in quantity_brain.review_notes("rebar")}
        self.assertEqual(notes["schedules-from-cad"]["status_label"], "적용")
        self.assertEqual(notes["bar-grade-in-general-notes"]["status_label"], "가정·확인 필요")
