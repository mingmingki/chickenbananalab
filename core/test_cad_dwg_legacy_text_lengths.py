import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from unittest import skipUnless
from unittest.mock import patch

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, SimpleTestCase

from . import views as core_views
from .test_cad_dwg_save_integrity import NODE, _html
from .test_cad_dwg_text_validation import EXECUTABLE, _run_writer
from .test_cad_dwg_xrecord_text import XDATA, XRECORD, _mif

FIXTURES = Path(settings.BASE_DIR) / "core" / "test_fixtures" / "cad"
# korean_xrecord_ac1018.dwg re-saved by a throwaway program built on the
# ACadSharp writer as it was before 2026-10-01: the Korean XRECORD and
# extended-data strings carry their character count where the byte count
# belongs (Korean takes two bytes per character).  ODA stops on it with
# "Invalid group code" unless it audits; the reader used to cut the strings
# short ("Unknown code for extended data") and a save kept them cut.
LEGACY = FIXTURES / "korean_xrecord_legacy_lengths_ac1018.dwg"
WELL_FORMED = FIXTURES / "korean_xrecord_ac1018.dwg"
ODA = core_views._cbl_v29_find_oda()


def _legacy_notes(meta):
    return [n["Message"] for n in meta["notifications"] if n["Message"].startswith(core_views._CBL_LEGACY_TEXT_LENGTH_PREFIX_V1)]


@skipUnless(EXECUTABLE is not None, "ACadSharp runtime is required")
class CadLegacyTextLengthTests(SimpleTestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cbl-legacy-lengths-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def runtime_dxf(self, dwg):
        dxf = self.tmp / (dwg.stem + ".dxf")
        run = subprocess.run([str(EXECUTABLE), "--dxf", str(dwg), str(dxf)], capture_output=True, timeout=300)
        self.assertEqual(run.returncode, 0, run.stderr.decode("utf-8", "replace")[-800:])
        return dxf.read_bytes().decode("cp949", "replace")

    def test_strings_with_character_count_lengths_are_read_in_full(self):
        dxf = self.runtime_dxf(LEGACY)
        self.assertIn(XRECORD, dxf)
        self.assertIn(XDATA, dxf)
        meta = core_views._cbl_free_dwg_acadsharp_metadata_v1(LEGACY)
        messages = [n["Message"] for n in meta["notifications"]]
        self.assertFalse([m for m in messages if m.startswith("Unknown code for extended data")], messages)
        self.assertEqual(len(_legacy_notes(meta)), 2, messages)

    def test_well_formed_strings_are_not_flagged(self):
        self.assertEqual(_legacy_notes(core_views._cbl_free_dwg_acadsharp_metadata_v1(WELL_FORMED)), [])

    def test_unedited_save_writes_byte_lengths(self):
        ops_path = self.tmp / "ops.json"
        ops_path.write_text(json.dumps({"ops": []}), encoding="utf-8")
        output = self.tmp / "saved.dwg"
        report = _run_writer([LEGACY, output, "AC1018", ops_path])
        core_views._cbl_free_dwg_save_local_validate_v1(LEGACY, output, None, [], report)
        self.assertEqual(_legacy_notes(core_views._cbl_free_dwg_acadsharp_metadata_v1(output)), [])
        dxf = self.runtime_dxf(output)
        self.assertIn(XRECORD, dxf)
        self.assertIn(XDATA, dxf)
        if ODA:
            # ODA without audit reads the saved file again, strings intact.
            src, out = self.tmp / "oda-in", self.tmp / "oda-out"
            src.mkdir()
            out.mkdir()
            shutil.copy(output, src / "saved.dwg")
            subprocess.run([ODA, str(src), str(out), "ACAD2018", "DXF", "0", "0", "*.DWG"], capture_output=True, timeout=300)
            self.assertFalse((out / "saved.dxf.err").exists(), (out / "saved.dxf.err").read_text(errors="replace")
                             if (out / "saved.dxf.err").exists() else "")
            text = _mif((out / "saved.dxf").read_text(encoding="utf-8", errors="replace"))
            self.assertIn(XRECORD, text)
            self.assertIn(XDATA, text)

    def test_open_api_reports_legacy_lengths(self):
        def open_count(path):
            request = RequestFactory().post(
                "/api/cblcad/free-dwg-to-dxf/?format=acadsharp-dxf&mode=free-dwg",
                {"file": SimpleUploadedFile(path.name, path.read_bytes(), content_type="application/acad")})
            with patch.object(core_views, "_cbl_is_free_dwg_request", return_value=True):
                response = core_views.cblcad_free_dwg_local_api(request)
            self.assertEqual(response.status_code, 200)
            return json.loads(response.content)["legacy_text_lengths"]
        self.assertEqual(open_count(LEGACY), 2)
        self.assertEqual(open_count(WELL_FORMED), 0)


class CadLegacyTextLengthCountTests(SimpleTestCase):
    def test_counts_only_legacy_notices(self):
        prefix = core_views._CBL_LEGACY_TEXT_LENGTH_PREFIX_V1
        notes = [{"Message": prefix + " extended data of handle 30"}, {"Message": prefix + " XRECORD 3A"},
                 {"Message": "Unknown code for extended data: 1204"}, "junk"]
        self.assertEqual(core_views._cbl_free_dwg_legacy_text_lengths_v1(notes), 2)
        self.assertEqual(core_views._cbl_free_dwg_legacy_text_lengths_v1(None), 0)


@skipUnless(NODE, "node is required to execute the CAD editor helpers")
class CadLegacyTextLengthEditorTests(SimpleTestCase):
    def test_open_message(self):
        html = _html()
        start = html.index("function cblLegacyTextLengthsMessageV1(count){")
        line_start = html.rfind("\n", 0, start) + 1
        indent = html[line_start:start]
        end = html.index("\n" + indent + "}", start) + len(indent) + 2
        script = html[line_start:end] + """
process.stdout.write(JSON.stringify([cblLegacyTextLengthsMessageV1(2), cblLegacyTextLengthsMessageV1(0),
                                     cblLegacyTextLengthsMessageV1(undefined)]));"""
        run = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=60)
        self.assertEqual(run.returncode, 0, run.stderr)
        message, zero, missing = json.loads(run.stdout)
        self.assertIn("2곳", message)
        self.assertIn("AutoCAD", message)
        self.assertIn("저장하면 바로잡힙니다", message)
        self.assertEqual((zero, missing), ("", ""))

    def test_open_shows_it_with_the_unreadable_notice(self):
        html = _html()
        body = html[html.index("async function cblFreeDwgOpenFileObjectV1(file,handle){"):]
        body = body[:body.index("function cblNativeFilePickerSupportedV1(){")]
        done = body.index("setHint('DWG 열기 완료: '")
        legacy = body.index("cblLegacyTextLengthsMessageV1(result.legacy_text_lengths)")
        self.assertLess(done, legacy)
        self.assertLess(body.index("cblUnreadableObjectsMessageV1(result.unreadable_objects)"), legacy)
