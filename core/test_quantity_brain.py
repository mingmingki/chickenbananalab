"""The quantity tool learns from people's answers in one store for every drawing (no office profiles)."""
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings

from core import quantity_views as views
from core import quantity_brain as brain_module
from core.quantity_brain import Brain


class BrainTests(SimpleTestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.brain = Brain(os.path.join(tmp.name, "brain.sqlite3"), seed=None)  # the shipped facts left out

    def test_one_store_for_every_drawing_names_need_two_jobs_to_agree(self):
        self.brain.learn({"A-CONC": "wall", "%%BEAM": "beam", "A-PLANAR LINE": None}, owner=1, job="fv-1", kind="correct")
        once = brain_module.trusted_names(self.brain.knowledge(), {"A-CONC", "%%BEAM", "A-PLANAR LINE"})
        self.assertEqual(once, {})  # one job is not enough for other drawings
        self.brain.learn({"A-CONC": "wall", "A-PLANAR LINE": None}, owner=2, job="fv-2")
        known = self.brain.knowledge()
        self.assertEqual(brain_module.trusted_names(known, {"A-CONC", "A-PLANAR LINE", "%%BEAM"}),
                         {"A-CONC": "wall", "A-PLANAR LINE": None})
        # a job's latest answer per name counts once; a split vote is not trusted
        self.brain.learn({"A-CONC": "beam"}, owner=3, job="fv-3")
        self.assertEqual(brain_module.trusted_names(self.brain.knowledge(), {"A-CONC"}), {})

    def test_words_of_names_are_hints_from_every_job(self):
        for job, name in (("j1", "0_STR_CONC"), ("j2", "S-CONC-WALL"), ("j3", "CONC")):
            self.brain.learn({name: "wall"}, job=job)
        self.assertEqual(brain_module.word_hint(self.brain.knowledge(), "NEW_CONC_X"), "wall")
        self.assertIsNone(brain_module.word_hint(self.brain.knowledge(), "SOMETHING"))

    def test_a_jobs_own_answers_apply_to_it_and_values_give_a_median(self):
        self.brain.learn({"X1": "beam"}, job="fv-1")
        self.brain.learn({"X1": "wall_above"}, job="fv-1")
        self.assertEqual(self.brain.corrections("fv-1"), {"X1": "wall_above"})  # the latest answer
        self.assertEqual(self.brain.knowledge(exclude_job="fv-1")["names"], {})
        for job, value in (("a", 1.0), ("b", 1.2), ("c", 1.5)):
            self.brain.learn_value("foundation:tower", value, job=job)
        self.assertEqual(self.brain.knowledge()["values"]["foundation:tower"], 1.2)


class LearnedRolesInReadingTests(SimpleTestCase):
    def items(self):
        from core.test_quantity_layer_roles import LayerRoleTests
        return LayerRoleTests().items()

    def test_a_jobs_own_answer_wins(self):
        from core import quantity_layer_roles as lr
        roles = lr.judge(self.items(), known={"X1": "wall_above"})
        self.assertEqual((roles["X1"]["role"], roles["X1"]["by"]), ("wall_above", "person"))

    def test_names_learned_elsewhere_are_used_only_where_the_geometry_allows(self):
        from core import quantity_layer_roles as lr
        roles = lr.judge(self.items(), learned={"X1": "wall_above", "CEN": "wall"})
        self.assertEqual((roles["X1"]["role"], roles["X1"]["by"]), ("wall_above", "learned"))
        self.assertIsNone(roles["CEN"]["role"])  # a single centre line has no wall faces

    def test_word_hints_name_a_layer_the_built_in_rules_do_not_know(self):
        from core import quantity_layer_roles as lr
        items = [("QQ", "dashed", pts) for _l, lt, pts in self.items() if _l == "X1"]
        self.assertEqual(lr.judge(items, hints={"QQ": "beam"})["QQ"]["role"], "beam")


class _NoThread:
    started = []

    def __init__(self, target=None, args=(), kwargs=None, daemon=None):
        self.target, self.args = target, args

    def start(self):
        _NoThread.started.append((self.target.__name__, self.args))


class DrawingProfileApiTests(TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        override = override_settings(QUANTITY_STATE_DB=str(self.dir / "state.sqlite3"))
        override.enable()
        self.addCleanup(override.disable)
        users = get_user_model().objects
        self.staff = users.create_user("staff", password="staff-pass-1", is_staff=True)
        self.other = users.create_user("other", password="other-pass-1", is_staff=True)
        self.client.force_login(self.staff)
        views._quantity_state().begin_job("fv-1", self.staff.pk, "api_quantity_floor_views", "x")
        views._quantity_state().begin_job("fv-2", self.other.pk, "api_quantity_floor_views", "y")
        _NoThread.started = []

    def post(self, body):
        with patch.object(views.threading, "Thread", _NoThread):
            return self.client.post("/api/quantity/drawing-profile/", json.dumps(body), content_type="application/json")

    def test_answers_are_learned_by_layer_name_without_the_xref_prefix(self):
        response = self.post({"floor_job_id": "fv-1", "corrected": True,
                              "layers": {"XR-구조101동 (현장)$0$A-CONC": "wall", "XR-구조101동 (현장)$0$A-PLANAR LINE": None}})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertFalse(body["rerun"])
        self.assertEqual(body["learned"], 2)
        self.assertEqual(views._brain().corrections("fv-1"), {"A-CONC": "wall", "A-PLANAR LINE": None})
        self.assertEqual(views._brain().events()[0]["kind"], "correct")

    def test_only_the_owner_may_teach(self):
        self.assertEqual(self.post({"floor_job_id": "fv-2", "layers": {"A": "wall"}}).status_code, 404)
        self.assertEqual(self.post({"floor_job_id": "fv-1", "layers": {}}).status_code, 400)

    def test_a_rerun_needs_the_kept_drawings(self):
        missing = self.post({"floor_job_id": "fv-1", "layers": {"A-CONC": "wall"}, "rerun": True}).json()
        self.assertFalse(missing["rerun"])
        self.assertIn("업로드를 다시", missing["error"])
        cache = Path(views._floor_cache_dir("fv-1"))
        cache.mkdir(parents=True)
        (cache / "manifest.json").write_text("{}")
        views._result_set("fv-1", {"ok": True, "results": {}})
        started = self.post({"floor_job_id": "fv-1", "layers": {"A-CONC": "wall"}, "rerun": True}).json()
        self.assertTrue(started["rerun"])
        self.assertEqual(_NoThread.started[-1], ("_rerun_floor_views_job", ("fv-1",)))
        self.assertIsNone(views._result_get("fv-1"))  # polling shows it running again

    def test_kept_drawings_are_read_again_with_what_was_learned(self):
        work = self.dir / "work"
        work.mkdir()
        dxf = work / "000.dwg.dxf"
        dxf.write_text("0\nEOF\n")
        views._keep_floor_inputs("fv-1", [{"name": "S-1 평면도.dwg", "dxf": str(dxf), "raw": False}], ["S-1 평면도.dwg"], [])
        views._brain().learn({"A-CONC": "wall", "B": "beam", "C": "column"}, owner=self.staff.pk, job="fv-1")
        seen = {}

        def fake_render(job_id, folder, manifest):
            seen["manifest"] = manifest
            seen["text"] = Path(manifest["files"][0]["dxf"]).read_text()

        with patch.object(views, "_render_floor_views", fake_render):
            views._rerun_floor_views_job("fv-1")
        self.assertEqual(seen["manifest"]["plans"], ["S-1 평면도.dwg"])
        self.assertEqual(seen["text"], "0\nEOF\n")

    def test_old_kept_drawings_are_removed(self):
        cache = Path(views._floor_cache_dir("fv-old"))
        cache.mkdir(parents=True)
        old = 1_000_000_000
        os.utime(cache, (old, old))
        views._prune_floor_cache()
        self.assertFalse(cache.exists())


class PartialFloorResultTests(TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        override = override_settings(QUANTITY_STATE_DB=str(self.dir / "state.sqlite3"))
        override.enable()
        self.addCleanup(override.disable)

    def test_a_timed_out_reading_keeps_what_was_read(self):
        import subprocess

        def slow(command, **kwargs):
            out = command[-1]
            with open(out + ".partial", "w", encoding="utf-8") as handle:
                json.dump({"views": [{"floor": "1층"}], "partial": True}, handle)
            raise subprocess.TimeoutExpired(command, 900)

        with patch.object(views.subprocess, "run", slow), patch.object(views, "_wait_for_free_memory", lambda: None):
            views._render_floor_views("fv-9", str(self.dir), {"files": [], "plans": [], "levels": []})
        result = views._result_get("fv-9")
        self.assertTrue(result["ok"])
        self.assertTrue(result["results"]["floor_views"]["partial"])

    def test_structural_files_are_read_first(self):
        from core import quantity_floor_views as fv
        names = ["A2-101 101동평면도.dwg", "S1-501 101동구조평면도.dwg", "S-111 동 구조평면도.dwg"]
        self.assertEqual(sorted(names, key=lambda n: 0 if fv._is_structural_file(n) else 1)[:2],
                         ["S1-501 101동구조평면도.dwg", "S-111 동 구조평면도.dwg"])


class SeedTests(SimpleTestCase):
    def test_facts_checked_by_hand_ship_with_the_code_one_job_per_project(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        seed = os.path.join(tmp.name, "seed.json")
        with open(seed, "w", encoding="utf-8") as handle:
            json.dump({"jobs": {"reference:a": {"layers": {"0_STR_GIRDER": "beam", "X-NOTE": None}, "values": {"foundation:tower": 1.0}},
                                "reference:b": {"layers": {"X-NOTE": None}}}}, handle)
        brain = Brain(os.path.join(tmp.name, "brain.sqlite3"), seed=seed)
        known = brain.knowledge()
        self.assertEqual(brain_module.trusted_names(known, {"X-NOTE", "0_STR_GIRDER"}), {"X-NOTE": None})  # two projects agree
        self.assertEqual(known["values"]["foundation:tower"], 1.0)
        brain.learn({"0_STR_GIRDER": "beam"}, job="fv-9")  # a person agrees on a real job: now trusted too
        self.assertEqual(brain_module.trusted_names(brain.knowledge(), {"0_STR_GIRDER"}), {"0_STR_GIRDER": "beam"})

    def test_the_shipped_seed_reads(self):
        events = brain_module.seed_events()
        self.assertGreaterEqual(len({job for job, _k, _p in events}), 4)
        self.assertTrue(all(job.startswith("reference:") for job, _k, _p in events))
