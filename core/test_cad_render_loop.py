import json
import subprocess
from unittest import skipUnless

from django.test import SimpleTestCase

from .test_cad_dwg_save_integrity import NODE, _html

HARNESS = """
var frames = [], renders = 0, layers = [];
function requestAnimationFrame(fn){ frames.push(fn); return frames.length; }
function flush(){ var now = frames; frames = []; now.forEach(function(fn){ fn(16); }); }
function render(){ renders++; if (typeof onRender === 'function') onRender(); }
var onRender = null;
%(module)s
var out = {};
%(body)s
process.stdout.write(JSON.stringify(out));
"""


@skipUnless(NODE, "node is required to execute the CAD editor helpers")
class CadRenderLoopTests(SimpleTestCase):
    """The canvas asks for a frame only when something has to be drawn.

    The loop asked for every frame (60 a second) while the drawing sat still,
    which kept the page, the GPU process and the browser awake: ~3% of a core
    for an open drawing doing nothing.
    """

    def run_module(self, body):
        html = _html()
        module = html[html.index("// CBL_SPEED_CORE_SAFE_V1_START"):html.index("// CBL_SPEED_CORE_SAFE_V1_END")]
        run = subprocess.run([NODE, "-e", HARNESS % {"module": module, "body": body}],
                             capture_output=True, text=True, timeout=60)
        self.assertEqual(run.returncode, 0, run.stderr)
        return json.loads(run.stdout)

    def test_nothing_to_draw_asks_for_no_frames(self):
        out = self.run_module("flush(); flush(); out.waiting = frames.length; out.renders = renders;")
        self.assertEqual(out, {"waiting": 0, "renders": 0})

    def test_requests_in_one_frame_draw_once(self):
        out = self.run_module("""
            requestCadRenderV1(); requestCadRenderV1('zoom'); requestCadRenderV1();
            out.asked = frames.length; flush(); out.renders = renders; out.after = frames.length;
            flush(); out.renders2 = renders;""")
        self.assertEqual(out, {"asked": 1, "renders": 1, "after": 0, "renders2": 1})

    def test_a_request_while_drawing_draws_the_next_frame(self):
        out = self.run_module("""
            var again = 1; onRender = function(){ if (again-- > 0) requestCadRenderV1(); };
            requestCadRenderV1(); flush(); out.first = renders; out.asked = frames.length;
            flush(); out.second = renders; out.after = frames.length;""")
        self.assertEqual(out, {"first": 1, "asked": 1, "second": 2, "after": 0})

    def test_a_failing_draw_does_not_stop_later_frames(self):
        out = self.run_module("""
            var fail = true; onRender = function(){ if (fail) { fail = false; throw new Error('boom'); } };
            console.error = function(){};
            requestCadRenderV1(); flush(); requestCadRenderV1(); flush(); out.renders = renders;""")
        self.assertEqual(out, {"renders": 2})
