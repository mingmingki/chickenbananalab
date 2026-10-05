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
from .test_cad_dwg_text_validation import EXECUTABLE

FIXTURES = Path(settings.BASE_DIR) / "core" / "test_fixtures" / "cad"
PLAN = FIXTURES / "quantity_plan_ac1018.dwg"


def _first_line(path):
    meta = core_views._cbl_free_dwg_acadsharp_metadata_v1(path)
    return next(e for e in meta["entities"] if e.get("space") == "modelspace" and e["type"] == "LINE")


@skipUnless(EXECUTABLE is not None, "ACadSharp runtime is required")
class CadSaveConverterRunsTests(SimpleTestCase):
    """A save starts the converter twice on the server (no LibreDWG there).

    It used to start it four times: the original was read for the edit
    targets and again for the validation, and the saved file was read once
    more although the writer had just reread it.  A 6 MB drawing (S-501-16)
    took ~12 s on a Mac and ~35 s on the server, 95% of it in the converter.
    """

    def save(self, ops, original=PLAN, reread_json=None):
        runs = []
        real = core_views._cbl_acadsharp_run_v1

        def run(command, timeout, **kwargs):
            runs.append([str(part) for part in command[1:]])
            return real(command, timeout, **kwargs)

        fields = {"ops": json.dumps({"ops": ops}), "target_version": "AC1018", "filename": "plan.dwg",
                  "original_dwg": SimpleUploadedFile("plan.dwg", original.read_bytes(), content_type="application/acad")}
        request = RequestFactory().post("/api/cblcad/free-dwg-save/?mode=free-dwg", fields)
        patches = [patch.object(core_views, "_cbl_is_free_dwg_request", return_value=True),
                   patch.object(core_views, "_cbl_free_dwg_local_find_dwgread_v1", return_value=None),
                   patch.object(core_views, "_cbl_acadsharp_run_v1", side_effect=run)]
        if reread_json is not None:
            patches.append(patch.object(core_views, "_cbl_free_dwg_reread_metadata_json_v1", side_effect=reread_json))
        for item in patches:
            item.start()
        try:
            return core_views.cblcad_free_dwg_save_local_api(request), runs
        finally:
            for item in reversed(patches):
                item.stop()

    def move_op(self):
        return [{"type": "move", "handle": _first_line(PLAN)["handle"], "delta": [0, -100, 0]}]

    def test_a_save_reads_the_original_once_and_takes_the_saved_read_from_the_writer(self):
        response, runs = self.save(self.move_op())
        self.assertEqual(response.status_code, 200, response.content[:300])
        self.assertEqual(response["X-CBL-FREE-DWG-SAVE-VALIDATED"], "1")
        self.assertEqual(len(runs), 2, runs)
        self.assertEqual(runs[0][0], "--metadata")
        self.assertIn("--reread-metadata", runs[1])

    def test_the_writer_read_is_what_gets_validated(self):
        # Drop one entity from the writer's reread: the save must be refused.
        real = core_views._cbl_free_dwg_reread_metadata_json_v1

        def without_an_entity(path):
            saved = real(path)
            first = next(i for i, row in enumerate(saved["OBJECTS"]) if row.get("entity") == "LINE")
            del saved["OBJECTS"][first]
            return saved

        response, _ = self.save(self.move_op(), reread_json=without_an_entity)
        self.assertEqual(response.status_code, 400, response.content[:300])
        self.assertIn("저장 검증 실패", json.loads(response.content)["error"])

    def test_without_the_writer_read_the_saved_file_is_read_again(self):
        # An older runtime writes no reread metadata.
        response, runs = self.save(self.move_op(), reread_json=lambda path: None)
        self.assertEqual(response.status_code, 200, response.content[:300])
        self.assertEqual([run[0] for run in runs], ["--metadata", runs[1][0], "--metadata"])
        self.assertTrue(runs[2][1].endswith("saved_AC1018.dwg"), runs[2])


@skipUnless(EXECUTABLE is not None, "ACadSharp runtime is required")
class CadWriterRereadMetadataTests(SimpleTestCase):
    """--reread-metadata writes exactly what --metadata reads from the saved file."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cbl-reread-meta-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_matches_a_separate_read(self):
        ops = self.tmp / "ops.json"
        ops.write_text(json.dumps({"ops": [{"type": "move", "handle": _first_line(PLAN)["handle"], "delta": [0, 50, 0]}]}))
        cases = [(PLAN, ops), (FIXTURES / "region_acds_ac1032.dwg", None), (FIXTURES / "korean_utf8_bytes_ac1018.dwg", None),
                 (FIXTURES / "korean_xrecord_ac1018.dwg", None), (FIXTURES / "sortents_dgn_ac1018.dwg", None)]
        for source, case_ops in cases:
            with self.subTest(source.name):
                output, metadata = self.tmp / f"{source.stem}.saved.dwg", self.tmp / f"{source.stem}.json"
                empty = self.tmp / "empty.json"
                empty.write_text('{"ops": []}')
                run = subprocess.run([str(EXECUTABLE), str(source), str(output), "AC1018", str(case_ops or empty),
                                      "--reread-metadata", str(metadata)], capture_output=True, timeout=300)
                self.assertEqual(run.returncode, 0, run.stderr[-800:])
                self.assertEqual(json.loads(run.stdout, strict=False)["status"], "written_and_reread")
                separate = subprocess.run([str(EXECUTABLE), "--metadata", str(output)], capture_output=True, timeout=300)
                self.assertEqual(json.loads(metadata.read_text(encoding="utf-8"), strict=False),
                                 json.loads(separate.stdout, strict=False))

    def test_the_writer_still_runs_without_it(self):
        output = self.tmp / "plain.dwg"
        run = subprocess.run([str(EXECUTABLE), str(PLAN), str(output), "AC1018"], capture_output=True, timeout=300)
        self.assertEqual(run.returncode, 0, run.stderr[-800:])
        self.assertFalse(list(self.tmp.glob("*.json")))
