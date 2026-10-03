import contextlib
import json
import re
import tempfile
import threading
from pathlib import Path
from unittest import skipUnless
from unittest.mock import patch

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, SimpleTestCase, override_settings

from . import views as core_views
from .test_cad_dwg_text_validation import EXECUTABLE

FIXTURE = Path(settings.BASE_DIR) / "core" / "test_fixtures" / "cad" / "arc_bulge_ac1018.dwg"


class AcadSharpSlotTests(SimpleTestCase):
    """At most CBLCAD_ACADSHARP_SLOTS converter runs at once, across processes (flock).

    A large drawing takes up to ~700 MB in the converter and the server has
    2 GB; opens, saves and quantity jobs used to start runs without limit.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = patch.object(core_views, "_CBL_ACADSHARP_SLOT_ROOT_V1", Path(self.tmp.name) / "slots")
        root.start()
        self.addCleanup(root.stop)

    @override_settings(CBLCAD_ACADSHARP_SLOTS=2)
    def test_runs_beyond_the_slots_wait_then_report_busy(self):
        with core_views._cbl_acadsharp_slot_v1(wait=0) as first, core_views._cbl_acadsharp_slot_v1(wait=0) as second:
            self.assertEqual({first, second}, {0, 1})
            with self.assertRaises(core_views._CBLAcadSharpBusy) as caught:
                with core_views._cbl_acadsharp_slot_v1(wait=0.3):
                    pass
            self.assertIn("잠시 후 다시", str(caught.exception))
        with core_views._cbl_acadsharp_slot_v1(wait=0) as again:
            self.assertEqual(again, 0)

    @override_settings(CBLCAD_ACADSHARP_SLOTS=2)
    def test_background_runs_leave_the_other_slots_to_the_editor(self):
        with core_views._cbl_acadsharp_slot_v1(wait=0, background=True) as background:
            self.assertEqual(background, 0)
            with self.assertRaises(core_views._CBLAcadSharpBusy):
                with core_views._cbl_acadsharp_slot_v1(wait=0, background=True):
                    pass
            with core_views._cbl_acadsharp_slot_v1(wait=0) as editor:
                self.assertEqual(editor, 1)

    @override_settings(CBLCAD_ACADSHARP_SLOTS=1)
    def test_a_slot_held_by_another_thread_blocks(self):
        held, release = threading.Event(), threading.Event()

        def hold():
            with core_views._cbl_acadsharp_slot_v1(wait=0):
                held.set()
                release.wait(5)

        worker = threading.Thread(target=hold)
        worker.start()
        try:
            self.assertTrue(held.wait(5))
            with self.assertRaises(core_views._CBLAcadSharpBusy):
                with core_views._cbl_acadsharp_slot_v1(wait=0.2):
                    pass
        finally:
            release.set()
            worker.join(5)
        with core_views._cbl_acadsharp_slot_v1(wait=1):
            pass

    def test_every_converter_run_goes_through_the_slots(self):
        # The only subprocess.run of the converter is inside _cbl_acadsharp_run_v1.
        source = Path(core_views.__file__).read_text(encoding="utf-8")
        runs = [m for m in re.finditer(r"_cbl_subprocess\.run\(\s*(\[str\(executable\)|command\b)", source)]
        helper = source.index("def _cbl_acadsharp_run_v1(")
        helper_end = source.index("\ndef ", helper + 10)
        outside = [source[m.start():m.start() + 120] for m in runs if not helper < m.start() < helper_end]
        self.assertEqual(outside, [])
        self.assertIn("_cbl_subprocess.run(command", source[helper:helper_end])


@skipUnless(EXECUTABLE is not None, "ACadSharp runtime is required")
class AcadSharpSlotUseTests(SimpleTestCase):
    def setUp(self):
        self.acquired = []

        @contextlib.contextmanager
        def record(wait=60, background=False):
            self.acquired.append(background)
            yield 0

        recorder = patch.object(core_views, "_cbl_acadsharp_slot_v1", side_effect=record)
        recorder.start()
        self.addCleanup(recorder.stop)

    def test_metadata_reads_and_dxf_conversion_take_a_slot(self):
        core_views._cbl_free_dwg_acadsharp_metadata_v1(FIXTURE)
        core_views._cbl_free_dwg_local_acadsharp_metadata_v1(FIXTURE)
        core_views._cbl_free_dwg_to_dxf_text_v1(FIXTURE, background=True)
        self.assertEqual(self.acquired, [False, False, True])

    def test_open_api_takes_a_slot(self):
        request = RequestFactory().post(
            "/api/cblcad/free-dwg-to-dxf/?format=acadsharp-dxf&mode=free-dwg",
            {"file": SimpleUploadedFile("a.dwg", FIXTURE.read_bytes(), content_type="application/acad")})
        with patch.object(core_views, "_cbl_is_free_dwg_request", return_value=True):
            response = core_views.cblcad_free_dwg_local_api(request)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.acquired, [False])


class AcadSharpBusyResponseTests(SimpleTestCase):
    def busy(self, wait=60, background=False):
        raise core_views._CBLAcadSharpBusy(core_views._CBL_ACADSHARP_BUSY_MESSAGE_V1)

    @skipUnless(EXECUTABLE is not None, "ACadSharp runtime is required")
    def test_open_and_save_answer_503_when_the_converter_is_busy(self):
        with patch.object(core_views, "_cbl_acadsharp_slot_v1", side_effect=self.busy), \
             patch.object(core_views, "_cbl_is_free_dwg_request", return_value=True), \
             patch.object(core_views, "_cbl_free_dwg_local_find_dwgread_v1", return_value=None):
            opened = core_views.cblcad_free_dwg_local_api(RequestFactory().post(
                "/api/cblcad/free-dwg-to-dxf/?format=acadsharp-dxf&mode=free-dwg",
                {"file": SimpleUploadedFile("a.dwg", FIXTURE.read_bytes(), content_type="application/acad")}))
            saved = core_views.cblcad_free_dwg_save_local_api(RequestFactory().post(
                "/api/cblcad/free-dwg-save/?mode=free-dwg",
                {"original_dwg": SimpleUploadedFile("a.dwg", FIXTURE.read_bytes(), content_type="application/acad"),
                 "ops": "[]", "target_version": "AC1018", "filename": "a.dwg"}))
        for response in (opened, saved):
            self.assertEqual(response.status_code, 503)
            body = json.loads(response.content)
            self.assertEqual((body["ok"], body.get("busy")), (False, True))
            self.assertIn("잠시 후 다시", body["error"])
