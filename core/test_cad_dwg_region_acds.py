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

FIXTURES = Path(settings.BASE_DIR) / "core" / "test_fixtures" / "cad"
# A REGION (2000x2000 square at 3000,0) and a LINE: ezdxf R2018 DXF converted
# to DWG AC1032 by ODA, which keeps the ACIS data ("ASM BinaryFile4", 1809
# bytes) in the AcDs data section, as AutoCAD 2013+ and ZWCAD do.
REGION_ACDS = FIXTURES / "region_acds_ac1032.dwg"
SQUARE = [(3000.0, 0.0, 0.0), (3000.0, 2000.0, 0.0), (5000.0, 0.0, 0.0), (5000.0, 2000.0, 0.0)]


@skipUnless(EXECUTABLE is not None, "ACadSharp runtime is required")
class CadDwgRegionAcdsTests(SimpleTestCase):
    """REGIONs of R2013+ drawings save with their ACIS data.

    The data sits in the AcDs section, which ACadSharp read but never gave to
    the entities, so every save of such a drawing (사무동 1-5층) was refused
    with "has no ACIS payload".
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cbl-region-acds-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def save(self, ops=()):
        ops_path = self.tmp / "ops.json"
        ops_path.write_text(json.dumps({"ops": list(ops)}), encoding="utf-8")
        output = self.tmp / "saved.dwg"
        report = _run_writer([REGION_ACDS, output, "AC1018", ops_path])
        return output, report

    def test_region_is_written_with_its_stored_acis_data(self):
        output, report = self.save()
        self.assertEqual(report["storedAcis"], 1)
        self.assertEqual(report["source"]["Counts"].get("Region"), 1)
        self.assertEqual(report["reread"]["Counts"].get("Region"), 1)
        region = report["reread"]["Regions"][0]
        self.assertEqual((region["handle"], region["dataBytes"]), ("2F", 1809))
        core_views._cbl_free_dwg_save_local_validate_v1(REGION_ACDS, output, None, [], report)

    def test_an_edit_next_to_the_region_saves(self):
        meta = core_views._cbl_free_dwg_acadsharp_metadata_v1(REGION_ACDS)
        line = next(e for e in meta["entities"] if e.get("space") == "modelspace" and e["type"] == "LINE")
        ops = [{"type": "move", "handle": line["handle"], "delta": [0, -100, 0]}]
        output, report = self.save(ops)
        core_views._cbl_free_dwg_save_local_validate_v1(REGION_ACDS, output, None, ops, report)
        self.assertEqual(report["reread"]["Counts"].get("Region"), 1)

    @skipUnless(core_views._cbl_free_dwg_local_find_dwgread_v1(), "LibreDWG dwgread is only on development machines")
    def test_libredwg_validation_accepts_the_stored_payload(self):
        # LibreDWG reads the AcDs payload of the original as empty.
        output, report = self.save()
        core_views._cbl_free_dwg_save_local_validate_v1(
            REGION_ACDS, output, core_views._cbl_free_dwg_local_find_dwgread_v1(), [], report)

    def test_a_changed_region_payload_is_refused(self):
        output, report = self.save()
        report["reread"]["Regions"][0]["dataSha256"] = "0" * 64
        with self.assertRaisesRegex(RuntimeError, "REGION ACIS"):
            core_views._cbl_free_dwg_save_local_validate_v1(REGION_ACDS, output, None, [], report)

    @skipUnless(find_oda(), "ODA File Converter is only used for local review")
    def test_another_reader_sees_the_same_region(self):
        import ezdxf
        from ezdxf.acis import api as acis

        output, _ = self.save()
        source, target = self.tmp / "oda-in", self.tmp / "oda-out"
        source.mkdir()
        target.mkdir()
        shutil.copy(output, source / "saved.dwg")
        subprocess.run([find_oda(), str(source), str(target), "ACAD2018", "DXF", "0", "1"], capture_output=True, timeout=300)
        regions = list(ezdxf.readfile(target / "saved.dxf").modelspace().query("REGION"))
        self.assertEqual(len(regions), 1)
        data = bytes(regions[0].sab) if regions[0].has_binary_data else regions[0].sat
        vertices = sorted({tuple(round(c, 3) for c in v)
                           for body in acis.load(data) for mesh in acis.mesh_from_body(body) for v in mesh.vertices})
        self.assertEqual(vertices, SQUARE)
