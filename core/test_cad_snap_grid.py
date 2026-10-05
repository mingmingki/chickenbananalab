import json
import subprocess
from unittest import skipUnless

from django.test import SimpleTestCase

from .test_cad_dwg_save_integrity import NODE, _editor_function, _html

HARNESS = """
var frames = [];
var window = {cblGetCanonicalShapesV1: () => current};
function requestAnimationFrame(fn){ frames.push(fn); }
function flush(limit){ var n = 0; while(frames.length && n < (limit || 1e9)){ frames.shift()(); n++; } }
function grid(n, dx){ var out = []; for(var i = 0; i < n; i++) out.push({type:'line', x1:i * 10 + dx, y1:0, x2:i * 10 + dx + 5, y2:5}); return out; }
var current = [];
function getBB(s){ return {x:Math.min(s.x1, s.x2), y:Math.min(s.y1, s.y2), w:Math.abs(s.x2 - s.x1), h:Math.abs(s.y2 - s.y1)}; }
%(helpers)s
var out = {};
try {
  %(body)s
} catch(e) { out.error = String(e && e.stack || e); }
process.stdout.write(JSON.stringify(out));
"""

HELPERS = [
    "var cblSnapGridV5 = {",
    "function cblSnapCanonicalShapesV5(){",
    "function cblSnapGridInvalidateV5(reason){",
    "function cblSnapGridGetBBV5(s){",
    "function cblSnapGridAddPointsV5(cells, bb, shape, minX, minY, cell){",
    "function cblSnapGridBuildAsyncV5(reason){",
]


def _source(html, signature):
    start = html.index(signature)
    return html[start:html.index("\nfunction ", start + len(signature))]


@skipUnless(NODE, "node is required to execute the CAD editor helpers")
class CadSnapGridTests(SimpleTestCase):
    """The snap index of a large drawing is built in animation-frame batches.

    Opening another drawing (or reopening) while a build was still running
    started a second build on the same grid; the first one's next batch read
    the cleared cell map: "Cannot read properties of null (reading 'get')".
    """

    def run_grid(self, body):
        html = _html()
        helpers = "\n".join(_source(html, sig) for sig in HELPERS)
        run = subprocess.run([NODE, "-e", HARNESS % {"helpers": helpers, "body": body}],
                             capture_output=True, text=True, timeout=60)
        self.assertEqual(run.returncode, 0, run.stderr)
        return json.loads(run.stdout)

    def test_a_new_build_during_a_build_wins_without_errors(self):
        out = self.run_grid("""
            current = grid(1000, 0);
            cblSnapGridBuildAsyncV5('first');
            flush(12);
            cblSnapGridInvalidateV5('reopen');
            current = grid(300, 50000);
            cblSnapGridBuildAsyncV5('second');
            flush();
            var g = cblSnapGridV5;
            out.built = g.built; out.building = g.building; out.len = g.shapesLen;
            out.minX = g.minX; out.ref = g.shapesRef === current;
        """)
        self.assertNotIn("error", out)
        self.assertEqual(out, {"built": True, "building": False, "len": 300, "minX": 50000, "ref": True})

    def test_an_invalidated_build_does_not_finish(self):
        out = self.run_grid("""
            current = grid(1000, 0);
            cblSnapGridBuildAsyncV5('first');
            flush(5);
            cblSnapGridInvalidateV5('edit');
            flush();
            out.built = cblSnapGridV5.built; out.building = cblSnapGridV5.building;
        """)
        self.assertNotIn("error", out)
        self.assertEqual(out, {"built": False, "building": False})

    def test_a_build_alone_still_completes(self):
        out = self.run_grid("""
            current = grid(1000, 0);
            cblSnapGridBuildAsyncV5('only');
            flush();
            out.built = cblSnapGridV5.built; out.cells = cblSnapGridV5.cells.size > 0; out.len = cblSnapGridV5.shapesLen;
        """)
        self.assertEqual(out, {"built": True, "cells": True, "len": 1000})
