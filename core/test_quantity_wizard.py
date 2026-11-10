"""The card-by-card quantity workflow: upload cards, drawing-check notes and their way to the AI."""
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings

from core import quantity_views as views

HOME = Path(__file__).with_name("templates").joinpath("core", "home.html")


class WizardTemplateTests(SimpleTestCase):
    def setUp(self):
        self.html = HOME.read_text()

    def test_deck_assets_load_before_the_workflow(self):
        workflow = self.html.index('var modal = document.getElementById("cblQuantityMessengerModal");')
        self.assertLess(self.html.index("core/js/quantity_deck.js"), workflow)
        self.assertLess(self.html.index("core/css/quantity_deck.css"), workflow)
        self.assertIn("window.cblQuantityDeck.create(flowEl", self.html)

    def test_upload_step_runs_cad_then_pdf_then_check_then_ai_question(self):
        order = [
            "프로젝트 확인이 끝났습니다.<br>다음, 도면 업로드로 넘어갈게요.",
            "건축·구조 캐드 파일을 올려주세요.",
            "이번엔 건축·구조 PDF를 올려주세요.",
            "/api/quantity/check-zip/",
            "다른 파일에 함께 있다면 적어주세요",
            "AI 판독을 시작할까요?",
        ]
        position = self.html.index("async function startUploadStep()")
        for text in order:
            position = self.html.index(text, position)  # each one after the previous one
        for chip in ("낱장 PDF", "합본 PDF", "폴더째"):
            self.assertIn(chip, self.html)

    def test_notes_travel_with_the_overview_check(self):
        self.assertIn('fd.append("drawing_notes", state.drawingNotes)', self.html)


class DrawingNotesTests(SimpleTestCase):
    def test_notes_join_the_user_context(self):
        self.assertIsNone(views._with_drawing_notes(None, None))
        self.assertEqual(views._with_drawing_notes("고쳐주세요", None), "고쳐주세요")
        only = views._with_drawing_notes(None, "- 구조 기본도면 '벽체일람표': S-311 파일 안")
        self.assertIn("[사용자 도면 메모", only)
        self.assertIn("S-311 파일 안", only)
        both = views._with_drawing_notes("지하 2개층", "메모")
        self.assertTrue(both.startswith("지하 2개층\n\n[사용자 도면 메모"))

    def test_notes_are_trimmed_and_capped(self):
        factory = RequestFactory()
        self.assertIsNone(views._drawing_notes_from_request(factory.post("/", {"drawing_notes": "   "})))
        long = views._drawing_notes_from_request(factory.post("/", {"drawing_notes": "가" * 5000}))
        self.assertEqual(len(long), views.QUANTITY_DRAWING_NOTES_MAX_CHARS)


class _NoThread:
    started = []

    def __init__(self, target=None, args=(), kwargs=None, daemon=None):
        self.args = args

    def start(self):
        _NoThread.started.append(self.args)


class OverviewCheckNotesTests(TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        override = override_settings(QUANTITY_STATE_DB=str(Path(tmp.name) / "state.sqlite3"))
        override.enable()
        self.addCleanup(override.disable)
        staff = get_user_model().objects.create_user("staff", password="staff-pass-1", is_staff=True)
        self.client.force_login(staff)
        _NoThread.started = []

    def post(self, notes=None, job="job-1"):
        data = {"job_id": job, "review_id": "rev-x",
                "structural_pdf": SimpleUploadedFile("s.pdf", b"%PDF-1.4 tiny", content_type="application/pdf")}
        if notes is not None:
            data["drawing_notes"] = notes
        with patch.object(views.threading, "Thread", _NoThread):
            return self.client.post("/api/quantity/overview-check/", data)

    def test_notes_reach_the_ai_context_and_a_change_reruns_a_cached_overview(self):
        response = self.post("- 일람표: S-111 파일 뒤쪽")
        self.assertEqual(response.status_code, 200)
        review_id = response.json()["review_id"]
        context = _NoThread.started[-1][4]
        self.assertIn("S-111 파일 뒤쪽", context)
        self.assertEqual(views._review_get(review_id)["drawing_notes"], "- 일람표: S-111 파일 뒤쪽")

        views._review_update(review_id, overview={"project_name": "x"})
        cached = self.post("- 일람표: S-111 파일 뒤쪽", job="job-2")
        self.assertTrue(cached.json().get("cache_hit"))
        self.assertEqual(len(_NoThread.started), 1)

        changed = self.post("- 일람표: S-311 파일", job="job-3")
        self.assertFalse(changed.json().get("cache_hit", False))
        self.assertIn("S-311 파일", _NoThread.started[-1][4])

    def test_without_notes_the_context_is_unchanged(self):
        self.post()
        self.assertIsNone(_NoThread.started[-1][4])
