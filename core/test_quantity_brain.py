"""The quantity tool learns how offices draw: profiles from people's answers, used on the next drawing."""
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings

from core import quantity_views as views
from core.quantity_brain import Brain, match


class BrainTests(SimpleTestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.brain = Brain(os.path.join(tmp.name, "brain.sqlite3"))

    def test_answers_build_a_profile_that_the_next_drawing_finds(self):
        first = self.brain.learn({"A-CONC": "wall", "A-하부벽체": "wall_below", "%%BEAM": "beam", "A-PLANAR LINE": None},
                                 owner=1, job="fv-1", kind="correct")
        again = self.brain.learn({"A-CONC": "wall", "A-콘크리트": "wall", "%%BEAM": "beam", "A-하부벽체": "wall_below"},
                                 owner=1, job="fv-2")
        self.assertEqual(first, again)  # the same office: merged, not a second profile
        profile = self.brain.profile(first)
        self.assertEqual(profile["uses"], 2)
        self.assertIsNone(profile["layers"]["A-PLANAR LINE"])
        self.assertEqual(profile["layers"]["A-콘크리트"], "wall")
        found = match(self.brain.profiles(), {"A-CONC", "A-하부벽체", "%%BEAM", "XC", "0"})
        self.assertEqual(found["id"], first)
        self.assertIsNone(match(self.brain.profiles(), {"0_STR_CONC", "0_STR_BEAM", "CEN"}))
        self.assertEqual([e["kind"] for e in self.brain.events(first)], ["confirm", "correct"])

    def test_unknown_roles_are_kept_as_not_a_member(self):
        pid = self.brain.learn({"X": "roof-ish", "Y": "wall", "Z": "beam", "W": "column"})
        self.assertIsNone(self.brain.profile(pid)["layers"]["X"])


class ProfileInFloorJobTests(SimpleTestCase):
    def test_a_matching_profile_overrides_the_geometry(self):
        from core import quantity_layer_roles as lr
        from core.test_quantity_layer_roles import LayerRoleTests
        items = LayerRoleTests().items()
        profiles = [{"id": "p1", "label": "", "layers": {"X1": "wall_above", "A-콘크리트": "wall", "6A": "wall_below"}}]
        known = match(profiles, {layer for layer, _lt, _pts in items})["layers"]
        roles = lr.judge(items, known=known)
        self.assertEqual(roles["X1"]["role"], "wall_above")
        self.assertEqual(roles["X1"]["by"], "profile")


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
        profile = views._brain().profile(body["profile_id"])
        self.assertEqual(profile["layers"], {"A-CONC": "wall", "A-PLANAR LINE": None})
        self.assertEqual(views._brain().events(body["profile_id"])[0]["kind"], "correct")

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

    def test_kept_drawings_are_read_again_with_the_profiles(self):
        work = self.dir / "work"
        work.mkdir()
        dxf = work / "000.dwg.dxf"
        dxf.write_text("0\nEOF\n")
        views._keep_floor_inputs("fv-1", [{"name": "S-1 평면도.dwg", "dxf": str(dxf), "raw": False}], ["S-1 평면도.dwg"], [])
        views._brain().learn({"A-CONC": "wall", "B": "beam", "C": "column"}, owner=self.staff.pk)
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
