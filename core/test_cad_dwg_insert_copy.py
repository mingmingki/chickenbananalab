import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from unittest import skipUnless

from django.conf import settings
from django.test import SimpleTestCase

from . import views as core_views

try:
    import ezdxf
except ImportError:  # pragma: no cover - verification dependency only
    ezdxf = None

EXECUTABLE = core_views._cbl_free_dwg_save_local_executable_v1()
FIXTURE = Path(settings.BASE_DIR) / "core" / "test_fixtures" / "cad" / "insert_attr_ac1018.dwg"
# Fixture: block BLK (circle r10 + ATTDEF TAG at 0,-15); INSERT 34 at (100,100) with TAG="ROOM-1" at (100,85).


@skipUnless(EXECUTABLE is not None and ezdxf is not None, "ACadSharp runtime and ezdxf are required")
class CadDwgInsertCopyTests(SimpleTestCase):
    """Copying a block reference in the editor must add an INSERT, keeping its attribute values."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cbl-insert-copy-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def save(self, ops, expect_ok=True):
        ops_path = self.tmp / "ops.json"
        ops_path.write_text(json.dumps({"ops": ops}), encoding="utf-8")
        output = self.tmp / "out.dwg"
        run = subprocess.run([str(EXECUTABLE), str(FIXTURE), str(output), "AC1018", str(ops_path)],
                             capture_output=True, timeout=300)
        if not expect_ok:
            return run
        self.assertEqual(run.returncode, 0, run.stderr.decode("utf-8", "replace")[-800:])
        dxf = self.tmp / "out.dxf"
        run = subprocess.run([str(EXECUTABLE), "--dxf", str(output), str(dxf)], capture_output=True, timeout=300)
        self.assertEqual(run.returncode, 0, run.stderr.decode("utf-8", "replace")[-800:])
        return ezdxf.readfile(str(dxf)).modelspace()

    @staticmethod
    def inserts(msp):
        return sorted((i.dxf.name, round(i.dxf.insert.x, 6), round(i.dxf.insert.y, 6),
                       [(a.dxf.tag, a.dxf.text, round(a.dxf.insert.x, 6), round(a.dxf.insert.y, 6)) for a in i.attribs])
                      for i in msp.query("INSERT"))

    def test_copy_clones_the_source_insert_and_moves_its_attributes(self):
        msp = self.save([{"type": "add_insert", "entity": "INSERT", "copyOf": "34", "blockName": "BLK", "layer": "0",
                          "insert": [150, 100, 0], "rotation": 0, "scale": [1, 1, 1]}])
        self.assertEqual(self.inserts(msp), [
            ("BLK", 100.0, 100.0, [("TAG", "ROOM-1", 100.0, 85.0)]),
            ("BLK", 150.0, 100.0, [("TAG", "ROOM-1", 150.0, 85.0)]),
        ])

    def test_new_insert_by_block_name(self):
        msp = self.save([{"type": "add_insert", "entity": "INSERT", "blockName": "BLK", "layer": "0",
                          "insert": [0, 0, 0], "rotation": 0, "scale": [2, 2, 1]}])
        names = [(i.dxf.name, round(i.dxf.insert.x, 6), round(i.dxf.xscale, 6)) for i in msp.query("INSERT")]
        self.assertEqual(sorted(names), [("BLK", 0.0, 2.0), ("BLK", 100.0, 1.0)])

    def test_rotated_copy_with_attributes_is_refused(self):
        # Attribute placement under rotation/mirroring is not implemented; fail instead of misplacing text.
        run = self.save([{"type": "add_insert", "entity": "INSERT", "copyOf": "34", "blockName": "BLK", "layer": "0",
                          "insert": [150, 100, 0], "rotation": 1.5707963267948966, "scale": [1, 1, 1]}], expect_ok=False)
        self.assertNotEqual(run.returncode, 0)
        self.assertIn("attribute", run.stderr.decode("utf-8", "replace").lower())

    def test_unknown_block_is_refused(self):
        run = self.save([{"type": "add_insert", "entity": "INSERT", "blockName": "NOPE", "layer": "0",
                          "insert": [0, 0, 0]}], expect_ok=False)
        self.assertNotEqual(run.returncode, 0)
