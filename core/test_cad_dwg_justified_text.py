import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from unittest import skipUnless

from django.conf import settings
from django.test import SimpleTestCase

from . import views as core_views

EXECUTABLE = core_views._cbl_free_dwg_save_local_executable_v1()
# Built once with ezdxf (LEFT NOTE at (0,20); CENTER TITLE middle-center at
# (150,60)) and converted to AC1018 offline; tests never run ODA.
FIXTURE = Path(settings.BASE_DIR) / "core" / "test_fixtures" / "cad" / "justified_text_ac1018.dwg"


def _texts(path):
    return {
        item["text"]: item
        for item in core_views._cbl_free_dwg_acadsharp_metadata_v1(path).get("entities", [])
        if item.get("text")
    }


@skipUnless(EXECUTABLE is not None, "ACadSharp runtime is not installed")
class CadDwgJustifiedTextMoveTests(SimpleTestCase):
    """Justified TEXT is positioned by its alignment point (DXF 11) in AutoCAD,
    so a move must shift it together with the insertion point (DXF 10)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cbl-justified-text-"))
        self.original = self.tmp / "original.dwg"
        shutil.copyfile(FIXTURE, self.original)
        self.before = _texts(self.original)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def move(self, text, dx):
        item = self.before[text]
        x, y, z = item["insert"]["point"]
        op = {"type": "update", "entity": "TEXT", "handle": item["handle"], "sourceHandle": item["handle"],
              "layer": "0", "text": text, "insert": [x + dx, y, z], "height": item["insert"]["height"],
              "rotation": item["insert"]["rotation"]}
        original_for_ops = core_views._cbl_free_dwg_save_local_json_v1(self.original, None)
        ops = core_views._cbl_normalize_free_dwg_ops_v1(original_for_ops, [op])
        ops_path = self.tmp / "ops.json"
        ops_path.write_text(json.dumps({"ops": ops}), encoding="utf-8")
        output = self.tmp / "saved.dwg"
        run = subprocess.run([str(EXECUTABLE), str(self.original), str(output), "AC1018", str(ops_path)],
                             capture_output=True, timeout=300)
        self.assertEqual(run.returncode, 0, run.stderr.decode("utf-8", "replace")[-800:])
        report = json.loads(run.stdout.decode("utf-8", "replace"), strict=False)
        core_views._cbl_free_dwg_save_local_validate_v1(self.original, output, None, ops, report)
        return _texts(output)[text]

    def test_moving_centered_text_moves_its_alignment_point(self):
        before = self.before["CENTER TITLE"]
        self.assertEqual((before["alignment"]["horizontal"], before["alignment"]["vertical"]), (1, 2))
        after = self.move("CENTER TITLE", 30)
        self.assertAlmostEqual(after["insert"]["point"][0], before["insert"]["point"][0] + 30)
        self.assertAlmostEqual(after["alignment"]["point"][0], before["alignment"]["point"][0] + 30)
        self.assertAlmostEqual(after["alignment"]["point"][1], before["alignment"]["point"][1])
        self.assertEqual((after["alignment"]["horizontal"], after["alignment"]["vertical"]), (1, 2))

    def test_moving_left_aligned_text_leaves_alignment_unused(self):
        before = self.before["LEFT NOTE"]
        after = self.move("LEFT NOTE", 30)
        self.assertAlmostEqual(after["insert"]["point"][0], before["insert"]["point"][0] + 30)
        self.assertEqual(after["alignment"], before["alignment"])
