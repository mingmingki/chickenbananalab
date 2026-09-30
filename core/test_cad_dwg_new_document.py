import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from unittest import skipUnless

from django.test import SimpleTestCase

from . import views as core_views

EXECUTABLE = core_views._cbl_free_dwg_save_local_executable_v1()


@skipUnless(EXECUTABLE is not None, "ACadSharp runtime is not installed")
class CadDwgNewDocumentKoreanTests(SimpleTestCase):
    """A drawing created in the editor (--create) must keep Korean layer names and text."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cbl-new-doc-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def create(self, ops):
        ops_path = self.tmp / "ops.json"
        ops_path.write_text(json.dumps({"ops": ops}, ensure_ascii=False), encoding="utf-8")
        output = self.tmp / "new.dwg"
        run = subprocess.run([str(EXECUTABLE), "--create", str(output), "AC1018", str(ops_path)],
                             capture_output=True, timeout=300)
        self.assertEqual(run.returncode, 0, run.stderr.decode("utf-8", "replace")[-800:])
        return core_views._cbl_free_dwg_acadsharp_metadata_v1(output)

    def test_korean_layer_name_and_text_survive_a_new_drawing(self):
        # Same ops the editor sends for a new drawing with a line and text on "기본".
        meta = self.create([
            {"type": "create_layer", "name": "기본", "color": 256, "linetype": "Continuous"},
            {"type": "add_line", "layer": "기본", "aci": 256, "linetype": "Continuous",
             "start": [0, 0, 0], "end": [100, 0, 0]},
            {"type": "add_text", "entity": "TEXT", "layer": "기본", "aci": 256, "text": "검증용 한글 텍스트",
             "insert": [0, 20, 0], "height": 10, "rotation": 0, "textStyle": "STANDARD",
             "widthFactor": 1, "obliqueAngle": 0},
        ])
        # ACadSharp names the Korean code page (DWG index 40, ANSI_949) "kcs5601".
        self.assertIn(str(meta["codePage"]).upper(), {"KCS5601", "ANSI_949"})
        self.assertIn("기본", [layer["name"] for layer in meta["layers"]])
        texts = [item for item in meta["entities"] if item.get("text")]
        self.assertEqual([item["text"] for item in texts], ["검증용 한글 텍스트"])
        self.assertEqual(texts[0]["layer"]["name"], "기본")

    def test_ascii_only_new_drawing_still_works(self):
        meta = self.create([
            {"type": "add_line", "layer": "0", "aci": 256, "linetype": "Continuous",
             "start": [0, 0, 0], "end": [100, 0, 0]},
            {"type": "add_text", "entity": "TEXT", "layer": "0", "aci": 256, "text": "PLAN A",
             "insert": [0, 20, 0], "height": 10, "rotation": 0, "textStyle": "STANDARD"},
        ])
        self.assertEqual([item["text"] for item in meta["entities"] if item.get("text")], ["PLAN A"])
