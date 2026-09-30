import json
import shutil
import subprocess
from pathlib import Path
from unittest import skipUnless

from django.conf import settings
from django.test import SimpleTestCase

CAD_HTML = Path(settings.BASE_DIR) / "core" / "static" / "core" / "tools" / "CBLCAD_VER2.html"
NODE = shutil.which("node")


def _helper_source():
    html = CAD_HTML.read_text(encoding="utf-8")
    start = html.index("  function cblDxfHasNoModelSpaceEntitiesV1(dxf){")
    end = html.index("\n  async function cblFreeDwgOpenFileObjectV1(", start)
    return html[start:end]


def _dxf(*entities, crlf=False):
    body = "  0\nSECTION\n  2\nHEADER\n  0\nENDSEC\n  0\nSECTION\n  2\nENTITIES\n"
    body += "".join(entities)
    body += "  0\nENDSEC\n  0\nEOF\n"
    return body.replace("\n", "\r\n") if crlf else body


LINE = "  0\nLINE\n  5\n49\n  8\n0\n 10\n0.0\n 20\n0.0\n 11\n100.0\n 21\n0.0\n"
PAPER_VIEWPORT = "  0\nVIEWPORT\n  5\n4A\n 67\n1\n  8\n0\n 10\n0.0\n 20\n0.0\n"


@skipUnless(NODE, "node is required to execute the CAD open helper")
class CadDwgOpenEmptyDrawingTests(SimpleTestCase):
    """An empty DWG is valid; only a DXF with no model-space entities may import as empty."""

    def check(self, **cases):
        script = _helper_source() + "\nconst cases = " + json.dumps(cases) + ";\n" + (
            "const out = {}; for (const [k, v] of Object.entries(cases)) out[k] = cblDxfHasNoModelSpaceEntitiesV1(v);\n"
            "process.stdout.write(JSON.stringify(out));"
        )
        run = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=60)
        self.assertEqual(run.returncode, 0, run.stderr)
        return json.loads(run.stdout)

    def test_empty_drawing_is_recognized(self):
        result = self.check(empty=_dxf(), empty_crlf=_dxf(crlf=True), paper_only=_dxf(PAPER_VIEWPORT))
        self.assertEqual(result, {"empty": True, "empty_crlf": True, "paper_only": True})

    def test_drawing_with_model_space_entities_is_not_empty(self):
        result = self.check(line=_dxf(LINE), line_and_viewport=_dxf(PAPER_VIEWPORT, LINE), line_crlf=_dxf(LINE, crlf=True))
        self.assertEqual(result, {"line": False, "line_and_viewport": False, "line_crlf": False})

    def test_dxf_without_entities_section_fails_closed(self):
        result = self.check(no_section="  0\nSECTION\n  2\nHEADER\n  0\nENDSEC\n  0\nEOF\n", garbage="not a dxf")
        self.assertEqual(result, {"no_section": False, "garbage": False})
