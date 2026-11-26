"""2D·3D review page and the fixes made there (storey heights, assumed values, foundations)."""
import io
import json

import openpyxl
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase

from core import quantity_floor_takeoff as ft
from core import quantity_review as review
from core import quantity_views as views
from core.test_quantity_cad_path import state_db
from core.test_quantity_floor_rebar import model


def stacked():
    """Three storeys stacked: B1 3.5 m from -3.5, 1층 and 2층 2.85 m."""
    plans = [{"slab_area": 400, "measure": {"area": 20, "faces": 80, "outside_faces": 30, "outline": 20,
                                            "col_area": 0, "col_perimeter": 0, "openings": [], "stairs": []}}]
    levels = [{"key": "B1", "label": "지하1층", "elev": -3.5, "height": 3.5, "plan": 0, "sheet": "S-1"},
              {"key": "1", "label": "1층", "elev": 0.0, "height": 2.85, "plan": 0, "sheet": "S-2"},
              {"key": "2", "label": "2층", "elev": 2.85, "height": 2.85, "plan": 0, "sheet": "S-3"}]
    return {"buildings": [{"name": "101동", "levels": levels, "plans": plans}]}


class StoreyHeightTests(SimpleTestCase):
    def test_a_storey_height_moves_the_storeys_above(self):
        before = stacked()
        after = review.apply_storey_heights(before, {"101동": {"1": 3.2}})
        levels = {l["key"]: l for l in after["buildings"][0]["levels"]}
        self.assertEqual(levels["1"]["height"], 3.2)
        self.assertEqual(levels["1"]["height_from"], "person")
        self.assertEqual(levels["1"]["height_before"], 2.85)
        self.assertAlmostEqual(levels["2"]["elev"], 3.2)
        self.assertAlmostEqual(levels["B1"]["elev"], -3.5)
        self.assertEqual(before["buildings"][0]["levels"][1]["height"], 2.85)  # the given model is left alone

    def test_heights_out_of_range_and_unknown_names_are_dropped(self):
        clean = review.clean_storey_heights({"101동": {"1": 30, "2": "2.9", "<x>": 3.0}, "102동": "3.0", 5: {"1": 3}})
        self.assertEqual(clean, {"101동": {"2": 2.9}})

    def test_the_takeoff_uses_the_storey_height_set(self):
        base = {r["key"]: r for r in ft.build_takeoff(stacked())["buildings"][0]["rows"]}
        taller = {r["key"]: r for r in ft.build_takeoff(review.apply_storey_heights(stacked(), {"101동": {"1": 3.35}}))["buildings"][0]["rows"]}
        self.assertAlmostEqual(taller["1"]["concrete"]["wall"] - base["1"]["concrete"]["wall"], 20 * 0.5, places=2)
        self.assertEqual(taller["1"]["height_from"], "person")
        self.assertEqual(base["1"]["height_from"], "drawing")
        self.assertAlmostEqual(taller["2"]["elevation"], 3.35)

    def test_feedback_is_checked(self):
        fb = review.clean_feedback({"storey_heights": {"101동": {"1": 3.0}}, "overrides": {"slab_thickness": 250, "nope": 1},
                                    "foundations": {"101동": 1.2, "102동": 9}, "edits": "x"})
        self.assertEqual(fb["storey_heights"], {"101동": {"1": 3.0}})
        self.assertEqual(fb["overrides"], {"slab_thickness": 250})
        self.assertEqual(fb["foundations"], {"101동": 1.2})
        self.assertEqual(fb["edits"], [])


class ReviewPageTests(SimpleTestCase):
    def test_the_review_data_has_every_storey_with_its_quantities_and_plan(self):
        m = stacked()
        views_list = [{"building": "101동", "floor": "1층", "sheet": "S-2", "svg": "<svg viewBox='0 0 1 1'></svg>", "discipline": "struct"}]
        data = review.review_data(m, ft.build_takeoff(m), views=views_list, title="현장")
        storeys = {s["key"]: s for s in data["buildings"][0]["storeys"]}
        self.assertEqual(set(storeys), {"B1", "1", "2"})
        self.assertEqual(storeys["1"]["plan"], 0)
        self.assertIsNone(storeys["2"]["plan"])
        self.assertGreater(storeys["1"]["concrete"]["total"], 0)
        self.assertEqual(storeys["B1"]["kind"], "basement")

    def test_the_page_works_offline_and_cannot_be_closed_by_the_data(self):
        m = stacked()
        m["buildings"][0]["name"] = "</script><b>"
        html = review.render_html(review.review_data(m, ft.build_takeoff(m), title="a</script>"))
        self.assertIn("cblQuantityReview.mount", html)
        self.assertIn("cblQuantityMass3D", html)
        self.assertNotIn("</script><b>", html)
        self.assertNotIn('src="http', html)


class ReviewWebTests(TestCase):
    def setUp(self):
        state_db(self)
        self.staff = get_user_model().objects.create_user("staff", password="staff-pass-1", is_staff=True)
        self.client.force_login(self.staff)
        views._quantity_state().begin_job("fv-1", self.staff.pk, "api_quantity_floor_views", "x")
        views._result_set("fv-1", {"ok": True, "results": {"floor_views": {"model": model(), "views": []}}})

    def post(self, url, body):
        return self.client.post(url, json.dumps(body), content_type="application/json")

    def test_storey_heights_saved_on_the_review_page_change_the_takeoff(self):
        key = model()["buildings"][0]["levels"][1]["key"]
        name = model()["buildings"][0]["name"]
        before = self.post("/api/quantity/floor-takeoff/", {"floor_job_id": "fv-1"}).json()["takeoff"]
        saved = self.post("/api/quantity/floor-edits/", {"floor_job_id": "fv-1", "storey_heights": {name: {key: 4.0}}}).json()
        self.assertEqual(saved["storey_heights"], {name: {key: 4.0}})
        # saving the members alone keeps the storey heights
        self.post("/api/quantity/floor-edits/", {"floor_job_id": "fv-1", "edits": []})
        after = self.post("/api/quantity/floor-takeoff/", {"floor_job_id": "fv-1"}).json()["takeoff"]
        row = lambda t: {r["key"]: r for r in t["buildings"][0]["rows"]}[key]
        self.assertEqual(row(after)["height"], 4.0)
        self.assertGreater(row(after)["concrete"]["wall"], row(before)["concrete"]["wall"])

    def test_the_review_page_is_the_owners(self):
        page = self.client.get("/quantity/review/fv-1/")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"cblQuantityReview", page.content)
        other = get_user_model().objects.create_user("other", password="other-pass-1", is_staff=True)
        self.client.force_login(other)
        self.assertEqual(self.client.get("/quantity/review/fv-1/").status_code, 404)
        self.client.logout()
        self.assertEqual(self.client.get("/quantity/review/fv-1/").status_code, 403)

    def test_the_excel_lists_the_storeys_of_the_3d(self):
        takeoff = self.post("/api/quantity/floor-takeoff/", {"floor_job_id": "fv-1"}).json()["takeoff"]
        response = self.post("/api/quantity/download-excel/", {"results": {"floor_takeoff": takeoff}})
        sheet = openpyxl.load_workbook(io.BytesIO(response.content))["3D 층 구성"]
        texts = [c.value for row in sheet.iter_rows() for c in row]
        self.assertIn("바닥 EL(m)", texts)
        self.assertIn("도면", texts)


class FolderCommandTests(SimpleTestCase):
    def test_archives_and_second_copies_are_read_once(self):
        import os
        import tempfile
        import time
        from core.management.commands.quantity_takeoff import collect
        with tempfile.TemporaryDirectory() as root:
            def put(rel, age_days=0):
                path = os.path.join(root, rel)
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "wb") as handle:
                    handle.write(b"x")
                stamp = time.time() - age_days * 86400
                os.utime(path, (stamp, stamp))
                return path
            work = put("구조/S-111 평면도.dwg")
            put("BIND/S-111 평면도.dwg")                 # the same day: the working copy is read
            put("아카이브/구조/S-301 일람표.dwg")          # an old issue
            newer = put("BIND/S-401 주차장.dwg")
            put("구조/S-401 주차장.dwg", age_days=30)     # older than the bound copy
            self.assertEqual(sorted(collect([root])), sorted([newer, work]))
        with tempfile.TemporaryDirectory() as root:
            only = os.path.join(root, "archive", "S-1.dxf")
            os.makedirs(os.path.dirname(only))
            open(only, "wb").close()
            self.assertEqual(collect([root]), [only])          # nothing else: the archive is read

    def test_feedback_file_and_cache_round_trip(self):
        import gzip
        import os
        import tempfile
        from core.management.commands import quantity_takeoff as cmd
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "fb.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump({"storey_heights": {"101동": {"1": 3.1}}, "overrides": {"slab_thickness": 230}}, handle)
            fb = cmd.read_feedback(path)
            self.assertEqual(fb["storey_heights"], {"101동": {"1": 3.1}})
            read = {"floor_views": {"model": stacked(), "views": []}, "links": None, "openings": None}
            cache = os.path.join(root, "c.json.gz")
            cmd.save_cache(cache, read)
            again = cmd.load_cache(cache)
            result = cmd.compute(again, feedback=fb)
            row = {r["key"]: r for r in result["takeoff"]["buildings"][0]["rows"]}["1"]
            self.assertEqual(row["height"], 3.1)
            self.assertIn("<!doctype html>", cmd.review_html(result, "t"))
            with gzip.open(cache, "rt", encoding="utf-8") as handle:
                self.assertIn("floor_views", json.load(handle))
