"""The 3D review works like a model viewer: plan and elevations, section cuts and a section box,
a section drawing from the model, member filters, measuring and a snapshot."""
from pathlib import Path

from django.test import SimpleTestCase

CORE = Path(__file__).parent
JS = CORE.joinpath("static", "core", "js", "quantity_mass3d.js")
CSS = CORE.joinpath("static", "core", "css", "quantity_deck.css")
HOME = CORE.joinpath("templates", "core", "home.html")


class ViewerToolTests(SimpleTestCase):
    def setUp(self):
        self.js = JS.read_text()

    def test_plan_and_four_elevations_are_drawn_without_perspective(self):
        for name in ("iso", "top", "front", "back", "left", "right"):
            self.assertIn(f'data-view="{name}"', self.js)
        self.assertIn("new THREE.OrthographicCamera(", self.js)
        # the plan view looks north from the south so the plan's top stays on top
        self.assertIn("top: { theta: Math.PI / 2, phi: 0.0005, ortho: true }", self.js)
        self.assertIn("front: { theta: Math.PI / 2, phi: Math.PI / 2, ortho: true }", self.js)

    def test_sections_and_the_section_box_clip_every_material(self):
        self.assertIn("renderer.localClippingEnabled = true;", self.js)
        self.assertIn("mat.clippingPlanes = clipPlanes;", self.js)
        self.assertIn("useClip(group);", self.js)    # formwork built later
        self.assertIn("useClip(segment);", self.js)  # rebar built later
        self.assertIn("if (sbox.on) {", self.js)
        # a new cut keeps the far side, so its face looks at the camera
        self.assertIn("section[axis].flip = camera.position[axis] < section[axis].value;", self.js)

    def test_the_section_drawing_is_made_from_the_model(self):
        for piece in ("function cutRects(axis, value)", "pairs(hits, 0.08, 1.0)", "pairs(bh, 0.15, 1.3)",
                      "function slabSpans(slabPlan, y0, y1)", "function levelLines()", "' EL ' + l.elev.toFixed(2)",
                      "function sectionSvg(axis)", "function buildCaps()"):
            self.assertIn(piece, self.js)

    def test_a_horizontal_cut_shows_that_storeys_plan(self):
        self.assertIn("options.planSvg(at.building, at.level)", self.js)
        html = HOME.read_text()
        self.assertIn("window.cblQuantityMass3D.mount(host, model, { planSvg: planSvgFor, edits: floorEdits().slice() });", html)
        body = html[html.index("function planSvgFor(building, level) {"):][:1200]
        self.assertIn("v.sheet === level.sheet", body)    # the sheet the storey was built from first
        self.assertIn("(\\d+)~(\\d+)층", body)            # else a typical-floor plan covering it

    def test_members_measuring_and_snapshot(self):
        self.assertIn('[["wall", "벽"], ["col", "기둥"], ["beam", "보"], ["slab", "슬래브"]]', self.js)
        self.assertIn('data-kind="\' + k[0] + \'"', self.js)
        self.assertIn("m.visible = state.kinds[m.userData.kind] !== false;", self.js)
        self.assertIn("raycaster.setFromCamera(ndc, camera);", self.js)
        self.assertIn("clipPlanes.every(function (pl) { return pl.distanceToPoint(p) >= -0.01; })", self.js)
        self.assertIn('link.download = "3D_검토.png";', self.js)

    def test_the_overlay_is_styled_and_cache_keys_move(self):
        css = CSS.read_text()
        for selector in (".q3d-ui", ".q3d-bar", ".q3d-panel", ".q3d-sheet", ".q3d-status"):
            self.assertIn(selector, css)
        html = HOME.read_text()
        self.assertIn("quantity_mass3d.js' %}?v=20261111-rebar1", html)
        self.assertIn("quantity_deck.css' %}?v=20261111-rebar1", html)


class SiteLayoutTests(SimpleTestCase):
    def test_placed_buildings_share_the_parkings_coordinates_and_heights(self):
        js = JS.read_text()
        self.assertIn("group.position.set(-siteBounds[0], lift, siteBounds[3]);", js)
        self.assertIn("var offset = siteBounds ? siteBounds[2] - siteBounds[0] + 24 : 0;", js)  # the rest beside it
        self.assertIn("y0: y0 + lift, y1: y1 + lift", js)  # section drawings in site heights
        self.assertIn('b.kind === "parking" ? COLOR.parkingWall', js)

    def test_the_page_says_how_the_site_was_put_together(self):
        html = HOME.read_text()
        self.assertIn("'. ' + siteNote(summary) + '</p>'", html)
        self.assertIn("도면에서 벽이 겹치는 자리를 찾아 제자리에 놓았어요", html)
        self.assertIn('[["#94a3b8", "지하주차장", "parking"], ["#2dd4bf", "부대시설", "facility"]]', html)
