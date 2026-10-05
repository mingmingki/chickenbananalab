from django.test import SimpleTestCase

from .test_cad_dwg_save_integrity import _html


class CadOpenSpeedTests(SimpleTestCase):
    """Work removed from every DWG open (S-501-16: ~1.3 s of the browser side)."""

    def test_text_missing_diagnostics_scan_only_when_asked(self):
        html = _html()
        module = html[html.index("var VERSION = 'CBL_TEXT_MISSING_DIAG_V1';"):]
        module = module[:module.index("</script>")]
        wrapped = module[module.index("    var wrapped = function(dxf){"):]
        wrapped = wrapped[:wrapped.index("    };")]
        self.assertNotIn("scanRawDxf", wrapped)
        self.assertNotIn("setTimeout", wrapped)
        status = module[module.index("  window.cblTextMissingDiagV1Status = function(){"):]
        self.assertIn("scanRawDxf(lastDxf)", status[:status.index("};")])

    def test_style_resolution_builds_its_maps_once_per_manifest(self):
        html = _html()
        resolve = html[html.index(" function resolve(a){a=a||{};"):]
        resolve = resolve[:resolve.index("\n")]
        self.assertIn("maps=mapsOf(m)", resolve)
        self.assertNotIn("layersOf(m)", resolve)
        self.assertIn("var manifestMaps=typeof WeakMap==='function'?new WeakMap():null;", html)
