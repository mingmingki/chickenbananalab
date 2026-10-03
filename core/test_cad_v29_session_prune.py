import os
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, SimpleTestCase, TestCase

from . import views as core_views


class CadV29SessionPruneTests(SimpleTestCase):
    DAY = 24 * 60 * 60

    def _age(self, path, seconds, now):
        os.utime(path, (now - seconds, now - seconds))

    def test_prunes_only_expired_session_directories(self):
        now = time.time()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            expired = root / ("a" * 32)
            expired.mkdir()
            (expired / "base.dxf").write_text("0\nEOF\n")
            self._age(expired / "base.dxf", 8 * self.DAY, now)
            self._age(expired, 8 * self.DAY, now)

            recent = root / ("b" * 32)
            recent.mkdir()

            foreign = root / "keep-me"
            foreign.mkdir()
            self._age(foreign, 30 * self.DAY, now)

            stray_file = root / ("c" * 32)
            stray_file.write_text("not a session dir")
            self._age(stray_file, 30 * self.DAY, now)

            removed = core_views._cbl_v29_prune_expired_sessions(root, now=now)

            self.assertEqual(removed, [expired.name])
            self.assertFalse(expired.exists())
            self.assertTrue(recent.exists())
            self.assertTrue(foreign.exists())
            self.assertTrue(stray_file.exists())

    def test_session_with_recent_save_output_is_kept(self):
        now = time.time()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            session = root / ("d" * 32)
            session.mkdir()
            (session / "base.dxf").write_text("0\nEOF\n")
            self._age(session / "base.dxf", 8 * self.DAY, now)
            (session / "edited_20260929.dxf").write_text("0\nEOF\n")
            self._age(session, 8 * self.DAY, now)

            self.assertEqual(core_views._cbl_v29_prune_expired_sessions(root, now=now), [])
            self.assertTrue(session.exists())

    def test_missing_root_is_a_no_op(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(core_views._cbl_v29_prune_expired_sessions(Path(tmp) / "absent"), [])

    def test_prune_is_throttled(self):
        now = time.time()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.object(core_views, "_cbl_v29_prune_expired_sessions", return_value=[]) as prune:
                core_views._cbl_v29_maybe_prune_sessions(root, now=now)
                core_views._cbl_v29_maybe_prune_sessions(root, now=now + 60)
                self.assertEqual(prune.call_count, 1)
                core_views._cbl_v29_maybe_prune_sessions(root, now=now + 2 * 60 * 60)
                self.assertEqual(prune.call_count, 2)


class CadV29OpenSessionPruneHookTests(TestCase):
    def test_open_session_prunes_old_sessions(self):
        user = get_user_model().objects.create_user(username="cad-pruner", password="test-password")
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(core_views, "_cbl_v29_root", return_value=Path(tmp)), \
             patch.object(core_views, "_cbl_v29_maybe_prune_sessions") as maybe_prune, \
             patch.object(core_views, "_cbl_v29_oda_convert", side_effect=RuntimeError("no converter in tests")):
            # The route answers 410 since ODA left ChickenBananaCAD; the view is
            # still checked in case it is routed again.
            upload = SimpleUploadedFile("drawing.dwg", b"AC1018 fake", content_type="application/acad")
            request = RequestFactory().post("/api/cblcad/v29/open-session/", {"file": upload})
            request.user = user
            core_views.cblcad_v29_open_session(request)
            maybe_prune.assert_called_once_with(Path(tmp))
