import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, RequestFactory, TestCase

from . import views as core_views


class CadV29SaveOpsSessionIdTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = get_user_model().objects.create_user(username="cad-saver", password="test-password")
        self.client.force_login(self.user)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "sessions"
        self.root.mkdir()
        # A drawing that lives outside the session root must never be reachable.
        self.outside = Path(self.tmp.name) / "outside"
        self.outside.mkdir()
        (self.outside / "base.dxf").write_text("0\nSECTION\n2\nENTITIES\n0\nENDSEC\n0\nEOF\n")

    def _post(self, session_id):
        # The route answers 410 since ODA left ChickenBananaCAD (test_cad_no_oda);
        # the view itself is still checked in case it is routed again.
        request = RequestFactory().post(
            "/api/cblcad/v29/save-ops/",
            data=json.dumps({"session_id": session_id, "ops": []}),
            content_type="application/json",
        )
        request.user = self.user
        with patch.object(core_views, "_cbl_v29_root", return_value=self.root), \
             patch.object(core_views, "_cbl_v29_oda_convert", side_effect=AssertionError("converter ran")):
            return core_views.cblcad_v29_save_ops(request)

    def test_path_traversal_session_ids_are_rejected(self):
        for session_id in ("../outside", str(self.outside), "..", "a" * 31, "A" * 32, "g" * 32):
            with self.subTest(session_id=session_id):
                response = self._post(session_id)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(json.loads(response.content)["error"], "invalid session_id")
        self.assertEqual(sorted(p.name for p in self.outside.iterdir()), ["base.dxf"])

    def test_unknown_well_formed_session_id_is_not_found(self):
        response = self._post("0" * 32)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(json.loads(response.content)["error"], "base.dxf not found")
