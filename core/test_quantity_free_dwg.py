import io
import subprocess
import zipfile
from pathlib import Path
from unittest import skipUnless
from unittest.mock import patch

from django.conf import settings
from django.test import SimpleTestCase

from . import quantity_views
from .test_cad_dwg_text_validation import EXECUTABLE

try:
    import ezdxf
except ImportError:  # pragma: no cover - verification dependency only
    ezdxf = None

# An ezdxf drawing written as AC1018 (KS C 5601) by a throwaway ACadSharp
# program: SLAB 10000 x 5000 outline with a 1000 x 1000 opening, a 10000 WALL
# line, two C1 column blocks, TEXT "기초 F1", MTEXT "슬래브 두께 200" and a
# linear dimension of 10000.
FIXTURE = Path(settings.BASE_DIR) / "core" / "test_fixtures" / "cad" / "quantity_plan_ac1018.dwg"

_REAL_POPEN = subprocess.Popen


def _popen_without_oda(args, *rest, **kwargs):
    if "odafileconverter" in " ".join(str(a) for a in (args if isinstance(args, (list, tuple)) else [args])).lower():
        raise AssertionError("ODA File Converter was started")
    return _REAL_POPEN(args, *rest, **kwargs)


def _zip(members):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return buffer.getvalue()


@skipUnless(EXECUTABLE is not None and ezdxf is not None, "ACadSharp runtime and ezdxf are required")
class QuantityFreeDwgTests(SimpleTestCase):
    """The quantity tool reads DWG with ChickenBananaCAD's free converter, never ODA."""

    def parse(self, members, **kwargs):
        with patch.object(subprocess, "Popen", side_effect=_popen_without_oda):
            return quantity_views.parse_dwg_from_zip(_zip(members), **kwargs)

    def test_dwg_quantities_are_read(self):
        result = self.parse({"구조/S-101 기초평면도.dwg": FIXTURE.read_bytes()})["구조/S-101 기초평면도.dwg"]
        self.assertNotIn("error", result)
        self.assertEqual(result["layer_geometry"]["SLAB"]["net_closed_area"], 49000000.0)
        self.assertEqual(result["layer_geometry"]["SLAB"]["opening_count"], 1)
        self.assertEqual(result["layer_geometry"]["SLAB"]["total_length"], 34000.0)
        self.assertEqual(result["layer_geometry"]["WALL"]["total_length"], 10000.0)
        self.assertEqual(result["block_counts"], {"C1": 2})
        # Korean text as the CAD editor shows it, not \U+XXXX or code page bytes.
        self.assertEqual(result["texts"], ["기초 F1", "슬래브 두께 200"])
        # The converter's DXF has no measurement (code 42): measured from the geometry.
        self.assertEqual(result["dimensions"], [10000.0])
        self.assertNotIn("unreadable_objects", result)

    def test_dxf_is_still_read_directly(self):
        doc = ezdxf.new("R2010")
        doc.modelspace().add_line((0, 0), (1000, 0), dxfattribs={"layer": "WALL"})
        text = io.StringIO()
        doc.write(text)
        result = self.parse({"S-102.dxf": text.getvalue().encode("utf-8")})["S-102.dxf"]
        self.assertEqual(result["layer_geometry"]["WALL"]["total_length"], 1000.0)

    def test_unreadable_dwg_asks_for_dxf(self):
        result = self.parse({"broken.dwg": b"AC1018 not a drawing"})["broken.dwg"]
        self.assertIn("DWG를 읽지 못했습니다", result["error"])
        self.assertIn("DXF로 내보내", result["error"])
        self.assertNotIn("ODA", result["error"])

    def test_time_budget_skips_the_remaining_dwg(self):
        # Like the 90 s ODA batch before, conversion has a per-ZIP budget; DXF
        # files are parsed regardless.
        doc = ezdxf.new("R2010")
        doc.modelspace().add_line((0, 0), (1000, 0))
        text = io.StringIO()
        doc.write(text)
        result = self.parse({"a.dwg": FIXTURE.read_bytes(), "b.dxf": text.getvalue().encode("utf-8")}, time_budget=0)
        self.assertIn("시간", result["a.dwg"]["error"])
        self.assertNotIn("error", result["b.dxf"])


@skipUnless(ezdxf is not None, "ezdxf is required")
class QuantityMifTextTests(SimpleTestCase):
    def test_mif_codes_are_read_as_the_editor_shows_them(self):
        # Pre-2007 drawings that went through ODA hold Korean as \\M+3XXXX;
        # the editor decodes them (decodeMplus), so does the quantity tool.
        doc = ezdxf.new("R2010")
        doc.modelspace().add_text("\\M+3B1E2\\M+3C3CA F1", dxfattribs={"layer": "TEXT"})
        stream = io.StringIO()
        doc.write(stream)
        with patch.object(quantity_views, "_cbl_free_dwg_to_dxf_text_v1", return_value=(stream.getvalue(), {"notifications": []})):
            result = quantity_views.parse_dwg_from_zip(_zip({"a.dwg": b"AC1018"}))
        self.assertEqual(result["a.dwg"]["texts"], ["기초 F1"])


class QuantityDimensionMeasurementTests(SimpleTestCase):
    @skipUnless(ezdxf is not None, "ezdxf is required")
    def test_stored_measurement_wins_and_missing_one_is_measured(self):
        doc = ezdxf.new("R2010", setup=True)
        msp = doc.modelspace()
        stored = msp.add_linear_dim(base=(0, 100), p1=(0, 0), p2=(3000, 0))
        stored.render()
        stored.dimension.dxf.actual_measurement = 2999.5
        measured = msp.add_linear_dim(base=(0, 300), p1=(0, 200), p2=(4000, 200))
        measured.render()
        if measured.dimension.dxf.hasattr("actual_measurement"):
            measured.dimension.dxf.discard("actual_measurement")
        self.assertEqual(quantity_views._extract_dxf_quantities(doc)["dimensions"], [2999.5, 4000.0])
