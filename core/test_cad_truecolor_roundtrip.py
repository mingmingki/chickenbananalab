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

try:
    import ezdxf
except ImportError:  # pragma: no cover - verification dependency only
    ezdxf = None

FIXTURE = Path(settings.BASE_DIR) / "core" / "test_fixtures" / "cad" / "truecolor_oda_ac1018.dwg"
# Made with ezdxf + ODA File Converter (not ACadSharp), so its colours do not
# depend on our runtime: layer TC = 0x8844CC; on TC, LINE 30 = 0x3B82F6,
# LINE 31 = 0xFF8000, LINE 32 = ByLayer.
FILE_COLOURS = {"30": 0x3B82F6, "31": 0xFF8000, "32": None}
ODA = find_oda()


@skipUnless(EXECUTABLE is not None and ezdxf is not None, "ACadSharp runtime and ezdxf are required")
class CadImportedTrueColourTests(SimpleTestCase):
    """An imported true colour reaches the editor as 0xRRGGBB and is saved back unchanged."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cbl-truecolor-rt-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def dxf_text(self, path):
        dxf = self.tmp / (path.stem + ".dxf")
        run = subprocess.run([str(EXECUTABLE), "--dxf", str(path), str(dxf)], capture_output=True, timeout=300)
        self.assertEqual(run.returncode, 0, run.stderr.decode("utf-8", "replace")[-800:])
        return dxf

    def runtime_colours(self, path):
        doc = ezdxf.readfile(str(self.dxf_text(path)))
        return ({e.dxf.handle: e.rgb and ezdxf.colors.rgb2int(e.rgb) for e in doc.modelspace()},
                ezdxf.colors.rgb2int(doc.layers.get("TC").rgb))

    def test_metadata_reports_true_colour_like_dxf_420(self):
        meta = core_views._cbl_free_dwg_acadsharp_metadata_v1(FIXTURE)
        self.assertEqual({e["handle"]: e["trueColor"] for e in meta["entities"]}, FILE_COLOURS)
        self.assertEqual({l["name"]: l["trueColor"] for l in meta["layers"]}["TC"], 0x8844CC)

    def test_open_manifest_gives_the_editor_the_file_colour(self):
        # The editor's display colour and cblRawTrueColor come from this manifest.
        meta = core_views._cbl_free_dwg_acadsharp_metadata_v1(FIXTURE)
        manifest = core_views._cbl_build_original_source_layer_manifest_v1(
            meta, self.dxf_text(FIXTURE).read_text(encoding="utf-8", errors="replace"))
        self.assertEqual({e["handle"]: e["trueColor"] for e in manifest["entities"]}, FILE_COLOURS)
        layer = next(l for l in manifest["layers"] if l["name"] == "TC")
        self.assertEqual(int(layer["trueColor"]), 0x8844CC)

    def save_moved_lines(self):
        # A moved imported line carries the colour it was opened with.
        opened = {e["handle"]: e["trueColor"] for e in core_views._cbl_free_dwg_acadsharp_metadata_v1(FIXTURE)["entities"]}
        ops = []
        for handle, y in (("30", 5), ("31", 25)):
            ops.append({"type": "update", "handle": handle, "entity": "LINE", "layer": "TC",
                        "start": [0, y, 0], "end": [100, y, 0], "aci": 256, "trueColor": opened[handle]})
        ops = core_views._cbl_normalize_free_dwg_ops_v1(core_views._cbl_free_dwg_save_local_json_v1(FIXTURE, None), ops)
        ops_path = self.tmp / "ops.json"
        ops_path.write_text(json.dumps({"ops": ops}), encoding="utf-8")
        output = self.tmp / "saved.dwg"
        report = _run_writer([FIXTURE, output, "AC1018", ops_path])
        core_views._cbl_free_dwg_save_local_validate_v1(FIXTURE, output, None, ops, report)
        return output

    def test_moved_imported_lines_keep_their_true_colour(self):
        output = self.save_moved_lines()
        self.assertEqual(self.runtime_colours(output), (FILE_COLOURS, 0x8844CC))
        meta = core_views._cbl_free_dwg_acadsharp_metadata_v1(output)
        self.assertEqual({e["handle"]: e["trueColor"] for e in meta["entities"]}, FILE_COLOURS)

    @skipUnless(ODA, "ODA File Converter is only used as an independent local check")
    def test_moved_imported_lines_keep_their_true_colour_in_oda(self):
        output = self.save_moved_lines()
        src, out = self.tmp / "oda-in", self.tmp / "oda-out"
        src.mkdir()
        out.mkdir()
        shutil.copy(output, src / "saved.dwg")
        subprocess.run([ODA, str(src), str(out), "ACAD2018", "DXF", "0", "1", "*.DWG"], capture_output=True, timeout=300)
        doc = ezdxf.readfile(str(out / "saved.dxf"))
        self.assertEqual({e.dxf.handle: e.rgb and ezdxf.colors.rgb2int(e.rgb) for e in doc.modelspace()}, FILE_COLOURS)
        self.assertEqual(ezdxf.colors.rgb2int(doc.layers.get("TC").rgb), 0x8844CC)
