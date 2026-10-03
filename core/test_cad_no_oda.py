import subprocess
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, SimpleTestCase, TestCase
from django.urls import URLResolver, get_resolver

from . import quantity_views
from . import views as core_views

# ODA File Converter must not be part of any ChickenBananaCAD feature (license).
# These routes ran it; they now answer 410 without starting anything.
ODA_ROUTES = [
    "/api/cblcad/v29/open-session/",
    "/api/cblcad/v29/save-ops/",
    "/api/cblcad/dwg-to-dxf/",
    "/api/cblcad/dwg-to-dxf",
    "/api/cblcad/dwg-to-best-dxf/",
    "/api/cblcad/dxf-to-dwg/",
    "/api/cblcad/dxf-to-dwg",
]
HOST = "www.chickenbananalab.com"
# Every CAD API view that may stay routed: the free (ACadSharp) DWG pipeline.
ODA_FREE_VIEWS = {
    "cblcad_free_dwg_local_api",
    "cblcad_free_dwg_native_open_api",
    "cblcad_free_dwg_native_save_path_api",
    "cblcad_free_dwg_save_local_api",
    "cblcad_free_dwg_download_api",
    "cblcad_csrf",
    "cblcad_oda_removed_api",
}


def _cad_api_views():
    views = {}

    def walk(patterns, prefix=""):
        for pattern in patterns:
            route = prefix + str(pattern.pattern)
            if isinstance(pattern, URLResolver):
                walk(pattern.url_patterns, route)
            elif route.startswith("api/cblcad"):
                views.setdefault(getattr(pattern.callback, "__name__", repr(pattern.callback)), set()).add("/" + route)

    walk(get_resolver().url_patterns)
    return views


class CadOdaRoutesRemovedTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.client.force_login(get_user_model().objects.create_user(username="cad-no-oda", password="test-password"))

    def test_oda_routes_answer_410_without_starting_a_converter(self):
        with patch.object(core_views, "_cbl_v29_oda_convert", side_effect=AssertionError("ODA ran")), \
             patch.object(subprocess, "Popen", side_effect=AssertionError("a process was started")):
            for path in ODA_ROUTES:
                for method in ("get", "post"):
                    with self.subTest(path=path, method=method):
                        data = {"file": SimpleUploadedFile("drawing.dwg", b"AC1018 fake", content_type="application/acad")}
                        response = self.client.get(path) if method == "get" else self.client.post(path, data)
                        self.assertEqual(response.status_code, 410)
                        body = response.json()
                        self.assertEqual((body["ok"], body["error"]), (False, "oda_removed"))
                        self.assertIn("무료 DWG", body["message"])

    def test_only_oda_free_views_are_routed(self):
        views = _cad_api_views()
        self.assertEqual(set(views) - ODA_FREE_VIEWS, set(), views)
        self.assertEqual(views["cblcad_oda_removed_api"], set(ODA_ROUTES))

    def test_cad_page_without_the_mode_opens_the_free_mode(self):
        for path, location in (("/tools/cad/", "/tools/cad/?mode=free-dwg"),
                               ("/tools/cad/?v=5", "/tools/cad/?v=5&mode=free-dwg"),
                               ("/cblcad/", "/tools/cad/?mode=free-dwg")):
            with self.subTest(path=path):
                response = self.client.get(path, HTTP_HOST=HOST)
                self.assertEqual(response.status_code, 302)
                self.assertEqual(response["Location"], location)
        response = self.client.get("/tools/cad/?mode=free-dwg", HTTP_HOST=HOST)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'"freeDwgBrowser":true', response.content)


class CadOdaWindowHideTests(SimpleTestCase):
    def test_subprocess_is_not_wrapped_for_oda(self):
        # The wrapper started 90 osascripts per ODA run that never exited
        # (2,227 piled up on 2026-10-03 and the Mac could not fork).
        self.assertFalse(getattr(subprocess, "_cbl_oda_hide_safe_v2_installed", False))


class QuantityWithoutOdaTests(SimpleTestCase):
    def test_quantity_tool_has_no_oda_converter(self):
        # DWG goes through the free converter (test_quantity_free_dwg).
        self.assertFalse(hasattr(quantity_views, "_convert_dwg_folder_to_dxf"))
        self.assertFalse(hasattr(quantity_views, "_find_oda_converter"))
