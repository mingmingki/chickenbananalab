import json
import subprocess
import tempfile
from pathlib import Path
from unittest import skipUnless

from django.test import SimpleTestCase

from . import views as core_views

EXECUTABLE = core_views._cbl_free_dwg_save_local_executable_v1()


def _run_writer(args):
    run = subprocess.run([str(EXECUTABLE), *map(str, args)], capture_output=True, timeout=300)
    if run.returncode != 0:
        raise AssertionError(run.stderr.decode("utf-8", "replace")[-800:])
    return json.loads(run.stdout.decode("utf-8", "replace"), strict=False)


def _text_op(kind, text, x, y, **extra):
    op = {"type": kind, "entity": "TEXT", "layer": "0", "aci": 256, "color": 256, "linetype": "Continuous",
          "text": text, "insert": [x, y, 0], "height": 25, "rotation": 0, "textStyle": "Standard",
          "widthFactor": 1, "obliqueAngle": 0}
    op.update(extra)
    return op


@skipUnless(EXECUTABLE is not None, "ACadSharp runtime is not installed")
class CadDwgTextSaveValidationTests(SimpleTestCase):
    """Runs the real ACadSharp writer + save validation exactly as free-dwg-save does in production
    (no LibreDWG dwgread on the server)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cbl-text-validation-"))
        self.original = self.tmp / "original.dwg"
        create_ops = self.tmp / "create.json"
        create_ops.write_text(json.dumps({"ops": [
            {"type": "add_line", "layer": "0", "aci": 256, "linetype": "Continuous", "start": [0, 0, 0], "end": [500, 0, 0]},
            _text_op("add_text", "EXISTING TEXT", 0, 100),
            _text_op("add_text", "DELETE ME", 0, 200),
        ]}), encoding="utf-8")
        _run_writer(["--create", self.original, "AC1018", create_ops])
        self.handles = {
            item.get("text"): item.get("handle")
            for item in core_views._cbl_free_dwg_acadsharp_metadata_v1(self.original).get("entities", [])
            if item.get("text")
        }

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def save(self, ops, written_ops=None):
        """Mirror cblcad_free_dwg_save_local_api: normalize, write, validate (dwgread=None)."""
        original_for_ops = core_views._cbl_free_dwg_save_local_json_v1(self.original, None)
        ops = core_views._cbl_normalize_free_dwg_ops_v1(original_for_ops, ops)
        writer_ops = ops if written_ops is None else core_views._cbl_normalize_free_dwg_ops_v1(original_for_ops, written_ops)
        ops_path = self.tmp / "ops.json"
        ops_path.write_text(json.dumps({"ops": writer_ops}), encoding="utf-8")
        output = self.tmp / "saved.dwg"
        report = _run_writer([self.original, output, "AC1018", ops_path])
        core_views._cbl_free_dwg_save_local_validate_v1(self.original, output, None, ops, report)
        return {item.get("text") for item in core_views._cbl_free_dwg_acadsharp_metadata_v1(output).get("entities", []) if item.get("text")}

    def test_adding_text_to_an_existing_drawing_passes_validation(self):
        texts = self.save([_text_op("add_text", "CBL TEST 123", 300, 300)])
        self.assertIn("CBL TEST 123", texts)
        self.assertIn("EXISTING TEXT", texts)

    def test_deleting_existing_text_passes_validation(self):
        texts = self.save([{"type": "delete", "entity": "TEXT", "handle": self.handles["DELETE ME"]}])
        self.assertNotIn("DELETE ME", texts)
        self.assertIn("EXISTING TEXT", texts)

    def test_moving_existing_text_still_passes_validation(self):
        handle = self.handles["EXISTING TEXT"]
        texts = self.save([_text_op("update", "EXISTING TEXT", 30, 100, handle=handle, sourceHandle=handle)])
        self.assertIn("EXISTING TEXT", texts)

    def test_text_written_differently_from_the_edit_is_still_rejected(self):
        # Same entity counts, wrong content: only the TEXT-family check can catch this.
        with self.assertRaises(core_views._CBLFreeDwgSaveValidationError) as caught:
            self.save([_text_op("add_text", "CBL TEST 123", 300, 300)],
                      written_ops=[_text_op("add_text", "WRONG TEXT", 300, 300)])
        diagnostics = caught.exception.diagnostics
        self.assertEqual([item["text"] for item in diagnostics["missing"]], ["CBL TEST 123"])
        self.assertEqual([item["text"] for item in diagnostics["extra"]], ["WRONG TEXT"])
