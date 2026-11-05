from pathlib import Path
from unittest.mock import Mock, patch

from django.conf import settings
from django.test import TestCase

from core import job_views


class JobInterviewStatusTests(TestCase):
    def _get(self, path, available):
        with patch("core.job_views.interview_analysis_available", return_value=available):
            return self.client.get(path)

    def test_page_says_preparing_when_analysis_is_unavailable(self):
        response = self._get("/job/", False)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "AI 면접은 지금 준비 중")
        self.assertNotContains(response, 'id="role-chips"')

    def test_page_offers_the_interview_when_analysis_is_available(self):
        response = self._get("/job/", True)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "AI 면접은 지금 준비 중")
        self.assertContains(response, 'id="role-chips"')

    def test_home_entry_follows_availability(self):
        self.assertContains(self._get("/", False), "var JOB_INTERVIEW_READY = false;")
        self.assertContains(self._get("/", True), "var JOB_INTERVIEW_READY = true;")

    def test_availability_needs_both_the_key_and_the_package(self):
        with patch("core.job_views._openai_api_key", return_value=""), patch("core.job_views.OpenAI", Mock()):
            self.assertFalse(job_views.interview_analysis_available())
        with patch("core.job_views._openai_api_key", return_value="value"), patch("core.job_views.OpenAI", None):
            self.assertFalse(job_views.interview_analysis_available())
        with patch("core.job_views._openai_api_key", return_value="value"), patch("core.job_views.OpenAI", Mock()):
            self.assertTrue(job_views.interview_analysis_available())

    def test_failed_analysis_returns_no_result_and_leaves_no_report(self):
        with patch("core.job_views.get_openai_client", return_value=None):
            response = self.client.post(
                "/api/job/analyze/",
                data={"profile": {}, "answers": [{"q_index": 0, "question": "질문", "answer": "답변"}]},
                content_type="application/json",
            )
        self.assertEqual(response.status_code, 503)
        payload = response.json()
        self.assertFalse(payload["ok"])
        self.assertNotIn("result", payload)
        self.assertEqual(self.client.get("/api/job/download-report/").status_code, 404)


class JobInterviewReportSourceTests(TestCase):
    def test_report_script_keeps_no_made_up_values(self):
        source = (Path(settings.BASE_DIR) / "core/templates/core/job_main.html").read_text(encoding="utf-8")
        for fallback in ("data.result || {}", "grade: 'B'", "r.grade || 'B'", "scores[key] || 3"):
            with self.subTest(fallback=fallback):
                self.assertNotIn(fallback, source)
