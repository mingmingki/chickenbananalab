"""Kept uploads and finished AI steps let an interrupted main analysis resume without paying twice."""
import io
import json
import os
import stat
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.core.management import call_command
from django.test import RequestFactory, SimpleTestCase, override_settings

from core import quantity_job_inputs as kept_inputs
from core import quantity_views as views
from core.quantity_state import QuantityStateStore

DEAD_PID = 2147483647
SETTINGS = {"review_id": "rev", "confirmed_general_spec": {"lap_splice_class": "B"}, "progress_stages": ["structural"]}


class TempState(SimpleTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        override = override_settings(QUANTITY_STATE_DB=str(Path(self.tmp.name) / "state.sqlite3"))
        override.enable()
        self.addCleanup(override.disable)
        self.state = QuantityStateStore(Path(self.tmp.name) / "state.sqlite3")
        self.inputs = views._job_inputs()


class JobInputStoreTests(TempState):
    def test_uploads_round_trip_privately_and_only_for_the_owner(self):
        meta = self.inputs.save("job", 7, {"structural_pdf": b"pdf", "structural_zip": b"zip"}, SETTINGS)
        self.assertEqual(set(meta["files"]), {"structural_pdf", "structural_zip"})
        folder = self.inputs.folder("job")
        self.assertEqual(stat.S_IMODE(folder.stat().st_mode), 0o700)
        self.assertTrue(all(stat.S_IMODE(f.stat().st_mode) == 0o600 for f in folder.iterdir()))
        self.assertNotIn("job", folder.name)  # the folder name does not reveal the job id
        inputs, job_settings, _meta = self.inputs.load("job", "7")
        self.assertEqual((inputs["structural_pdf"], inputs["architectural_zip"]), (b"pdf", None))
        self.assertEqual(job_settings, SETTINGS)
        self.assertIsNone(self.inputs.load("job", 8))
        self.assertIsNone(self.inputs.resume_info("job", 8))

    def test_damaged_or_expired_uploads_are_not_used(self):
        self.inputs.save("job", 7, {"structural_pdf": b"pdf"}, SETTINGS)
        (self.inputs.folder("job") / "structural_pdf.bin").write_bytes(b"pdx")
        self.assertIsNone(self.inputs.load("job", 7))
        self.inputs.save("old", 7, {"structural_pdf": b"pdf"}, SETTINGS)
        later = time.time() + kept_inputs.RETENTION_SECONDS + 1
        with patch.object(kept_inputs.time, "time", return_value=later):
            self.assertIsNone(self.inputs.resume_info("old", 7))
            self.inputs.purge()
        self.assertFalse(self.inputs.folder("old").exists())

    def test_nothing_is_kept_when_space_is_short(self):
        with patch.object(kept_inputs, "MAX_TOTAL_BYTES", 2):
            self.assertIsNone(self.inputs.save("job", 7, {"structural_pdf": b"pdf"}, SETTINGS))
        self.assertFalse(self.inputs.folder("job").exists())

    def test_a_step_is_reused_only_for_the_same_key(self):
        self.inputs.save("job", 7, {"structural_pdf": b"pdf"}, SETTINGS)
        steps = self.inputs.steps("job", {"model": "m"})
        self.assertTrue(steps.save("structural-0001-0008", {"walls": [1]}, "cad", 1))
        again = self.inputs.steps("job", {"model": "m"})
        self.assertIsNone(again.load("structural-0001-0008", "cad", 2))
        self.assertEqual(again.load("structural-0001-0008", "cad", 1), {"walls": [1]})
        self.assertEqual(again.reused, ["structural-0001-0008"])
        self.assertIsNone(self.inputs.steps("job", {"model": "other"}).load("structural-0001-0008", "cad", 1))
        self.assertEqual(self.inputs.resume_info("job", 7)["structural_batches"], 1)
        self.assertFalse(steps.save("bad", {"x": float("nan")}))


class StructuralBatchReuseTests(TempState):
    def run_extraction(self, steps, failing=()):
        calls = []

        def one_batch(client, dwg, images, idx, total, page_numbers=None, confirmed_general_spec=None):
            calls.append(idx)
            if idx in failing:
                return dict(views._EMPTY_MEMBERS, notes=[f"[배치 {idx}/{total}] Gemini 부재 추출 요청 중 오류가 발생했습니다: 503"])
            return dict(views._EMPTY_MEMBERS, walls=[{"mark": f"W{idx}", "count": 1}], notes=[])

        with patch.object(views, "get_gemini_client", return_value=object()), \
                patch.object(views, "pdfinfo_from_bytes", return_value={"Pages": 20}), \
                patch.object(views, "_detect_schedule_pages", return_value=[]), \
                patch.object(views, "_render_pdf_page_range", side_effect=lambda pdf, a, b, **k: [f"p{p}" for p in range(a, b + 1)]), \
                patch.object(views, "_extract_structural_members_one_batch", side_effect=one_batch):
            result = views.extract_structural_members({}, b"pdf", steps=steps)
        return calls, result

    def test_only_unfinished_batches_call_the_ai_again(self):
        meta = self.inputs.save("job", 7, {"structural_pdf": b"pdf"}, SETTINGS)
        base = views._job_step_base(meta)
        calls, first = self.run_extraction(self.inputs.steps("job", base), failing={2})
        self.assertEqual(calls, [1, 2, 3])
        self.assertTrue(views._extraction_incomplete_reasons(first))
        calls, second = self.run_extraction(self.inputs.steps("job", base))
        self.assertEqual(calls, [2])  # batch 2 failed before: asked again; 1 and 3 reused
        self.assertEqual(sorted(w["mark"] for w in second["walls"]), ["W1", "W2", "W3"])
        self.assertFalse(views._extraction_incomplete_reasons(second))

    def test_a_changed_confirmed_spec_or_prompt_reruns_every_batch(self):
        meta = self.inputs.save("job", 7, {"structural_pdf": b"pdf"}, SETTINGS)
        self.run_extraction(self.inputs.steps("job", views._job_step_base(meta)))
        changed = dict(meta, settings=dict(SETTINGS, confirmed_general_spec={"lap_splice_class": "A"}))
        self.assertEqual(self.run_extraction(self.inputs.steps("job", views._job_step_base(changed)))[0], [1, 2, 3])
        with patch.object(views, "MEMBER_EXTRACTION_SYSTEM_PROMPT", "changed prompt"):
            self.assertEqual(self.run_extraction(self.inputs.steps("job", views._job_step_base(meta)))[0], [1, 2, 3])


class JobRunReuseTests(TempState):
    def run_job(self, steps, analyze, elevation, render):
        members = dict(views._EMPTY_MEMBERS, walls=[], notes=[])
        with patch.object(views, "parse_dwg_from_zip", return_value={}), \
                patch.object(views, "pdf_to_images", render), \
                patch.object(views, "analyze_with_gemini", analyze), \
                patch.object(views, "extract_elevation_section_data", elevation), \
                patch.object(views, "extract_structural_members", return_value=members), \
                patch.object(views, "_build_source_manifest", return_value={}), \
                patch.object(views, "attach_member_sources", side_effect=lambda members, manifest: members), \
                patch.object(views, "build_annotated_pages", return_value=[]):
            views._run_quantity_job("job", None, b"spdf", None, b"apdf", ["architectural", "elevation", "structural"],
                                    3, None, steps=steps)
        return views._result_get("job")

    def test_saved_architectural_steps_skip_the_ai_and_the_pdf_rendering(self):
        meta = self.inputs.save("job", 7, {"structural_pdf": b"spdf", "architectural_pdf": b"apdf"}, SETTINGS)
        base = views._job_step_base(meta)
        analyze = Mock(return_value={"items": [{"name": "창호"}], "summary": "ok", "warnings": [], "missing_info": []})
        elevation = Mock(return_value=dict(views._EMPTY_ELEVATION_SECTION, notes=[]))
        render = Mock(return_value=["image"])
        first = self.run_job(self.inputs.steps("job", base), analyze, elevation, render)
        self.assertEqual((analyze.call_count, elevation.call_count, render.call_count), (1, 1, 1))
        second = self.run_job(self.inputs.steps("job", base), analyze, elevation, render)
        self.assertEqual((analyze.call_count, elevation.call_count, render.call_count), (1, 1, 1))
        self.assertEqual(second["results"]["architectural"], first["results"]["architectural"])
        self.assertNotIn("error", second["results"]["structural"], second["results"]["structural"])
        notes = views._extraction_store_get("job")["members"]["notes"]
        self.assertTrue(any("이어서 실행" in note and "건축도면 분석" in note for note in notes))
        self.assertFalse(views._extraction_incomplete_reasons({"notes": notes}))

    def test_a_failed_ai_answer_is_not_saved(self):
        meta = self.inputs.save("job", 7, {"architectural_pdf": b"apdf"}, SETTINGS)
        base = views._job_step_base(meta)
        analyze = Mock(return_value={"items": [], "summary": "Gemini 분석 요청 중 오류가 발생했습니다: 503", "warnings": [], "missing_info": []})
        elevation = Mock(return_value=dict(views._EMPTY_ELEVATION_SECTION, notes=[]))
        self.run_job(self.inputs.steps("job", base), analyze, elevation, Mock(return_value=["image"]))
        self.run_job(self.inputs.steps("job", base), analyze, elevation, Mock(return_value=["image"]))
        self.assertEqual((analyze.call_count, elevation.call_count), (2, 1))


class ThreadLifecycleTests(TempState):
    def test_kept_files_are_removed_once_a_result_is_stored(self):
        seen = {}

        def run(job_id, *args, steps=None):
            seen["steps"] = steps
            seen["kept"] = self.inputs.folder(job_id).exists()
            views._result_set(job_id, {"ok": True, "results": {}})

        with patch.object(views, "_run_quantity_job", side_effect=run):
            views._quantity_job_thread("job", 7, {"structural_pdf": b"pdf"}, SETTINGS)
        self.assertTrue(seen["kept"])
        self.assertIsNotNone(seen["steps"])
        self.assertFalse(self.inputs.folder("job").exists())

    def test_a_job_that_stops_without_a_result_keeps_its_files(self):
        with patch.object(views, "_run_quantity_job"):
            views._quantity_job_thread("job", 7, {"structural_pdf": b"pdf"}, SETTINGS)
        self.assertIsNotNone(self.inputs.resume_info("job", 7))

    def test_the_job_still_runs_when_nothing_can_be_kept(self):
        with patch.object(kept_inputs, "MAX_TOTAL_BYTES", 0), patch.object(views, "_run_quantity_job") as run:
            views._quantity_job_thread("job", 7, {"structural_pdf": b"pdf"}, SETTINGS)
        self.assertIsNone(run.call_args.kwargs["steps"])
        self.assertEqual(run.call_args.args[5], ["structural"])


class ResumeApiTests(TempState):
    def setUp(self):
        super().setUp()
        self.factory = RequestFactory()
        self.user = SimpleNamespace(pk=7, is_staff=True, is_superuser=False, is_authenticated=True)
        self.state.begin_job("job", "7", "api_run_quantity", "input")
        self.state.update("job", "job", {"pid": DEAD_PID})
        self.inputs.save("job", 7, {"structural_pdf": b"pdf"}, SETTINGS)

    def call(self, view, method="post", user=None, job="job"):
        request = getattr(self.factory, method)("/", {"job_id": job})
        request.user = user or self.user
        response = view(request)
        return response.status_code, json.loads(response.content)

    def test_interrupted_job_offers_a_resume(self):
        code, payload = self.call(views.api_quantity_progress, "get")
        self.assertTrue(payload["interrupted"] and payload["resumable"])
        self.assertIn("이어서 실행", payload["error"])
        code, payload = self.call(views.api_quantity_jobs, "get")
        self.assertEqual((payload["jobs"][0]["status"], payload["jobs"][0]["resumable"]), ("interrupted", True))

    def test_resume_starts_one_worker_from_the_kept_uploads(self):
        with patch.object(views.threading, "Thread") as thread:
            code, payload = self.call(views.api_quantity_resume)
            self.assertEqual((code, payload["accepted"]), (200, True))
            args = thread.call_args.kwargs["args"]
            self.assertEqual((args[0], args[2]["structural_pdf"], args[3]), ("job", b"pdf", SETTINGS))
            self.assertEqual(self.state.get("job", "job")["pid"], os.getpid())
            self.assertEqual(views._progress_get("job")["label"], "이어서 실행 대기 중")
            code, _payload = self.call(views.api_quantity_resume)
            self.assertEqual(code, 409)  # its worker is alive now
            self.assertEqual(thread.return_value.start.call_count, 1)

    def test_resume_is_refused_for_others_finished_or_lost_jobs(self):
        other = SimpleNamespace(pk=8, is_staff=True, is_superuser=False, is_authenticated=True)
        member = SimpleNamespace(pk=7, is_staff=False, is_superuser=False, is_authenticated=True)
        with patch.object(views.threading, "Thread") as thread:
            self.assertEqual(self.call(views.api_quantity_resume, user=other)[0], 403)
            self.assertEqual(self.call(views.api_quantity_resume, user=member)[0], 403)
            self.state.begin_job("overview", "7", "api_quantity_overview_check", "input")
            self.assertEqual(self.call(views.api_quantity_resume, job="overview")[0], 400)
            self.inputs.discard("job")
            code, payload = self.call(views.api_quantity_resume)
            self.assertEqual((code, payload["resumable"]), (410, False))
            views._result_set("job", {"ok": True, "results": {}})
            self.assertEqual(self.call(views.api_quantity_resume)[0], 409)
        thread.assert_not_called()
        self.assertEqual(self.state.get("job", "job")["pid"], DEAD_PID)


class RunningJobsCommandTests(TempState):
    def test_lists_running_analyses_and_fails_so_a_restart_waits(self):
        self.state.begin_job("dead", "7", "api_run_quantity", "input")
        self.state.update("job", "dead", {"pid": DEAD_PID})
        out = io.StringIO()
        call_command("quantity_running_jobs", stdout=out)
        self.assertIn("0 running", out.getvalue())
        self.state.begin_job("live", "7", "api_run_quantity", "input")  # this process is alive
        views._progress_set("live", "structural", 3, 9, "구조 부재 추출 배치 3/9 처리 중")
        out = io.StringIO()
        with self.assertRaises(SystemExit) as stopped:
            call_command("quantity_running_jobs", stdout=out)
        self.assertEqual(stopped.exception.code, 1)
        self.assertIn("배치 3/9", out.getvalue())
