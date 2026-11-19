"""Architectural takeoff from CAD: walls by material and the windows and doors of the plans."""
import io
import json

import openpyxl
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase

from core import quantity_floor_views as fv
from core import quantity_views as qv
from core.test_quantity_sheet_identity import evidence, form_doc, xref_host

S = 200.0


def rect(msp, x0, y0, x1, y1, layer):
    msp.add_lwpolyline([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], close=True, dxfattribs={"layer": layer})


def opening_blocks(doc):
    door = doc.blocks.new("D1")  # a 900 door: frame lines and its swing
    door.add_line((0, 0), (900, 0), dxfattribs={"layer": "0"})
    door.add_line((0, 0), (0, 900), dxfattribs={"layer": "0"})
    door.add_arc((0, 0), 900, 0, 90, dxfattribs={"layer": "0"})
    window = doc.blocks.new("AW1")  # a 2000 window in a 200 wall
    window.add_lwpolyline([(0, 0), (2000, 0), (2000, 200), (0, 200)], close=True, dxfattribs={"layer": "0"})
    window.add_line((0, 100), (2000, 100), dxfattribs={"layer": "0"})


def unit_plan(msp, ox, oy):
    """A 30 x 15 m floor: concrete outer walls broken by four 2 m windows on each long side,
    masonry partitions (100) with door gaps, an ALC wall and a dry wall."""
    t = 200
    pieces = [(0, 4000), (6000, 11000), (13000, 17000), (19000, 24000), (26000, 30000)]
    for a, b in pieces:
        rect(msp, ox + a, oy, ox + b, oy + t, "A-CONC")
        rect(msp, ox + a, oy + 15000 - t, ox + b, oy + 15000, "A-CONC")
    rect(msp, ox, oy + t, ox + t, oy + 15000 - t, "A-CONC")
    rect(msp, ox + 30000 - t, oy + t, ox + 30000, oy + 15000 - t, "A-CONC")
    # masonry: x = 7000 from y 200 to 14800 with a 900 door gap at y 3000..3900 → 13.7 m of wall
    rect(msp, ox + 7000, oy + 200, ox + 7100, oy + 3000, "A-조적")
    rect(msp, ox + 7000, oy + 3900, ox + 7100, oy + 14800, "A-조적")
    # ALC: y = 7500 from x 7100 to 12000 (4.9 m); dry wall: x = 20000 from y 200 to 6000 (5.8 m)
    rect(msp, ox + 7100, oy + 7500, ox + 12000, oy + 7600, "A-ALC")
    rect(msp, ox + 20000, oy + 200, ox + 20100, oy + 6000, "A-건식벽")
    msp.add_blockref("D1", (ox + 7050, oy + 3000), dxfattribs={"layer": "A-DOOR", "rotation": 90})
    for a in (4000, 11000, 17000, 24000):
        msp.add_blockref("AW1", (ox + a, oy), dxfattribs={"layer": "A-WIN"})
        msp.add_blockref("AW1", (ox + a, oy + 15000 - t), dxfattribs={"layer": "A-WIN"})


def arch_drawing():
    doc = xref_host([("A-101", "101동 1층 평면도"), ("A-102", "101동 2~5층 평면도")], scale=S)
    opening_blocks(doc)
    for i in range(2):
        unit_plan(doc.modelspace(), i * 841 * S + 40 * S, 60 * S)
    return doc


def views():
    docs = {"A-101~102 101동 평면도.dxf": arch_drawing(), "XR-FORM-A(A3).dxf": form_doc()}
    return fv.build_views(docs, evidence(**docs))["views"]


class ArchMeasureTests(SimpleTestCase):
    def test_the_end_line_of_each_drawn_piece_is_not_wall_length(self):
        from core import quantity_arch
        from core.quantity_floor_takeoff import end_lines
        pieces = [(0, 0, 3000, 100), (0, 1000, 1000, 1100), (2000, 1000, 3000, 1100)]  # 3 m + two 1 m pieces, 100 thick
        paths = []
        for x0, y0, x1, y1 in pieces:
            points = [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)]
            paths.append((("masonry", "A-조적"), x0, y0, x1, y1, points))
        walls = quantity_arch.measure(paths, (-1000, -1000, 5000, 5000))["walls"]
        self.assertAlmostEqual(walls["masonry"]["length"], 5.0, delta=0.05)
        self.assertAlmostEqual(walls["masonry"]["thickness"], 0.1, delta=0.01)
        # a short stub's own sides are wall, only its end lines are not
        self.assertEqual(end_lines([(0, 0, 0.3, 0), (0.3, 0, 0.3, 0.1), (0.3, 0.1, 0, 0.1), (0, 0.1, 0, 0)]), {1, 3})

    def test_walls_by_material_and_the_openings_they_hold(self):
        found = {v["floor"]: v for v in views()}
        self.assertEqual(set(found), {"1층", "2~5층"})
        arch = found["2~5층"]["arch"]
        walls = arch["walls"]
        self.assertAlmostEqual(walls["masonry"]["length"], 13.7, delta=0.3)
        self.assertAlmostEqual(walls["masonry"]["thickness"], 0.1, delta=0.02)
        self.assertAlmostEqual(walls["alc"]["length"], 4.9, delta=0.3)
        self.assertAlmostEqual(walls["dry"]["length"], 5.8, delta=0.3)
        openings = {o["name"]: o for o in arch["openings"]}
        self.assertEqual((openings["D1"]["kind"], openings["D1"]["count"]), ("door", 1))
        self.assertAlmostEqual(openings["D1"]["width"], 0.9, delta=0.01)
        self.assertEqual(openings["D1"]["on"], {"masonry": 1})
        self.assertEqual((openings["AW1"]["kind"], openings["AW1"]["count"]), ("window", 8))
        self.assertAlmostEqual(openings["AW1"]["width"], 2.0, delta=0.01)
        self.assertEqual(openings["AW1"]["on"], {"frame": 8})


class ArchTakeoffTests(SimpleTestCase):
    def model(self):
        levels = [{"key": str(n), "label": f"{n}층", "height": 2.9, "plan": 0} for n in range(1, 6)]
        return {"buildings": [{"name": "101동", "levels": levels, "plans": []}], "heights_from_drawings": True}

    def test_walls_by_material_and_openings_per_storey(self):
        from core import quantity_arch_takeoff as at
        result = at.build(views(), self.model())
        building, = result["buildings"]
        self.assertEqual([r["key"] for r in building["rows"]], ["1", "2", "3", "4", "5"])
        row = building["rows"][2]
        clear = 2.9 - 0.21
        masonry = row["walls"]["masonry"]
        # 13.7 m of wall to the slab, plus the wall over the 0.9 m door (clear height less 2.1 m)
        self.assertAlmostEqual(masonry["area"], masonry["length"] * clear + 0.9 * (clear - 2.1), places=1)
        self.assertAlmostEqual(row["walls"]["alc"]["area"], row["walls"]["alc"]["length"] * clear, places=1)
        self.assertEqual((row["doors"], row["windows"]), (1, 8))
        self.assertEqual(building["totals"]["windows"], 40)
        self.assertEqual({o["name"]: o["count"] for o in building["openings"]}, {"D1": 5, "AW1": 40})
        keys = {a["key"] for a in result["assumed"]}
        self.assertTrue({"slab_thickness", "door_height"} <= keys)
        self.assertNotIn("arch_storey_height", keys)  # the heights came from the drawings

    def test_a_person_sets_the_door_height_and_the_storey_heights_are_listed_when_assumed(self):
        from core import quantity_arch_takeoff as at
        model = self.model()
        model["heights_from_drawings"] = False
        base = at.build(views(), model)
        taller = at.build(views(), model, overrides={"door_height": 2.4})
        row = lambda r: r["buildings"][0]["rows"][2]["walls"]["masonry"]["area"]
        self.assertAlmostEqual(row(base) - row(taller), 0.9 * 0.3, places=2)
        items = {a["key"]: a for a in taller["assumed"]}
        self.assertTrue(items["door_height"]["set"])
        self.assertEqual(items["arch_storey_height"]["value"], "1층~5층 2.9m (기본값)")  # the heights it used, by storey

    def test_without_the_structural_model_default_heights_are_used(self):
        from core import quantity_arch_takeoff as at
        building, = at.build(views(), None)["buildings"]
        self.assertEqual(building["rows"][0]["height"], fv.DEFAULT_STOREY_M["ground"])


class ArchApiTests(TestCase):
    def setUp(self):
        from core.test_quantity_cad_path import state_db
        state_db(self)
        self.staff = get_user_model().objects.create_user("staff", password="staff-pass-1", is_staff=True)
        self.client.force_login(self.staff)
        model = ArchTakeoffTests().model()
        qv._quantity_state().begin_job("fv-1", self.staff.pk, "api_quantity_floor_views", "x")
        qv._result_set("fv-1", {"ok": True, "results": {"floor_views": {"views": json.loads(json.dumps(views())), "model": model}}})

    def post(self, url, body):
        return self.client.post(url, json.dumps(body), content_type="application/json")

    def test_the_takeoff_uses_the_values_kept_with_the_job(self):
        arch = self.post("/api/quantity/floor-arch/", {"floor_job_id": "fv-1"}).json()["arch"]
        self.assertEqual(arch["buildings"][0]["totals"]["doors"], 5)
        self.post("/api/quantity/floor-edits/", {"floor_job_id": "fv-1", "overrides": {"door_height": 2.4}})
        taller = self.post("/api/quantity/floor-arch/", {"floor_job_id": "fv-1"}).json()["arch"]
        self.assertEqual(taller["overrides"], {"door_height": 2.4})
        self.assertLess(taller["buildings"][0]["totals"]["masonry"], arch["buildings"][0]["totals"]["masonry"])
        self.assertEqual(qv._learned_values("another-job").get("override:door_height"), 2.4)

    def test_only_the_owner_of_the_floor_job(self):
        other = get_user_model().objects.create_user("other", password="other-pass-1", is_staff=True)
        self.client.force_login(other)
        self.assertEqual(self.post("/api/quantity/floor-arch/", {"floor_job_id": "fv-1"}).status_code, 404)

    def test_the_excel_gets_the_architectural_sheets(self):
        arch = self.post("/api/quantity/floor-arch/", {"floor_job_id": "fv-1"}).json()["arch"]
        takeoff = self.post("/api/quantity/floor-takeoff/", {"floor_job_id": "fv-1"}).json()["takeoff"]
        response = self.post("/api/quantity/download-excel/", {"results": {"floor_takeoff": takeoff, "floor_arch": arch}})
        workbook = openpyxl.load_workbook(io.BytesIO(response.content))
        self.assertEqual(workbook.sheetnames, ["요약", "층별 집계(N층 기준)", "건축 집계(층별)", "창호 집계", "임의로 정한 것", "검토 사항"])
        texts = lambda name: [c.value for row in workbook[name].iter_rows() for c in row if c.value is not None]
        self.assertIn("[101동]", texts("건축 집계(층별)"))
        self.assertIn("AW1", texts("창호 집계"))
        self.assertIn("건축", texts("임의로 정한 것"))
        self.assertIn("건축 (CAD 건축 평면 기준 추정)", texts("요약"))


class ArchPageTests(SimpleTestCase):
    def test_the_card_follows_the_rebar_and_recalculates_with_the_others(self):
        from pathlib import Path
        html = (Path(__file__).resolve().parent / "templates" / "core" / "home.html").read_text(encoding="utf-8")
        self.assertIn("async function showFloorArch() {", html)
        self.assertIn('data-go>건축 벽체·창호 보기 →</button>', html)
        self.assertIn('data-skip>건축은 건너뛰기</button>', html)
        self.assertIn('state.quantityCards.arch = { data: arch, refresh: function (fresh) {', html)
        self.assertIn('quantityPost("/api/quantity/floor-arch/", { floor_job_id: job.id, overrides: state.floorOverrides || {} });', html)
        self.assertIn('if (arch) data.results.floor_arch = arch;', html)
        self.assertIn('assumedBox(arch.assumed || [], "건축 벽을", !assumedOpen)', html)
        # the card only comes when the architectural plans measured something
        self.assertIn('var arch = takeoff && archPlans().length ? await showFloorArch() : null;', html)
