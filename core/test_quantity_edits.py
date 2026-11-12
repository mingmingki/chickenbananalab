"""People fix what the reading missed: members added or taken out on a plan, kept with the floor
job, drawn in 3D and counted in the storeys that use the plan."""
import json
import tempfile
from pathlib import Path

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings

from core import quantity_edits as qe
from core import quantity_floor_takeoff as ft
from core import quantity_floor_views as fv
from core import quantity_site as site
from core import quantity_views as views
from core.test_quantity_floor_takeoff import FloorRuleTests

CORE = Path(__file__).parent
HOME = CORE / "templates" / "core" / "home.html"
EDIT_JS = CORE / "static" / "core" / "js" / "quantity_plan_edit.js"
MASS_JS = CORE / "static" / "core" / "js" / "quantity_mass3d.js"


def with_ids(model):
    for i, plan in enumerate(model["buildings"][0]["plans"]):
        plan["id"] = f"p{i}"
    return model


class CleanTests(SimpleTestCase):
    def test_only_valid_fixes_are_kept_and_sizes_are_bounded(self):
        edits = qe.clean([
            {"plan": "p1", "op": "add", "kind": "beam", "a": [0, 0], "b": [6, 0], "w": 0.4, "d": 0.7, "layer": "S-BEAM", "x": 1},
            {"plan": "p1", "op": "add", "kind": "wall", "a": [0, 0], "b": [0, 0]},           # no length
            {"plan": "p1", "op": "paint", "kind": "beam", "a": [0, 0], "b": [1, 0]},         # unknown op
            {"plan": "", "op": "add", "kind": "column", "c": [1, 1]},                        # no plan
            {"plan": "p2", "op": "add", "kind": "column", "c": [1, 1], "w": 99, "h": "x"},
            "nonsense",
        ])
        self.assertEqual(len(edits), 2)
        beam, column = edits
        self.assertEqual((beam["w"], beam["d"], beam["layer"]), (0.4, 0.7, "S-BEAM"))
        self.assertNotIn("x", beam)
        self.assertEqual((column["w"], column["h"]), (3.0, 0.5))  # bounded, defaulted

    def test_each_fix_counts_like_the_reading_does(self):
        beam = qe.clean([{"plan": "p", "op": "add", "kind": "beam", "a": [0, 0], "b": [5, 0], "w": 0.4, "d": 0.6}])[0]
        concrete, form = qe.effect(beam, "ground", 2.64, 0.21)
        self.assertAlmostEqual(concrete["beam"], 0.4 * 0.39 * 5)
        self.assertAlmostEqual(form["al_vertical"], (2 * 0.39 + 0.4) * 5)
        wall = qe.clean([{"plan": "p", "op": "remove", "kind": "wall", "a": [0, 0], "b": [3, 4], "t": 0.2}])[0]
        concrete, form = qe.effect(wall, "basement", 3.25, 0.25)
        self.assertAlmostEqual(concrete["wall"], -5 * 0.2 * 3.25)
        self.assertAlmostEqual(form["euro"], -2 * 5 * 3.25)


class TakeoffWithFixesTests(SimpleTestCase):
    def test_walls_go_to_the_storey_of_the_plan_and_beams_to_the_storey_below(self):
        model = with_ids(FloorRuleTests().model())  # levels B1:p0, 1:p1, 2:p2, 3:p2, R:p3
        before = {r["key"]: r for r in ft.build_takeoff(model)["buildings"][0]["rows"]}
        edits = qe.clean([
            {"plan": "p2", "op": "add", "kind": "beam", "a": [0, 0], "b": [10, 0], "w": 0.4, "d": 0.6},
            {"plan": "p1", "op": "add", "kind": "wall", "a": [0, 0], "b": [4, 0], "t": 0.2},
        ])
        after = {r["key"]: r for r in ft.build_takeoff(model, edits=edits)["buildings"][0]["rows"]}
        beam = 0.4 * (0.6 - 0.21) * 10
        # plan p2 is the floor of storeys 2 and 3: its beam is under them, in storeys 1 and 2
        self.assertAlmostEqual(after["1"]["concrete"]["beam"] - before["1"]["concrete"]["beam"], beam, places=2)
        self.assertAlmostEqual(after["2"]["concrete"]["beam"] - before["2"]["concrete"]["beam"], beam, places=2)
        self.assertAlmostEqual(after["3"]["concrete"]["beam"], before["3"]["concrete"]["beam"], places=2)
        self.assertAlmostEqual(after["1"]["concrete"]["wall"] - before["1"]["concrete"]["wall"], 4 * 0.2 * (2.85 - 0.21), places=2)
        self.assertEqual(after["1"]["fixed"]["count"], 2)
        self.assertTrue(any(n.startswith("사람이 고친 것: 벽 1개, 보 1개 추가") for n in after["1"]["notes"]))
        self.assertIsNone(after["3"]["fixed"])


class PlanIdentityTests(SimpleTestCase):
    def test_a_frame_keeps_one_id_for_its_picture_storey_and_fixes(self):
        row = {"frame_bbox": [0.4, 0, 84100, 59400], "drawing_number": "S-111"}
        self.assertEqual(fv.plan_key("a.dwg", row), fv.plan_key("a.dwg", dict(row, frame_bbox=[0, 0, 84100.2, 59400])))
        self.assertNotEqual(fv.plan_key("a.dwg", row), fv.plan_key("b.dwg", row))

    def test_pictures_name_each_lines_layer_and_show_unclassified_lines_last(self):
        paths = [(("other", "X1"), 0, 0, 1000, 0, [(0, 0), (1000, 0)]),
                 (("frame", "A-CONC"), 0, 0, 0, 1000, [(0, 0), (0, 1000)])]
        svg, counts = fv._svg(paths, [0, 0, 2000, 2000])
        self.assertLess(svg.index('class="fv-frame" data-layer="A-CONC"'), svg.index('class="fv-other" data-layer="X1"'))
        self.assertEqual(counts, {"frame": 1, "other": 1})

    def test_a_plan_moved_onto_the_site_remembers_how(self):
        plan = {"walls": [[1, 0, 2, 0, 0]]}
        site.move_plan(plan, 0, 10, 5)
        site.move_plan(plan, 1, 3, 0)
        # (1, 0) → (11, 5) → turned (-5, 11) → (-2, 11)
        self.assertEqual(plan["walls"][0][:2], [-2, 11])
        moved = plan["moved"]
        x, y = 1, 0
        for _ in range(moved["turn"]):
            x, y = -y, x
        self.assertEqual((x + moved["dx"], y + moved["dy"]), (-2, 11))


class FloorEditsApiTests(TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        override = override_settings(QUANTITY_STATE_DB=str(Path(tmp.name) / "state.sqlite3"))
        override.enable()
        self.addCleanup(override.disable)
        users = get_user_model().objects
        self.staff = users.create_user("staff", password="staff-pass-1", is_staff=True)
        self.other = users.create_user("other", password="other-pass-1", is_staff=True)
        self.client.force_login(self.staff)
        model = with_ids(FloorRuleTests().model())
        views._quantity_state().begin_job("fv-1", self.staff.pk, "api_quantity_floor_views", "x")
        views._result_set("fv-1", {"ok": True, "results": {"floor_views": {"model": model}}})
        views._quantity_state().begin_job("fv-2", self.other.pk, "api_quantity_floor_views", "y")

    def post(self, url, body):
        return self.client.post(url, json.dumps(body), content_type="application/json")

    def test_fixes_are_kept_and_counted_in_the_takeoff(self):
        fix = {"plan": "p1", "op": "add", "kind": "wall", "a": [0, 0], "b": [4, 0], "t": 0.2}
        saved = self.post("/api/quantity/floor-edits/", {"floor_job_id": "fv-1", "edits": [fix]}).json()
        self.assertEqual(len(saved["edits"]), 1)
        self.assertEqual(self.post("/api/quantity/floor-edits/", {"floor_job_id": "fv-1"}).json()["edits"], saved["edits"])
        rows = self.post("/api/quantity/floor-takeoff/", {"floor_job_id": "fv-1"}).json()["takeoff"]["buildings"][0]["rows"]
        self.assertEqual({r["key"]: r for r in rows}["1"]["fixed"]["count"], 1)

    def test_foundation_thickness_is_kept_and_used(self):
        saved = self.post("/api/quantity/floor-edits/", {"floor_job_id": "fv-1", "foundations": {"101동": 1.1, "x": 99}}).json()
        self.assertEqual(saved["foundations"], {"101동": 1.1})
        rows = self.post("/api/quantity/floor-takeoff/", {"floor_job_id": "fv-1"}).json()["takeoff"]["buildings"][0]["rows"]
        self.assertEqual(rows[0]["foundation"]["source"], "person")
        self.assertEqual(rows[0]["foundation"]["thickness"], 1.1)

    def test_only_the_owner_may_read_or_change_fixes(self):
        self.assertEqual(self.post("/api/quantity/floor-edits/", {"floor_job_id": "fv-2", "edits": []}).status_code, 404)
        self.assertEqual(self.post("/api/quantity/floor-edits/", {"floor_job_id": "fv-1", "edits": "x"}).status_code, 400)
        self.client.logout()
        self.assertIn(self.post("/api/quantity/floor-edits/", {"floor_job_id": "fv-1"}).status_code, (302, 401, 403))


class FixingPageTests(SimpleTestCase):
    def test_the_plan_check_and_the_3d_review_both_open_the_editor(self):
        html = HOME.read_text()
        self.assertIn("quantity_plan_edit.js' %}?v=20261112-tools1", html)
        self.assertIn("data-fix>빠진 부재 고치기</button>", html)
        self.assertIn("window.cblQuantityPlanEdit.mount(root.querySelector(\".qd-floor-stage\")", html)
        self.assertIn("'<button type=\"button\" class=\"qd-btn\" data-fix2d>2D에서 고치기</button>'", html)
        self.assertIn("if (viewer.setEdits) viewer.setEdits(floorEdits().slice());", html)
        # the takeoff waits until the last fixes are kept
        body = html[html.index("async function showFloorTakeoff(data) {"):][:1500]
        self.assertLess(body.index("await floorEditsSave(true);"), body.index('fetch("/api/quantity/floor-takeoff/"'))

    def test_the_editor_snaps_traces_and_keeps_sheet_metres(self):
        js = EDIT_JS.read_text()
        self.assertIn("function toSheet(p) { return [Math.round(p.x) / 100, Math.round(H - p.y) / 100]; }", js)
        self.assertIn("function partner(lines, seg)", js)       # a line's parallel face gives the width
        self.assertIn('stage.addEventListener("dblclick", onDouble);', js)
        mass = MASS_JS.read_text()
        self.assertIn("model = applyEdits(model, options.edits);", mass)
        self.assertIn("var move = plan.moved || { turn: 0, dx: 0, dy: 0 };", mass)


class SharpPlanZoomTests(SimpleTestCase):
    """Plan pictures are zoomed through the SVG viewBox, not a CSS scale: lines are drawn again at
    every zoom, sharp and as thin as set (a CSS scale blew a 1.7 px line up to a blurred band)."""

    def test_the_helper_moves_the_viewbox_and_fits_the_plan_not_the_sheet(self):
        js = EDIT_JS.read_text()
        self.assertIn("function zoomSvg(svg, view) {", js)
        self.assertIn('svg.setAttribute("viewBox", [f.b[0] + (-view.x / view.s - f.ox) / f.k', js)
        self.assertIn("function fitView(svg, margin) {", js)
        self.assertIn("var rx = range(xs, f.b[0], f.b[0] + f.b[2]), ry = range(ys, f.b[1], f.b[1] + f.b[3]);", js)  # kept on the sheet
        self.assertIn("zoomSvg: zoomSvg, fitView: fitView", js)
        # the editor reads sheet metres from the drawing's own viewBox, not the zoomed one
        self.assertIn("var box = baseBox(svg);", js)
        self.assertNotIn('svg.style.transform = "translate(" + view.x', js)

    def test_the_floor_check_and_the_3d_sheet_use_it(self):
        html = HOME.read_text()
        self.assertIn("window.cblQuantityPlanEdit.zoomSvg(svg, view);", html)
        show = html[html.index("function showFloor(floor, keepVariant) {"):][:1600]
        self.assertLess(show.index("fitPlan();"), show.index("applyTransform();"))
        self.assertIn('if (mode === "fit") fitPlan();', html)
        mass = MASS_JS.read_text()
        self.assertIn("if (api && api.zoomSvg) { api.zoomSvg(svg, sheetView); return; }", mass)
