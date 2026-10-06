import json
import shutil
import tempfile
from pathlib import Path
from unittest import skipUnless
from unittest.mock import patch

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, SimpleTestCase

from . import views as core_views
from .test_cad_dwg_text_validation import EXECUTABLE, _run_writer

FIXTURES = Path(settings.BASE_DIR) / "core" / "test_fixtures" / "cad"
KINDS = FIXTURES / "move_kinds_ac1032.dwg"
# DIMENSION_LINEAR of move_kinds; its block (*D3 to ACadSharp, "*D" to
# LibreDWG) holds 3 LINE, 3 POINT, 2 INSERT and 1 MTEXT.
DIMENSION = "93"
DWGREAD = core_views._cbl_free_dwg_local_find_dwgread_v1()


@skipUnless(EXECUTABLE is not None, "ACadSharp runtime is required")
@skipUnless(DWGREAD, "LibreDWG dwgread is only on development machines")
class CadDwgCopyLibreDwgTests(SimpleTestCase):
    """A copied DIMENSION saves when LibreDWG counts the entities.

    The copy brings a copy of the dimension's block.  Its contents were
    counted from the ACadSharp entities of the original, which only the read
    without dwgread carried, so with dwgread the copy was refused (LINE 7 to
    10, POINT 7 to 10, INSERT 4 to 6, MTEXT 2 to 3 with no delta).
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cbl-copy-libredwg-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def save(self, original, ops, restore=None):
        fields = {"ops": json.dumps({"ops": ops}), "target_version": "AC1018", "filename": "plan.dwg", "delivery": "binary",
                  "original_dwg": SimpleUploadedFile("plan.dwg", original, content_type="application/acad")}
        if restore is not None:
            fields["restore_dwg"] = SimpleUploadedFile("opened.dwg", restore, content_type="application/acad")
        request = RequestFactory().post("/api/cblcad/free-dwg-save/?mode=free-dwg", fields)
        with patch.object(core_views, "_cbl_is_free_dwg_request", return_value=True):
            return core_views.cblcad_free_dwg_save_local_api(request)

    def test_a_copied_dimension_saves(self):
        ops = [{"type": "add_copy", "copyOf": DIMENSION, "matrix": [1, 0, 0, 1, 3000, 0], "clientShapeId": "c1"}]
        response = self.save(KINDS.read_bytes(), ops)
        self.assertEqual(response.status_code, 200, response.content[:1500])
        self.assertEqual(response["X-CBL-FREE-DWG-SAVE-VALIDATED"], "1")

    def test_a_restored_dimension_saves(self):
        deleted = self.save(KINDS.read_bytes(), [{"type": "delete", "handle": DIMENSION, "sourceHandle": DIMENSION}])
        self.assertEqual(deleted.status_code, 200, deleted.content[:1500])
        ops = [{"type": "add_copy", "copyOf": DIMENSION, "fromOpened": True, "matrix": [1, 0, 0, 1, 0, 0], "clientShapeId": "r0"}]
        response = self.save(deleted.content, ops, restore=KINDS.read_bytes())
        self.assertEqual(response.status_code, 200, response.content[:1500])
        self.assertEqual(response["X-CBL-FREE-DWG-SAVE-VALIDATED"], "1")

    def test_block_contents_the_writer_does_not_name_are_refused(self):
        original_json = core_views._cbl_free_dwg_save_local_json_v1(KINDS, DWGREAD)
        ops = core_views._cbl_normalize_free_dwg_ops_v1(original_json, [
            {"type": "add_copy", "copyOf": DIMENSION, "matrix": [1, 0, 0, 1, 3000, 0], "clientShapeId": "c1"}])
        ops_path, output = self.tmp / "ops.json", self.tmp / "saved.dwg"
        ops_path.write_text(json.dumps({"ops": ops}), encoding="utf-8")
        report = _run_writer([KINDS, output, "AC1018", ops_path])
        core_views._cbl_free_dwg_save_local_validate_v1(KINDS, output, DWGREAD, ops, report, original_json=original_json)
        # The copied block's contents are expected only for the block the
        # writer says it copied; without it they are unexplained additions.
        report["editReport"]["applied"][0]["sourceBlock"] = ""
        with self.assertRaisesRegex(ValueError, "엔티티 종류별 보존 수") as caught:
            core_views._cbl_free_dwg_save_local_validate_v1(KINDS, output, DWGREAD, ops, report, original_json=original_json)
        self.assertEqual(set(caught.exception.diagnostics["mismatches"]), {"INSERT", "LINE", "MTEXT", "POINT"})
