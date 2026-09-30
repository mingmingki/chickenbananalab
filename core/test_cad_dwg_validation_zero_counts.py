import json
import shutil
import tempfile
from pathlib import Path
from unittest import skipUnless

from django.conf import settings
from django.test import SimpleTestCase

from . import views as core_views
from .test_cad_dwg_text_validation import EXECUTABLE, _run_writer, _text_op

INSERT_FIXTURE = Path(settings.BASE_DIR) / "core" / "test_fixtures" / "cad" / "insert_attr_ac1018.dwg"


@skipUnless(EXECUTABLE is not None, "ACadSharp runtime is not installed")
class CadDwgSaveValidationZeroCountTests(SimpleTestCase):
    """Deleting the last entity of a type leaves a 0 in the expected counts; that is not a mismatch."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cbl-zero-count-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def save(self, original, ops):
        original_for_ops = core_views._cbl_free_dwg_save_local_json_v1(original, None)
        ops = core_views._cbl_normalize_free_dwg_ops_v1(original_for_ops, ops)
        ops_path = self.tmp / "ops.json"
        ops_path.write_text(json.dumps({"ops": ops}), encoding="utf-8")
        output = self.tmp / "saved.dwg"
        report = _run_writer([original, output, "AC1018", ops_path])
        core_views._cbl_free_dwg_save_local_validate_v1(original, output, None, ops, report)
        return [item.get("type") for item in core_views._cbl_free_dwg_acadsharp_metadata_v1(output).get("entities", [])]

    def test_deleting_the_only_circle_passes_validation(self):
        original = self.tmp / "original.dwg"
        create = self.tmp / "create.json"
        create.write_text(json.dumps({"ops": [
            {"type": "add_line", "layer": "0", "start": [0, 0, 0], "end": [100, 0, 0]},
            {"type": "add_circle", "layer": "0", "center": [50, 50, 0], "radius": 10},
        ]}), encoding="utf-8")
        _run_writer(["--create", original, "AC1018", create])
        circle = next(item["handle"] for item in core_views._cbl_free_dwg_acadsharp_metadata_v1(original)["entities"]
                      if item["type"] == "CIRCLE")
        types = self.save(original, [{"type": "delete", "handle": circle, "entity": "CIRCLE"},
                                     {"type": "add_line", "layer": "0", "start": [0, 10, 0], "end": [100, 10, 0]}])
        self.assertEqual(sorted(types), ["LINE", "LINE"])

    def test_exploding_the_only_block_passes_validation(self):
        types = self.save(INSERT_FIXTURE, [
            {"type": "delete", "handle": "34", "entity": "INSERT", "blockName": "BLK", "insert": [100, 100, 0]},
            {"type": "add_circle", "layer": "0", "center": [100, 100, 0], "radius": 10},
            _text_op("add_text", "ROOM-1", 100, 85, height=2.5),
        ])
        self.assertIn("CIRCLE", types)
        self.assertNotIn("INSERT", types)

    def test_deleting_every_entity_passes_validation(self):
        # Without blocks the saved model space is empty; that is the expected result, not a failed read.
        original = self.tmp / "lines_only.dwg"
        create = self.tmp / "create_lines.json"
        create.write_text(json.dumps({"ops": [
            {"type": "add_line", "layer": "0", "start": [0, 0, 0], "end": [100, 0, 0]},
            {"type": "add_line", "layer": "0", "start": [0, 10, 0], "end": [100, 10, 0]},
        ]}), encoding="utf-8")
        _run_writer(["--create", original, "AC1018", create])
        handles = [item["handle"] for item in core_views._cbl_free_dwg_acadsharp_metadata_v1(original)["entities"]]
        self.assertEqual(len(handles), 2)
        types = self.save(original, [{"type": "delete", "handle": h, "entity": "LINE"} for h in handles])
        self.assertEqual(types, [])

    def test_losing_every_entity_unexpectedly_still_fails(self):
        original = self.tmp / "lines_only2.dwg"
        create = self.tmp / "create_lines2.json"
        create.write_text(json.dumps({"ops": [
            {"type": "add_line", "layer": "0", "start": [0, 0, 0], "end": [100, 0, 0]},
            {"type": "add_line", "layer": "0", "start": [0, 10, 0], "end": [100, 10, 0]},
        ]}), encoding="utf-8")
        _run_writer(["--create", original, "AC1018", create])
        handles = [item["handle"] for item in core_views._cbl_free_dwg_acadsharp_metadata_v1(original)["entities"]]
        # The writer deletes both lines, but the ops given to the validator claim only one delete.
        original_for_ops = core_views._cbl_free_dwg_save_local_json_v1(original, None)
        writer_ops = core_views._cbl_normalize_free_dwg_ops_v1(original_for_ops, [{"type": "delete", "handle": h, "entity": "LINE"} for h in handles])
        ops_path = self.tmp / "ops.json"
        ops_path.write_text(json.dumps({"ops": writer_ops}), encoding="utf-8")
        output = self.tmp / "saved.dwg"
        report = _run_writer([original, output, "AC1018", ops_path])
        with self.assertRaises(Exception):
            core_views._cbl_free_dwg_save_local_validate_v1(original, output, None, writer_ops[:1], report)
