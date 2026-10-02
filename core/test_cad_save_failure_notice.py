import json
import subprocess
from unittest import skipUnless

from django.test import SimpleTestCase

from .test_cad_dwg_save_integrity import NODE, _build_ops_source, _html, _line

HARNESS = """
var alerts = [], hints = [];
var lifecycleDialog = %(dialog)s;
var document = {getElementById: function(id){ return id === 'cblFreeBrowserLifecycleDialogV1' && lifecycleDialog ? {} : null; }};
function alert(m){ alerts.push(String(m)); }
function setHint(m){ hints.push(String(m)); }
const window = {CBL_ACADSHARP_FULL_DXF_ACTIVE: true, CBL_CAD_TEXT_STYLES_V1: {}, layers: [], CBL_FREE_DWG_ORIGINAL_LAYER_NAMES: []};
%(helpers)s
window.CBL_FREE_DWG_ORIGINAL_SHAPES = %(base)s;
window.shapes = %(shapes)s;
%(body)s
"""


@skipUnless(NODE, "node is required to execute the CAD save helpers")
class CadUnsavedChangesTests(SimpleTestCase):
    """Unsaved changes are what a save would send, not a whole-model snapshot."""

    def run_js(self, body, base=None, shapes=None, dialog=False):
        script = HARNESS % {"helpers": _build_ops_source(_html()), "base": json.dumps(base if base is not None else [_line("A1")]),
                            "shapes": json.dumps(shapes if shapes is not None else [_line("A1")]),
                            "body": body, "dialog": json.dumps(dialog)}
        run = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=60)
        self.assertEqual(run.returncode, 0, run.stderr)
        return json.loads(run.stdout)

    def unsaved(self, **kw):
        return self.run_js("process.stdout.write(JSON.stringify(window.cblFreeDwgHasUnsavedChangesV1()));", **kw)

    def test_opened_drawing_without_edits_is_saved(self):
        self.assertIs(self.unsaved(), False)

    def test_moved_line_is_unsaved(self):
        self.assertIs(self.unsaved(shapes=[_line("A1", y=10)]), True)

    def test_added_line_is_unsaved(self):
        self.assertIs(self.unsaved(shapes=[_line("A1"), _line(None, y=50)]), True)

    def test_edit_the_save_would_refuse_still_counts_as_unsaved(self):
        # An unsupported shape makes buildOps throw; those edits are not saved.
        self.assertIs(self.unsaved(shapes=[_line("A1"), {"type": "spline", "layId": 1}]), True)

    def test_empty_new_drawing_has_nothing_to_save(self):
        self.assertIs(self.run_js("window.CBL_FREE_DWG_ORIGINAL_SHAPES = undefined; window.CBL_ACADSHARP_FULL_DXF_ACTIVE = false;"
                                  "process.stdout.write(JSON.stringify(window.cblFreeDwgHasUnsavedChangesV1()));",
                                  shapes=[]), False)

    def test_failure_notice_alerts_unless_the_lifecycle_dialog_shows_it(self):
        body = "notifySaveFailureV1('DWG 저장 실패: 테스트'); process.stdout.write(JSON.stringify({alerts: alerts, hints: hints}));"
        self.assertEqual(self.run_js(body), {"alerts": ["DWG 저장 실패: 테스트"], "hints": ["DWG 저장 실패: 테스트"]})
        self.assertEqual(self.run_js(body, dialog=True), {"alerts": [], "hints": ["DWG 저장 실패: 테스트"]})


class CadSaveFailureWiringTests(SimpleTestCase):
    def save_function(self):
        html = _html()
        start = html.index("window.cblFreeDwgSaveAC1018V1=async function(options){")
        return html[start:html.index("\n  };", start)]

    def test_every_failure_path_notifies(self):
        body = self.save_function()
        self.assertIn("notifySaveFailureV1('파일 저장은 완료됐지만 편집 상태 동기화 실패—파일을 다시 여십시오');return false;", body)
        self.assertIn("notifySaveFailureV1('원본 파일이 다른 프로그램에서 사용 중입니다. 파일을 닫은 후 저장을 다시 누르세요.');", body)
        catch = body[body.rindex("}catch(e){"):]
        self.assertLess(catch.index("if(e&&e.name==='AbortError')return false;"), catch.index("notifySaveFailureV1(message)"))

    def test_closing_the_page_with_unsaved_changes_warns(self):
        html = _html()
        script = html[html.index('<script id="CBL_FREE_DWG_AC1018_SAVE_V1_SCRIPT">'):]
        script = script[:script.index("</script>")]
        listener = script[script.index("window.addEventListener('beforeunload',"):]
        listener = listener[:listener.index("});") + 3]
        self.assertIn("freeMode()", listener)
        self.assertIn("window.cblFreeDwgHasUnsavedChangesV1()", listener)
        self.assertIn("ev.preventDefault()", listener)

    def test_lifecycle_dialog_uses_the_same_check(self):
        html = _html()
        dirty = html[html.index("  function dirty(){\n    if(typeof window.cblFreeDwgHasUnsavedChangesV1"):]
        self.assertIn("return !!window.cblFreeDwgHasUnsavedChangesV1();", dirty[:400])
