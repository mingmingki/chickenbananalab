import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from unittest import skipUnless

from django.conf import settings
from django.test import SimpleTestCase

from . import views as core_views
from .test_cad_dwg_text_validation import EXECUTABLE, _run_writer
from .test_oda_review import find_oda

# ezdxf + ODA AC1018 drawing.  Three overlapping SOLIDs 2F (red), 30 (green)
# and 31 (blue) whose draw order (model space SORTENTS) is 30, 31, 2F, unlike
# their handle order.  ACAD_DGNLINESTYLECOMP holds three SORTENTSTABLE objects
# without a block under their own keys, as drawings that came through DGN do
# (S-501 has 8,992).  ACadSharp filed every SORTENTSTABLE under
# "ACAD_SORTENTS": the first kept that wrong key, the rest were dropped
# ("Error when trying to add the entry ACAD_SORTENTS ...").
FIXTURE = Path(settings.BASE_DIR) / "core" / "test_fixtures" / "cad" / "sortents_dgn_ac1018.dwg"
DGN_KEYS = ["PLAN.dgn-StrokePattern-1", "PLAN.dgn-StrokePattern-2", "PLAN.dgn-StrokePattern-3"]
DRAW_ORDER = {"2F": "300", "30": "100", "31": "200"}
ODA = find_oda()


def _objects(dxf_text):
    lines = dxf_text.split("\n")
    pairs = [(lines[i].strip(), lines[i + 1].strip()) for i in range(0, len(lines) - 1, 2)]
    objects, current = {}, None
    for code, value in pairs:
        if code == "0":
            current = {"type": value, "codes": []}
        elif current is not None:
            current["codes"].append((code, value))
            if code == "5" and "handle" not in current:
                current["handle"] = value.upper()
                objects[value.upper()] = current
    return objects


def _dictionary(objects, name):
    """Entries {key: (type, handle)} of the dictionary filed under `name` anywhere."""
    for obj in objects.values():
        if obj["type"] != "DICTIONARY":
            continue
        key = None
        for code, value in obj["codes"]:
            if code == "3":
                key = value
            elif code in ("350", "360") and key == name:
                entries, child_key = {}, None
                for child_code, child_value in objects[value.upper()]["codes"]:
                    if child_code == "3":
                        child_key = child_value
                    elif child_code in ("350", "360") and child_key is not None:
                        entries[child_key] = (objects.get(child_value.upper(), {}).get("type"), child_value.upper())
                        child_key = None
                return entries
            elif code in ("350", "360"):
                key = None
    return None


def _model_space_draw_order(objects):
    for obj in objects.values():
        if obj["type"] != "SORTENTSTABLE":
            continue
        owners = [value for code, value in obj["codes"] if code == "330"]
        block = objects.get(owners[-1].upper()) if owners else None
        if block and ("2", "*Model_Space") in block["codes"]:
            codes = [(code, value.upper()) for code, value in obj["codes"] if code in ("331", "5")][1:]
            return {codes[i][1]: codes[i + 1][1] for i in range(0, len(codes) - 1, 2) if codes[i][0] == "331"}
    return None


@skipUnless(EXECUTABLE is not None, "ACadSharp runtime is required")
class CadSortEntsTableTests(SimpleTestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cbl-sortents-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def save_unedited(self):
        ops_path = self.tmp / "ops.json"
        ops_path.write_text(json.dumps({"ops": []}), encoding="utf-8")
        output = self.tmp / "saved.dwg"
        report = _run_writer([FIXTURE, output, "AC1018", ops_path])
        core_views._cbl_free_dwg_save_local_validate_v1(FIXTURE, output, None, [], report)
        return output

    def runtime_objects(self, dwg):
        dxf = self.tmp / (dwg.stem + ".dxf")
        run = subprocess.run([str(EXECUTABLE), "--dxf", str(dwg), str(dxf)], capture_output=True, timeout=300)
        self.assertEqual(run.returncode, 0, run.stderr.decode("utf-8", "replace")[-800:])
        return _objects(dxf.read_text(encoding="utf-8", errors="replace"))

    def test_sort_tables_are_read_under_their_own_keys(self):
        meta = core_views._cbl_free_dwg_acadsharp_metadata_v1(FIXTURE)
        messages = [n["Message"] for n in meta["notifications"]]
        self.assertFalse([m for m in messages if "ACAD_SORTENTS" in m], messages)
        entries = _dictionary(self.runtime_objects(FIXTURE), "ACAD_DGNLINESTYLECOMP")
        self.assertEqual(sorted(entries), DGN_KEYS)
        self.assertEqual({kind for kind, _ in entries.values()}, {"SORTENTSTABLE"})

    def test_unedited_save_keeps_draw_order_and_dgn_entries(self):
        saved = self.save_unedited()
        objects = self.runtime_objects(saved)
        self.assertEqual(sorted(_dictionary(objects, "ACAD_DGNLINESTYLECOMP")), DGN_KEYS)
        self.assertEqual(_model_space_draw_order(objects), DRAW_ORDER)
        if ODA:
            src, out = self.tmp / "oda-in", self.tmp / "oda-out"
            src.mkdir()
            out.mkdir()
            shutil.copy(saved, src / "saved.dwg")
            subprocess.run([ODA, str(src), str(out), "ACAD2018", "DXF", "0", "0", "*.DWG"], capture_output=True, timeout=300)
            oda = _objects((out / "saved.dxf").read_text(encoding="utf-8", errors="replace"))
            entries = _dictionary(oda, "ACAD_DGNLINESTYLECOMP")
            self.assertEqual(sorted(entries), DGN_KEYS)
            self.assertEqual({kind for kind, _ in entries.values()}, {"SORTENTSTABLE"})
            self.assertEqual(_model_space_draw_order(oda), DRAW_ORDER)
