import json
import re
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

# KS C 5601 AC1018 drawing with Korean XRECORD and extended-data strings stored
# as code page bytes, as AutoCAD writes them (S-501 has such a NAS path).  Made
# from an ezdxf + ODA drawing by a throwaway ACadSharp program that set the
# strings; ODA reads them back (as \M+ MIF codes) unchanged.
FIXTURE = Path(settings.BASE_DIR) / "core" / "test_fixtures" / "cad" / "korean_xrecord_ac1018.dwg"
XRECORD = "\\\\Nas\\프로젝트\\경로 1"
XDATA = "한글 확장데이터 ABC"
ODA = find_oda()


def _mif(text):
    # \M+3XXXX is ODA's multibyte form: code page 3 (Korean), two cp949 bytes.
    return re.sub(r"\\M\+3([0-9A-Fa-f]{4})", lambda m: bytes.fromhex(m.group(1)).decode("cp949"), text)


@skipUnless(EXECUTABLE is not None, "ACadSharp runtime is required")
class CadKoreanXRecordTextTests(SimpleTestCase):
    """Korean XRECORD/extended-data strings are read with their code page and saved unchanged."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cbl-xrecord-text-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def runtime_dxf(self, dwg):
        dxf = self.tmp / (dwg.stem + ".dxf")
        run = subprocess.run([str(EXECUTABLE), "--dxf", str(dwg), str(dxf)], capture_output=True, timeout=300)
        self.assertEqual(run.returncode, 0, run.stderr.decode("utf-8", "replace")[-800:])
        return dxf.read_bytes().decode("cp949", "replace")

    def save_unedited(self):
        ops_path = self.tmp / "ops.json"
        ops_path.write_text(json.dumps({"ops": []}), encoding="utf-8")
        output = self.tmp / "saved.dwg"
        report = _run_writer([FIXTURE, output, "AC1018", ops_path])
        core_views._cbl_free_dwg_save_local_validate_v1(FIXTURE, output, None, [], report)
        return output

    def test_strings_are_read_with_their_code_page(self):
        dxf = self.runtime_dxf(FIXTURE)
        self.assertIn("\n" + XRECORD + "\n", dxf)
        self.assertIn("\n" + XDATA + "\n", dxf)

    def test_unedited_save_keeps_the_strings(self):
        dxf = self.runtime_dxf(self.save_unedited())
        self.assertIn("\n" + XRECORD + "\n", dxf)
        self.assertIn("\n" + XDATA + "\n", dxf)

    @skipUnless(ODA is not None and ezdxf is not None, "ODA File Converter is only used as an independent local check")
    def test_oda_reads_the_saved_strings(self):
        output = self.save_unedited()
        src, out = self.tmp / "oda-in", self.tmp / "oda-out"
        src.mkdir()
        out.mkdir()
        shutil.copy(output, src / "saved.dwg")
        subprocess.run([ODA, str(src), str(out), "ACAD2018", "DXF", "0", "0", "*.DWG"], capture_output=True, timeout=300)
        doc = ezdxf.readfile(str(out / "saved.dxf"))
        self.assertEqual(_mif(doc.rootdict["CBL_TEST"].tags[0].value), XRECORD)
        xdata = [e.get_xdata("CBL_APP") for e in doc.modelspace() if e.has_xdata("CBL_APP")]
        self.assertEqual([_mif(tags[0].value) for tags in xdata], [XDATA])
