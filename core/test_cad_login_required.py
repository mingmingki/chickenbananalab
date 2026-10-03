import os
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.apps import apps
from django.contrib.auth import get_user_model
from django.contrib.staticfiles.finders import get_finders
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, SimpleTestCase, TestCase
from django.urls import URLResolver, get_resolver

from . import views as core_views


def _cad_api_paths():
    """Every routed /api/cblcad/ URL, so newly added CAD endpoints are covered too."""
    paths = set()

    def walk(patterns, prefix=""):
        for pattern in patterns:
            route = prefix + str(pattern.pattern)
            if isinstance(pattern, URLResolver):
                walk(pattern.url_patterns, route)
            elif route.startswith("api/cblcad"):
                paths.add("/" + route.replace("<str:token>", "sample-token"))

    walk(get_resolver().url_patterns)
    return sorted(paths)


class CadLoginRequiredTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = get_user_model().objects.create_user(
            username="cad-member",
            password="test-password",
        )

    def test_cad_api_routes_are_discovered(self):
        paths = _cad_api_paths()
        self.assertIn("/api/cblcad/v29/open-session/", paths)
        self.assertIn("/api/cblcad/dwg-to-dxf/", paths)
        self.assertIn("/api/cblcad/dxf-to-dwg", paths)
        self.assertGreaterEqual(len(paths), 10)

    def test_anonymous_cad_api_requests_are_rejected_with_401_json(self):
        for path in _cad_api_paths():
            for method in ("get", "post"):
                with self.subTest(path=path, method=method):
                    response = getattr(self.client, method)(path)
                    self.assertEqual(response.status_code, 401)
                    self.assertEqual(response.json()["error"], "login_required")
                    self.assertFalse(response.json()["ok"])

    def test_anonymous_upload_never_reaches_the_converter(self):
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(core_views, "_cbl_v29_root", return_value=Path(tmp)), \
             patch.object(core_views, "_cbl_v29_oda_convert", side_effect=AssertionError("converter ran")):
            upload = SimpleUploadedFile("drawing.dwg", b"AC1018 fake", content_type="application/acad")
            response = self.client.post("/api/cblcad/v29/open-session/", {"file": upload})
            self.assertEqual(response.status_code, 401)
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_logged_in_member_reaches_cad_api(self):
        self.client.force_login(self.user)
        response = self.client.get("/api/cblcad/csrf/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"ok": True})

    def test_anonymous_cad_pages_redirect_to_login(self):
        for path in ("/cblcad/", "/tools/cad/"):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 302)
                self.assertTrue(response["Location"].startswith("/accounts/login/?next="))

    def test_logged_in_member_opens_cblcad_page(self):
        self.client.force_login(self.user)
        response = self.client.get("/cblcad/", HTTP_HOST="www.chickenbananalab.com", follow=True)
        self.assertEqual(response.redirect_chain, [("/tools/cad/?mode=free-dwg", 302)])
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"ChickenBananaCAD", response.content)


class CadStaticExposureTests(SimpleTestCase):
    def _collected_paths(self):
        # Mirrors collectstatic's collect(): the paths nginx would serve from STATIC_ROOT.
        ignore_patterns = apps.get_app_config("staticfiles").ignore_patterns
        collected = set()
        for finder in get_finders():
            for path, _storage in finder.list(ignore_patterns):
                collected.add(path.replace(os.sep, "/"))
        return collected

    def test_collectstatic_does_not_publish_the_cad_app_html(self):
        collected = self._collected_paths()
        self.assertNotIn("core/tools/CBLCAD_VER2.html", collected)
        self.assertEqual(
            [p for p in collected if p.rsplit("/", 1)[-1].startswith("CBLCAD_VER2.html")],
            [],
        )

    def test_collectstatic_still_publishes_cad_support_assets(self):
        collected = self._collected_paths()
        self.assertIn("core/tools/cbl_shx_parser.umd.js", collected)
        self.assertIn("core/cblcad_templates/blank.dwg", collected)
