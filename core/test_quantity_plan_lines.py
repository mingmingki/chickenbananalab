"""Cleaner plans and finer fixing: stray lines left out by the reading, lines people erase (the
drawings are read again without them), lines brought back, and openings cut into a wall."""
import json
import tempfile
from pathlib import Path
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings

from core import quantity_edits as qe
from core import quantity_floor_rebar as fr
from core import quantity_floor_takeoff as ft
from core import quantity_floor_views as fv
from core import quantity_views as views
from core.test_quantity_floor_rebar import links, model as rebar_model

CORE = Path(__file__).parent
HOME = CORE / "templates" / "core" / "home.html"
EDIT_JS = CORE / "static" / "core" / "js" / "quantity_plan_edit.js"
MASS_JS = CORE / "static" / "core" / "js" / "quantity_mass3d.js"
FRAME = (0, 0, 40000, 30000)  # a 40 x 30 m frame, drawing units mm


def path(category, points):
    pts = [(x * 1000, y * 1000) for x, y in points]
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return (category, min(xs), min(ys), max(xs), max(ys), pts)


def plan_paths():
    """A 10 x 6 m room of 200 mm walls, plus what a drawing may also hold on its member layers."""
    room = [path("wall", [(5, 5), (15, 5)]), path("wall", [(5, 5.2), (15, 5.2)]),
            path("wall", [(5, 11), (15, 11)]), path("wall", [(5, 11.2), (15, 11.2)]),
            path("wall", [(5, 5), (5, 11.2)]), path("wall", [(5.2, 5.2), (5.2, 11)]),
            path("wall", [(15, 5), (15, 11.2)]), path("wall", [(14.8, 5.2), (14.8, 11)])]
    lone = path("wall", [(30, 20), (36, 24)])                  # one line far from the plan, no second face
    outside = path("wall", [(10, 12), (12, 60)])               # runs off the sheet
    short_wall = [path("wall", [(25, 5), (28, 5)]), path("wall", [(25, 5.2), (28, 5.2)])]  # a wall apart: two faces
    column = path("col", [(25, 15), (25.5, 15), (25.5, 15.5), (25, 15.5), (25, 15)])      # a column apart
    return room, lone, outside, short_wall, column


class StrayLineTests(SimpleTestCase):
    def test_lone_lines_apart_and_lines_off_the_sheet_are_stray_walls_and_columns_apart_are_not(self):
        room, lone, outside, short_wall, column = plan_paths()
        stray = fv._stray_segments(room + [lone, outside] + short_wall + [column], FRAME)
        rounded = sorted(tuple(round(v, 2) for v in seg) for seg in stray)
        self.assertEqual(rounded, [(10.0, 12.0, 12.0, 60.0), (30.0, 20.0, 36.0, 24.0)])

    def test_left_out_lines_are_found_again_either_way_round_and_people_win(self):
        left = fv.LeftOut(stray=[(30, 20, 36, 24)], erase=[(5, 5, 15, 5)], keep=[(36.01, 24.02, 30, 20)])
        self.assertEqual(left.why(15.02, 5.0, 5.0, 4.99), "person")   # rounding of the picture, other direction
        self.assertIsNone(left.why(30, 20, 36, 24))                    # brought back
        self.assertIsNone(left.why(5, 11, 15, 11))
        self.assertEqual(fv.LeftOut(stray=[(1, 1, 2, 2)]).why(1, 1, 2, 2), "stray")

    def test_a_polyline_is_split_where_a_piece_is_left_out(self):
        left = fv.LeftOut(erase=[(1, 0, 2, 0)])
        runs = fv._runs([(0, 0), (1, 0), (2, 0), (3, 0)], left)
        self.assertEqual([(len(points), why) for points, why in runs], [(2, None), (2, "person"), (2, None)])


class LeftOutModelTests(SimpleTestCase):
    def test_erased_and_stray_lines_leave_the_model_and_its_measures(self):
        room, lone, outside, _short, _column = plan_paths()
        paths = room + [lone, outside]
        before = fv._plan_model(paths, FRAME)
        left = fv.LeftOut.find(paths, FRAME, {"erase": [[5, 5, 15, 5]]})  # one face of the bottom wall
        after = fv._plan_model(paths, FRAME, left)
        self.assertEqual(after["left_out"], {"stray": 2, "person": 1})
        self.assertNotIn([5.0, 5.0, 15.0, 5.0], after["walls"])
        self.assertNotIn([30.0, 20.0, 36.0, 24.0], after["walls"])
        # the bottom wall lost a face, so its section is no longer measured
        self.assertAlmostEqual(before["measure"]["area"] - after["measure"]["area"], 10 * 0.2, delta=0.15)

    def test_a_column_loses_its_outline_whole(self):
        column = path("col", [(5, 5), (5.5, 5), (5.5, 5.5), (5, 5.5), (5, 5)])
        wall = [path("wall", [(6, 5), (16, 5)]), path("wall", [(6, 5.2), (16, 5.2)])]
        left = fv.LeftOut(erase=[(5, 5, 5.5, 5)])
        model = fv._plan_model([column] + wall, FRAME, left)
        self.assertEqual(model["cols"], [])
        self.assertEqual(model["left_out"], {"person": 4})

    def test_the_picture_draws_left_out_lines_apart_and_says_why(self):
        room, lone, _outside, _short, _column = plan_paths()
        paths = [((("frame", "WALL") if p[0] == "wall" else p[0]),) + p[1:] for p in room + [lone]]
        left = fv.LeftOut.find([p for p in room + [lone]], FRAME, {"erase": [[5, 5, 15, 5]]})
        svg, counts = fv._svg(paths, FRAME, left_out=left)
        self.assertIn('class="fv-removed" data-why="stray" d="M3000 1000L3600 600"', svg)
        self.assertIn('class="fv-removed" data-why="person" d="M500 2500L1500 2500"', svg)
        self.assertEqual(counts["removed"], 2)
        self.assertEqual(counts["frame"], 7)


class OpeningFixTests(SimpleTestCase):
    def opening(self, **extra):
        return qe.clean([dict({"plan": "p1", "op": "add", "kind": "opening", "a": [0, 0], "b": [1.5, 0], "t": 0.2, "h": 2.1}, **extra)])

    def test_an_opening_takes_the_wall_out_and_adds_its_sides(self):
        fix = self.opening()[0]
        concrete, form = qe.effect(fix, "ground", 2.64, 0.21)
        self.assertAlmostEqual(concrete["wall"], -1.5 * 0.2 * 2.1)
        self.assertAlmostEqual(form["al_vertical"], -2 * 1.5 * 2.1 + 0.2 * (1.5 + 2 * 2.1))
        self.assertEqual(self.opening(op="remove"), [])          # taken back by deleting the fix
        self.assertEqual(self.opening(h=99)[0]["h"], 6.0)         # bounded

    def test_the_storey_using_the_plan_loses_the_wall_and_gains_opening_bars(self):
        m = rebar_model()
        fix = self.opening()
        before = {r["key"]: r for r in ft.build_takeoff(m, links())["buildings"][0]["rows"]}
        after = {r["key"]: r for r in ft.build_takeoff(m, links(), edits=fix)["buildings"][0]["rows"]}
        self.assertLess(after["1"]["concrete"]["wall"], before["1"]["concrete"]["wall"])
        bars_before = {r["key"]: r for r in fr.build_rebar(m, links())["buildings"][0]["rows"]}
        bars_after = {r["key"]: r for r in fr.build_rebar(m, links(), edits=fix)["buildings"][0]["rows"]}
        self.assertLess(bars_after["1"]["kg"]["wall"], bars_before["1"]["kg"]["wall"])
        self.assertGreater(bars_after["1"]["kg"]["opening"], bars_before["1"]["kg"]["opening"])
        no_extra = {r["key"]: r for r in fr.build_rebar(m, links(), edits=fix, extras={"opening": False})["buildings"][0]["rows"]}
        self.assertEqual(no_extra["1"]["kg"]["opening"], 0)


class FloorLinesApiTests(TestCase):
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
        views._quantity_state().begin_job("fv-1", self.staff.pk, "api_quantity_floor_views", "x")
        views._quantity_state().begin_job("fv-2", self.other.pk, "api_quantity_floor_views", "y")

    def post(self, body):
        return self.client.post("/api/quantity/floor-lines/", json.dumps(body), content_type="application/json")

    def test_erased_lines_are_kept_cleaned_and_given_to_the_next_reading(self):
        lines = {"p1": {"erase": [[1, 2, 3, 4], [1, 1, 1, 1], ["x", 0, 0, 0], [0, 0, 1]], "keep": [[5, 5, 6, 6]]}, "": {"erase": [[0, 0, 1, 1]]}}
        saved = self.post({"floor_job_id": "fv-1", "lines": lines}).json()
        self.assertEqual(saved["lines"], {"p1": {"erase": [[1.0, 2.0, 3.0, 4.0]], "keep": [[5.0, 5.0, 6.0, 6.0]]}})
        self.assertEqual(self.post({"floor_job_id": "fv-1"}).json()["lines"], saved["lines"])
        self.assertEqual(views._floor_lines("fv-1"), saved["lines"])

    def test_reading_again_starts_the_job_over_the_kept_drawings(self):
        with mock.patch.object(views.os.path, "exists", return_value=True), \
                mock.patch.object(views.threading, "Thread") as thread:
            body = self.post({"floor_job_id": "fv-1", "lines": {"p1": {"erase": [[1, 2, 3, 4]]}}, "rerun": True}).json()
        self.assertTrue(body["rerun"])
        thread.assert_called_once()
        self.assertEqual(thread.call_args.kwargs["target"], views._rerun_floor_views_job)

    def test_only_the_owner(self):
        self.assertEqual(self.post({"floor_job_id": "fv-2", "lines": {}}).status_code, 404)
        self.assertEqual(self.post({"floor_job_id": "fv-1", "lines": "x"}).status_code, 400)
        self.client.logout()
        self.assertIn(self.post({"floor_job_id": "fv-1"}).status_code, (302, 401, 403))


class EditorToolTests(SimpleTestCase):
    def test_the_editor_has_the_finer_tools(self):
        js = EDIT_JS.read_text()
        for tool in ('["view", "선택"]', '["opening", "개구부"]', '["remove", "부재 빼기"]', '["erase", "선 지우기"]',
                     '["keep", "되살리기"]', '["measure", "거리 재기"]'):
            self.assertIn(tool, js)
        self.assertIn("function inBox(b) {", js)                       # a dragged box erases what is inside
        self.assertIn("e.stopImmediatePropagation();", js)              # ... instead of panning
        self.assertIn("function typedEnd() {", js)                      # a typed length
        self.assertIn("function ortho(q, e) {", js)                     # Shift: straight
        self.assertIn("function onWall(p) {", js)                       # openings lie on the picked wall
        self.assertIn("data-qpe-delete", js)                            # the selected fix, out
        self.assertIn("data-qpe-reread", js)                            # erased lines: read again

    def test_the_page_keeps_lines_and_reads_again(self):
        html = HOME.read_text()
        self.assertIn('fetch("/api/quantity/floor-lines/"', html)
        self.assertIn("lines: floorLines(), linesPending: floorLinesPending,", html)
        self.assertIn('["removed", "뺀 선(자동·지움)"]', html)
        self.assertIn("viewer.setModel(massModel());", html)
        mass = MASS_JS.read_text()
        self.assertIn("outer.setModel = function (next) {", mass)
        self.assertIn('plan.user.opens.push([a[0], a[1], z[0], z[1], e.t || 0.2, e.h || 2.1]);', mass)


class EstimatedFrameTests(SimpleTestCase):
    def test_a_drawing_without_a_frame_keeps_lines_running_past_its_drawn_extent(self):
        room, lone, outside, _short, _column = plan_paths()
        stray = fv._stray_segments(room + [outside], FRAME, outside=False)
        self.assertEqual(stray, [])  # the long line touches the plan, so it is no lone group either
        self.assertEqual(len(fv._stray_segments(room + [outside], FRAME)), 1)


class PlanAskTests(SimpleTestCase):
    SVG = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 4000 3000" preserveAspectRatio="xMidYMid meet">'
           '<path class="fv-frame" data-layer="WALL" d="M500 2500L1500 2500M500 2480L1500 2480"/>'
           '<path class="fv-other" data-layer="X" d="M3500 100L3900 100"/>'
           '<path class="fv-removed" data-why="stray" d="M3000 1000L3600 600"/></svg>')

    def test_the_lines_of_the_marked_part_in_sheet_metres(self):
        from core import quantity_plan_ask as ask
        lines = ask.svg_lines(self.SVG, [4, 4, 16, 6])
        self.assertEqual(lines[0], {"a": [5.0, 5.0], "b": [15.0, 5.0], "class": "frame", "layer": "WALL"})
        self.assertEqual(len(lines), 2)
        self.assertEqual(ask.svg_lines(self.SVG, [30, 20, 40, 30])[0]["class"], "removed")

    def test_the_cost_is_told_in_tokens_and_money_only_with_prices(self):
        from core import quantity_plan_ask as ask
        self.assertEqual(ask.image_tokens(1024, 768), 85 + 170 * 4)
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertIsNone(ask.estimate("벽이 빠졌어요", [], (1024, 768))["krw"])
        with mock.patch.dict("os.environ", {"OPENAI_QUANTITY_PRICE_IN": "2", "OPENAI_QUANTITY_PRICE_OUT": "8", "QTY_KRW_PER_USD": "1000"}):
            est = ask.estimate("벽이 빠졌어요", [], (1024, 768))
            self.assertEqual(est["krw"], round((est["input_tokens"] * 2 + est["output_tokens"] * 8) / 1000))

    def test_proposals_are_checked_and_kept_to_the_marked_part(self):
        from core import quantity_plan_ask as ask
        raw = json.dumps({"answer": "벽 하나", "fixes": [
            {"op": "add", "kind": "wall", "a": [5, 5.1], "b": [15, 5.1], "t": 0.2, "reason": "두 면 사이"},
            {"op": "add", "kind": "wall", "a": [50, 50], "b": [60, 50], "t": 0.2},   # far outside
            {"op": "paint", "kind": "wall", "a": [5, 5], "b": [6, 5]},               # not a fix
            {"op": "add", "kind": "opening", "a": [6, 5.1], "b": [7, 5.1], "t": 0.2, "h": 2.1}]})
        out = ask.proposals(raw, "p1", [4, 4, 16, 6])
        self.assertEqual(out["answer"], "벽 하나")
        self.assertEqual([(f["kind"], f["plan"], f["by"]) for f in out["fixes"]], [("wall", "p1", "ai"), ("opening", "p1", "ai")])
        self.assertEqual(out["fixes"][0]["reason"], "두 면 사이")


class PlanAskApiTests(TestCase):
    PNG = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        override = override_settings(QUANTITY_STATE_DB=str(Path(tmp.name) / "state.sqlite3"), QUANTITY_BRAIN_DB=str(Path(tmp.name) / "brain.sqlite3"))
        override.enable()
        self.addCleanup(override.disable)
        self.staff = get_user_model().objects.create_user("staff", password="staff-pass-1", is_staff=True)
        self.client.force_login(self.staff)
        views._quantity_state().begin_job("fv-1", self.staff.pk, "api_quantity_floor_views", "x")
        views._result_set("fv-1", {"ok": True, "results": {"floor_views": {"views": [
            {"plan_id": "p1", "building": "101동", "floor": "3층", "sheet": "S-1", "svg": PlanAskTests.SVG}]}}})

    def post(self, body):
        return self.client.post("/api/quantity/plan-ask/", json.dumps(dict({"floor_job_id": "fv-1", "plan": "p1", "box": [4, 4, 16, 6],
                                                                            "text": "여기 벽이 빠졌어요", "image": self.PNG, "width": 1, "height": 1}, **body)),
                                content_type="application/json")

    def test_first_only_what_would_be_sent_and_its_cost(self):
        with mock.patch("core.quantity_ai.get_ai_client") as client:
            body = self.post({}).json()
        client.assert_not_called()
        self.assertEqual(body["sending"], {"lines": 2, "image_kb": 0, "text": 10})
        self.assertGreater(body["estimate"]["input_tokens"], 0)

    def test_confirmed_the_ai_is_asked_once_and_its_fixes_come_back_checked(self):
        from core import quantity_ai
        answer = quantity_ai.AiResponse(text=json.dumps({"answer": "벽", "fixes": [{"op": "add", "kind": "wall", "a": [5, 5.1], "b": [15, 5.1], "t": 0.2}]}),
                                        finish_reason="STOP", input_tokens=900, output_tokens=200, reasoning_tokens=300)
        fake = mock.Mock()
        fake.generate.return_value = answer
        with mock.patch("core.quantity_ai.get_ai_client", return_value=fake):
            body = self.post({"confirm": True}).json()
        self.assertEqual(fake.generate.call_count, 1)
        sent = fake.generate.call_args.kwargs["contents"]
        self.assertIn("여기 벽이 빠졌어요", sent[0])
        self.assertIsInstance(sent[1], quantity_ai.ImagePart)
        self.assertEqual([f["kind"] for f in body["fixes"]], ["wall"])
        self.assertEqual(body["usage"]["output_tokens"], 500)
        self.assertIsNone(views._quantity_state().get("plan_ask_lock", str(self.staff.pk)))  # free for the next question

    def test_one_question_at_a_time_and_no_key_no_call(self):
        views._quantity_state().put("plan_ask_lock", str(self.staff.pk), {"at": 1}, ttl=60)
        with mock.patch("core.quantity_ai.get_ai_client", return_value=mock.Mock()):
            self.assertEqual(self.post({"confirm": True}).status_code, 429)
        views._quantity_state().pop("plan_ask_lock", str(self.staff.pk))
        with mock.patch("core.quantity_ai.get_ai_client", return_value=None):
            self.assertEqual(self.post({"confirm": True}).status_code, 503)
        self.assertEqual(self.post({"text": ""}).status_code, 400)
        self.assertEqual(self.post({"box": [0, 0, 0.1, 0.1]}).status_code, 400)

    def test_what_was_accepted_is_kept_for_learning_but_not_read_as_layers(self):
        body = self.post({"accepted": [{"op": "add", "kind": "wall", "a": [0, 0], "b": [1, 0]}], "rejected": 2, "answer": "벽"}).json()
        self.assertTrue(body["ok"])
        brain = views._brain()
        event = brain.events(1)[0]
        self.assertEqual((event["kind"], event["payload"]["accepted"], event["payload"]["rejected"]), ("ask", ["add:wall"], 2))
        self.assertEqual(brain.knowledge()["names"].get("text"), None)
        self.assertEqual(brain.corrections("fv-1"), {})


class MarkAndFix3dPageTests(SimpleTestCase):
    def test_marking_asks_with_the_cost_first_and_fixes_from_3d_go_to_the_plans(self):
        js = EDIT_JS.read_text()
        self.assertIn('["mark", "표시·AI"]', js)
        self.assertIn("function markImage() {", js)                     # the marked part drawn here
        self.assertIn('data-qpe-ask="estimate">AI에게 물어보기 (비용 먼저 보기)', js)
        self.assertIn('data-qpe-ask="send">보내기 (유료)', js)
        self.assertIn('data-qpe-ask="take">고른 것 넣기', js)
        html = HOME.read_text()
        self.assertIn('fetch("/api/quantity/plan-ask/"', html)
        self.assertIn("onAsk: planAsk", html)
        mass = MASS_JS.read_text()
        self.assertIn("(options.onEdit ? '<button type=\"button\" data-tool=\"fix\">고치기</button>' : '')", mass)
        self.assertIn("function toSheet(plan, q) {", mass)                # back through the site placing
        self.assertIn("function nearestKind(lv, q, wallsOnly) {", mass)
        self.assertIn('["mark", "표시·AI"]', mass)
        self.assertIn("fix: { on: fix.on, mode: fix.mode", mass)          # fixing goes on after redrawing
        self.assertIn("onEdit: function (edit) {", html)
