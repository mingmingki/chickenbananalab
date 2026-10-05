import base64
import collections
import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from unittest import skipUnless
from unittest.mock import patch

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, SimpleTestCase

from . import views as core_views
from .test_cad_dwg_save_integrity import _html
from .test_cad_dwg_text_validation import EXECUTABLE, _run_writer

FIXTURES = Path(settings.BASE_DIR) / "core" / "test_fixtures" / "cad"
# ezdxf drawings: layer "슬래브" (two closed polylines), a WALL line, block "기둥"
# inserted twice, TEXT "기초 F1", MTEXT "슬래브 두께 200" and a linear dimension.
# One is R2004 in KS C 5601 bytes ($DWGCODEPAGE ANSI_949), one R2018 UTF-8.
DXF_949 = FIXTURES / "plan_ansi949_r2004.dxf"
DXF_UTF8 = FIXTURES / "plan_utf8_r2018.dxf"


def _editor_dxf(codepage_lines):
    """What the editor's DXF export wrote until 2026-10-04: only a LAYER table,
    UTF-8 bytes, and (buildLayeredDXF) $DWGCODEPAGE ANSI_949."""
    return "\n".join([
        "0", "SECTION", "2", "HEADER", "9", "$ACADVER", "1", "AC1015", *codepage_lines,
        "9", "$INSUNITS", "70", "4", "0", "ENDSEC",
        "0", "SECTION", "2", "TABLES", "0", "TABLE", "2", "LAYER", "70", "1",
        "0", "LAYER", "2", "기본", "70", "0", "62", "5", "6", "CONTINUOUS", "0", "ENDTAB", "0", "ENDSEC",
        "0", "SECTION", "2", "ENTITIES",
        "0", "LINE", "8", "기본", "10", "0", "20", "0", "30", "0", "11", "500", "21", "0", "31", "0",
        "0", "TEXT", "8", "기본", "10", "0", "20", "100", "30", "0", "40", "14", "1", "기초 F1", "50", "0",
        "0", "ENDSEC", "0", "EOF", ""]).encode("utf-8")


def _model_counts(meta):
    return collections.Counter(e["type"] for e in meta["entities"] if e.get("space") == "modelspace")


def _texts(meta):
    return sorted(e.get("text") for e in meta["entities"] if e.get("space") == "modelspace" and "text" in e)


@skipUnless(EXECUTABLE is not None, "ACadSharp runtime is required")
class CadDxfToDwgRuntimeTests(SimpleTestCase):
    """A DXF opened in the editor first becomes an AC1018 DWG (blocks and dimensions kept)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cbl-dxf-open-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def convert(self, dxf):
        out = self.tmp / (dxf.stem + ".dwg")
        run = subprocess.run([str(EXECUTABLE), "--dwg-from-dxf", str(dxf), str(out)], capture_output=True, timeout=300)
        self.assertEqual(run.returncode, 0, run.stderr.decode("utf-8", "replace")[-800:])
        report = json.loads(run.stdout.decode("utf-8", "replace"), strict=False)
        self.assertEqual(report["status"], "dwg_written")
        self.assertEqual(out.read_bytes()[:6], b"AC1018")
        return report, core_views._cbl_free_dwg_acadsharp_metadata_v1(out)

    def check_plan(self, report, meta):
        self.assertEqual(report["dropped"], {})
        counts = _model_counts(meta)
        self.assertEqual(counts["LWPOLYLINE"], 2)
        self.assertEqual(counts["LINE"], 1)
        self.assertEqual(counts["INSERT"], 2)
        self.assertEqual(counts["TEXTENTITY"], 1)
        self.assertEqual(counts["MTEXT"], 1)
        self.assertEqual(sum(n for t, n in counts.items() if t.startswith("DIMENSION")), 1)
        inserts = [e for e in meta["entities"] if e.get("space") == "modelspace" and e["type"] == "INSERT"]
        self.assertEqual({e["block"]["name"] for e in inserts}, {"기둥"})
        self.assertIn("슬래브", [layer["name"] for layer in meta["layers"]])
        self.assertIn("기초 F1", _texts(meta))
        self.assertTrue(any("슬래브 두께 200" in t for t in _texts(meta)), _texts(meta))

    def test_korean_code_page_dxf(self):
        report, meta = self.convert(DXF_949)
        self.check_plan(report, meta)
        self.assertFalse(report["readAsUtf8"])
        self.assertEqual(meta["codePage"].lower(), "kcs5601")

    def test_utf8_dxf(self):
        report, meta = self.convert(DXF_UTF8)
        self.check_plan(report, meta)

    def test_converted_dwg_saves_and_validates(self):
        report, meta = self.convert(DXF_949)
        source = self.tmp / (DXF_949.stem + ".dwg")
        line = next(e for e in meta["entities"] if e.get("space") == "modelspace" and e["type"] == "LINE")
        for ops in ([], [{"type": "move", "handle": line["handle"], "delta": [0, 100, 0]}]):
            ops_path = self.tmp / "ops.json"
            ops_path.write_text(json.dumps({"ops": ops}), encoding="utf-8")
            output = self.tmp / "saved.dwg"
            writer_report = _run_writer([source, output, "AC1018", ops_path])
            core_views._cbl_free_dwg_save_local_validate_v1(source, output, None, ops, writer_report)

    def editor_dxf(self, codepage_lines):
        path = self.tmp / "editor.dxf"
        path.write_bytes(_editor_dxf(codepage_lines))
        return path

    def test_editor_export_declaring_ansi_949_is_read_as_utf8(self):
        report, meta = self.convert(self.editor_dxf(["9", "$DWGCODEPAGE", "3", "ANSI_949"]))
        self.assertTrue(report["readAsUtf8"])
        self.assertEqual(report["dropped"], {})
        self.assertEqual(meta["codePage"].lower(), "kcs5601")
        self.assertEqual(_texts(meta), ["기초 F1"])
        self.assertIn("기본", [layer["name"] for layer in meta["layers"]])
        self.assertEqual(_model_counts(meta), {"LINE": 1, "TEXTENTITY": 1})

    def test_editor_export_without_code_page_gets_the_korean_one(self):
        report, meta = self.convert(self.editor_dxf([]))
        self.assertFalse(report["readAsUtf8"])
        self.assertEqual(meta["codePage"].lower(), "kcs5601")
        self.assertEqual(_texts(meta), ["기초 F1"])

    def test_header_naming_missing_entries_falls_back_to_the_defaults(self):
        # ezdxf.new() writes $DIMSTYLE "ISO-25" without that style, and other
        # programs leave header names of removed layers/styles; the DWG writer
        # looked them up and every such DXF was refused.
        import ezdxf

        cases = {"ezdxf default": None, "$CLAYER": "없는레이어", "$CELTYPE": "NOLT", "$TEXTSTYLE": "NOSTYLE",
                 "$CMLSTYLE": "NOML", "$DIMTXSTY": "NOSTYLE"}
        for variable, value in cases.items():
            with self.subTest(variable):
                doc = ezdxf.new("R2010")
                doc.modelspace().add_line((0, 0), (1000, 0))
                doc.modelspace().add_text("기초 F1", height=200)
                if value:
                    doc.header[variable] = value
                path = self.tmp / "header.dxf"
                doc.saveas(path)
                report, meta = self.convert(path)
                self.assertEqual(report["dropped"], {})
                self.assertEqual(_model_counts(meta), {"LINE": 1, "TEXTENTITY": 1})
                self.assertEqual(_texts(meta), ["기초 F1"])
                fixed = [n["Message"] for n in report["notifications"] if "missing entry" in (n.get("Message") or "")]
                self.assertTrue(any("$DIMSTYLE" in m and "'ISO-25'" in m for m in fixed), fixed)
                if value:
                    self.assertTrue(any(variable in m and f"'{value}'" in m for m in fixed), fixed)
                source = self.tmp / "header.dwg"
                ops_path = self.tmp / "ops.json"
                ops_path.write_text('{"ops": []}', encoding="utf-8")
                writer_report = _run_writer([source, self.tmp / "saved.dwg", "AC1018", ops_path])
                core_views._cbl_free_dwg_save_local_validate_v1(source, self.tmp / "saved.dwg", None, [], writer_report)

    def test_not_a_dxf_is_refused(self):
        bad = self.tmp / "bad.dxf"
        bad.write_bytes(b"not a drawing")
        run = subprocess.run([str(EXECUTABLE), "--dwg-from-dxf", str(bad), str(self.tmp / "bad.dwg")], capture_output=True, timeout=120)
        self.assertNotEqual(run.returncode, 0)
        self.assertFalse((self.tmp / "bad.dwg").exists())


@skipUnless(EXECUTABLE is not None, "ACadSharp runtime is required")
class CadDxfOpenApiTests(SimpleTestCase):
    def open(self, path, name=None):
        request = RequestFactory().post(
            "/api/cblcad/free-dwg-to-dxf/?format=acadsharp-dxf&mode=free-dwg",
            {"file": SimpleUploadedFile(name or path.name, path.read_bytes(), content_type="application/octet-stream")})
        with patch.object(core_views, "_cbl_is_free_dwg_request", return_value=True):
            response = core_views.cblcad_free_dwg_local_api(request)
        self.assertEqual(response.status_code, 200, response.content[:400])
        return json.loads(response.content)

    def test_dxf_upload_is_opened_as_a_new_dwg(self):
        result = self.open(DXF_949)
        self.assertTrue(result["converted_from_dxf"])
        self.assertEqual(result["converted_dwg_name"], "plan_ansi949_r2004.dwg")
        self.assertEqual(base64.b64decode(result["converted_dwg_base64"])[:6], b"AC1018")
        self.assertIn("기초 F1", result["dxf"])
        self.assertEqual(result["dxf_dropped_objects"], {})

    def test_dxf_is_recognised_by_content(self):
        # A DXF picked under another name is still read as DXF.
        self.assertTrue(self.open(DXF_UTF8, name="drawing.dwg")["converted_from_dxf"])

    def test_unreadable_dxf_says_so(self):
        request = RequestFactory().post(
            "/api/cblcad/free-dwg-to-dxf/?format=acadsharp-dxf&mode=free-dwg",
            {"file": SimpleUploadedFile("broken.dxf", b"not a drawing", content_type="application/dxf")})
        with patch.object(core_views, "_cbl_is_free_dwg_request", return_value=True):
            response = core_views.cblcad_free_dwg_local_api(request)
        self.assertEqual(response.status_code, 500)
        self.assertIn("이 DXF 파일을 무료 변환기로 읽지 못했습니다", json.loads(response.content)["error"])

    def test_dwg_upload_is_unchanged(self):
        result = self.open(FIXTURES / "arc_bulge_ac1018.dwg")
        self.assertFalse(result["converted_from_dxf"])
        self.assertNotIn("converted_dwg_base64", result)


def _body(html, start, end):
    body = html[html.index(start):]
    return body[:body.index(end)]


class CadDxfOpenEditorTests(SimpleTestCase):
    """Opening a DXF (or JSON, or a new tab) never leaves the save pointed at another file.

    The bottom "DXF 열기" added the DXF's shapes to the open drawing and kept
    that drawing's DWG and file handle, so the next save wrote them into the
    DWG (and failed with "block not found" when the DXF had blocks).
    """

    def open_body(self):
        return _body(_html(), "async function cblFreeDwgOpenFileObjectV1(file,handle){",
                     "function cblNativeFilePickerSupportedV1(){")

    def test_save_target_changes_only_after_the_server_read_the_file(self):
        body = self.open_body()
        read = body.index("if (!response.ok || !result.ok) throw")
        for assignment in ("window.CBLCAD_V29_LAST_DWG_FILE = sourceFile;", "window.CBL_FREE_DWG_CURRENT_FILENAME = openedName;",
                           "window.CBL_FREE_DWG_NATIVE_FILE_HANDLE = handle || null;",
                           "window.CBL_NATIVE_DWG_CURRENT_HANDLE_V1 = handle || null;"):
            self.assertEqual(body.count(assignment), 1, assignment)
            self.assertGreater(body.index(assignment), read, assignment)
        self.assertEqual(body.count("window.CBLCAD_V29_LAST_DWG_FILE ="), 1)
        picker = _body(_html(), "async function cblOpenDwgWithNativePickerImplV1(){", "window.cblOpenDwgWithNativePickerV1=function")
        self.assertNotIn("CBL_FREE_DWG_NATIVE_FILE_HANDLE=handle", picker)
        self.assertNotIn("CBL_NATIVE_DWG_CURRENT_HANDLE_V1=handle", picker)

    def test_a_dxf_becomes_the_converted_dwg_without_a_file_handle(self):
        body = self.open_body()
        converted = _body(body, "if (result.converted_from_dxf) {", "\n    }\n")
        self.assertIn("result.converted_dwg_base64", converted)
        self.assertIn("sourceFile = new File([convertedBytes], openedName", converted)
        self.assertIn("handle = null;", converted)
        self.assertIn("window.CBL_FREE_DWG_LOCAL_FILE_TOKEN = null;", converted)
        self.assertIn("cblDxfDroppedObjectsMessageV1(result.dxf_dropped_objects)", converted)
        self.assertLess(body.index("if (result.converted_from_dxf) {"), body.index("window.CBLCAD_V29_LAST_DWG_FILE = sourceFile;"))

    @skipUnless(shutil.which("node"), "node is required to execute the CAD editor helpers")
    def test_dropped_objects_message(self):
        html = _html()
        start = html.index("  function cblObjectCountsTextV1(counts){")
        end = html.index("\n  }\n", html.index("  function cblDxfDroppedObjectsMessageV1(counts){")) + 4
        script = html[start:end] + """
process.stdout.write(JSON.stringify([cblDxfDroppedObjectsMessageV1({MULTILEADER: 2, ENTITY: 1}), cblDxfDroppedObjectsMessageV1({})]));"""
        run = subprocess.run([shutil.which("node"), "-e", script], capture_output=True, text=True, timeout=60)
        self.assertEqual(run.returncode, 0, run.stderr)
        message, empty = json.loads(run.stdout)
        self.assertIn("MULTILEADER 2개, ENTITY 1개", message)
        self.assertIn("저장할 수 없습니다", message)
        self.assertEqual(empty, "")

    def test_save_is_refused_when_dxf_objects_did_not_reach_the_dwg(self):
        save = _body(_html(), "window.cblFreeDwgSaveAC1018V1=async function(options){", "var requested=name(options);")
        self.assertIn("if(window.CBL_FREE_DWG_SAVE_BLOCKED_V1){notifySaveFailureV1(", save)

    def test_dxf_open_buttons_go_through_the_free_open(self):
        html = _html()
        bottom = _body(html, " window.cblBottomOpenDXF = function(){", "\n };\n")
        free = bottom[:bottom.index("return;")]
        self.assertIn("window.cblFreeDwgOpenFileObjectV1(file, null)", free)
        self.assertNotIn("applyImportedShapes", free)
        dialog = _body(html, "function fileOpenDXF(){", "var inp=")
        self.assertIn("return window.cblFreeDwgLocalOpenV1();", dialog)

    def test_json_open_detaches_from_the_previous_dwg(self):
        html = _html()
        self.assertIn("window.cblFreeDwgDetachSourceV1=function(name,options){", html)
        restore = _body(html, " function restoreJSON(data){", "\n }\n")
        self.assertIn("window.cblFreeDwgDetachSourceV1(data.name || data.fileName || 'drawing.wcd', {unsaved: true})", restore)
        detach = _body(html, "window.cblFreeDwgDetachSourceV1=function(name,options){", "\n  };\n")
        for cleared in ("window.CBLCAD_V29_LAST_DWG_FILE=null", "window.CBL_FREE_DWG_NATIVE_FILE_HANDLE=null",
                        "window.CBL_NATIVE_DWG_CURRENT_HANDLE_V1=null", "window.CBL_FREE_DWG_LOCAL_FILE_TOKEN=null",
                        "window.CBL_FREE_DWG_ORIGINAL_SHAPES=[]"):
            self.assertIn(cleared, detach)

    def test_document_tabs_keep_each_drawing_source(self):
        tabs = _body(_html(), '<script id="CBL_DOC_TABS_FROM_VER3_SAFE_V1">', "</script>")
        capture = _body(tabs, "  function capture(){", "  function blankState(name){")
        restore = _body(tabs, "  function restore(st){", "  function activeTab(){")
        self.assertIn("st.freeDwg = window.cblFreeDwgDocumentStateV1()", capture)
        self.assertIn("window.cblFreeDwgRestoreDocumentStateV1(st.freeDwg || null, st.curFile)", restore)
        keys = _body(_html(), "var CBL_FREE_DWG_DOCUMENT_KEYS_V1=[", "];")
        for key in ("CBLCAD_V29_LAST_DWG_FILE", "CBL_FREE_DWG_NATIVE_FILE_HANDLE", "CBL_NATIVE_DWG_CURRENT_HANDLE_V1",
                    "CBL_FREE_DWG_LOCAL_FILE_TOKEN", "CBL_FREE_DWG_ORIGINAL_SHAPES", "CBL_FREE_DWG_SAVED_REVISION_V1"):
            self.assertIn("'" + key + "'", keys)

    def test_dxf_save_writes_korean_as_unicode_escapes(self):
        # The header says ANSI_949, but the Blob is UTF-8: AutoCAD read garbage.
        html = _html()
        build = _body(html, "  function buildDXF(){applyAllLayerStyles();", "\n")
        self.assertIn("return asciiDxf(out.join('\\r\\n')+'\\r\\n');", build)
        helper = _body(html, "  function asciiDxf(text){", "\n")
        self.assertIn("'\\\\U+'", helper)

