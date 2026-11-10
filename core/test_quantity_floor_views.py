"""Floor-by-floor colored plans: layer materials, floor names, file choice, drawing walk and the job."""
import io
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import ezdxf
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase, override_settings

from core import quantity_floor_views as fv
from core import quantity_views as views


class MaterialAndFloorTests(SimpleTestCase):
    def test_layers_map_to_materials_and_noise_is_dropped(self):
        cases = {
            "단위세대-Conc": "frame", "XR-주동 평면도|단위세대-Brick": "masonry", "단위세대-ALC": "alc",
            "단위세대-석고보드": "dry", "WALL-Gboard": "dry", "단위세대-WIN": "opening", "단위세대-DOOR": "opening",
            "0_STR_COL": "frame", "0_STR_WALL": "frame", "0_STR_GIRDER": "beam", "0_STR_BEAM": "beam",
            "0_STR_COL_NAME": None, "단위세대-CEN": None, "A-TEXT": None, "Bubble": None, "HAT": None,
            "점형블럭": None, "단위세대-FUR": None, "0": None,
        }
        for layer, expected in cases.items():
            self.assertEqual(fv.layer_category(layer), expected, layer)

    def test_floor_names_come_from_sheet_titles(self):
        cases = [
            ("101동 지하 2층 평면도", ("101동", "지하2층", "basement")),
            ("102동 지상 3층~지상 7층 평면도", ("102동", "3~7층", "ground")),
            ("101동 지붕, 옥탑층, 옥탑지붕 평면도", ("101동", "지붕·옥탑", "roof")),
            ("지하1층 지하주차장 구조평면도", ("지하주차장", "지하1층", "basement")),
            ("66A형 단위세대 평면도 (확장형)", ("단위세대", "66A형 확장형", "unit")),
            ("74P1형 단위세대 구조평면도", ("단위세대", "74P1형", "unit")),
            ("101동 동기초 구조평면도", ("101동", "기초", "basement")),
        ]
        for title, (building, floor, group) in cases:
            got = fv.floor_of(title)
            self.assertEqual((got["building"], got["floor"], got["group"]), (building, floor, group), title)
        self.assertEqual(fv.floor_of("지하1층 지하주차장 구조평면도")["discipline"], "struct")
        self.assertEqual(fv.floor_of("101동 지상 1층 평면도")["discipline"], "arch")

    def test_only_plan_titles_are_drawn(self):
        self.assertTrue(fv.is_plan_title("101동 지상 1층 평면도"))
        self.assertTrue(fv.is_plan_title("지하2층 지하주차장 구조평면도"))
        for title in ("아파트 보 일람표", "단위세대 단열,방수, 결로방지평면도", "코아 확대평면도",
                      "건물 배치도(인동거리,지반층 평면도)", "주동 단면도"):
            self.assertFalse(fv.is_plan_title(title), title)

    def test_files_are_chosen_by_name_before_they_are_read(self):
        self.assertTrue(fv.is_plan_file("A-401~419 주동 평면도.dwg"))
        self.assertTrue(fv.is_plan_file("S-111~130 동 구조평면도.dwg"))
        self.assertFalse(fv.is_plan_file("A-331~342 단위세대 단열,방수, 결로방지평면도.dwg"))
        self.assertFalse(fv.is_plan_file("S-301 아파트 기둥 일람표.dwg"))
        self.assertTrue(fv.is_reference_file("XR-주동 평면도.dwg"))
        self.assertTrue(fv.is_reference_file("52타입 수정.dxf"))
        self.assertFalse(fv.is_reference_file("S-301 아파트 기둥 일람표.dwg"))

    def test_referenced_xrefs_are_read_from_the_dxf_text(self):
        text = "  0\nBLOCK\n  2\nXR-주동 평면도\n  1\n..\\Xref\\XR-주동 평면도.dwg\n  0\nENDBLK\n"
        self.assertEqual(fv.referenced_files(text), {"xr-주동 평면도"})


def _doc_with_xref():
    xref = ezdxf.new()
    xref.modelspace().add_line((0, 0), (1000, 0), dxfattribs={"layer": "단위세대-Conc"})
    xref.modelspace().add_line((0, 500), (1000, 500), dxfattribs={"layer": "단위세대-Brick"})
    xref.modelspace().add_line((0, 900), (1000, 900), dxfattribs={"layer": "단위세대-FUR"})
    host = ezdxf.new()
    host.add_xref_def("..\\Xref\\XR-PLAN.dwg", "XR-PLAN")
    host.modelspace().add_blockref("XR-PLAN", (5000, 0))
    block = host.blocks.new("WIN1")
    block.add_line((0, 0), (800, 0), dxfattribs={"layer": "0"})
    host.modelspace().add_blockref("WIN1", (5000, 200), dxfattribs={"layer": "단위세대-WIN"})
    return host, xref


class DrawingWalkTests(SimpleTestCase):
    def test_xrefs_are_drawn_in_place_and_block_layer_zero_takes_the_insert_layer(self):
        host, xref = _doc_with_xref()
        out = []
        fv._walk(host, host.modelspace(), ezdxf.math.Matrix44(), None, {"xr-plan": xref}, out)
        cats = sorted(item[0] for item in out)
        self.assertEqual(cats, ["frame", "masonry", "opening"])  # furniture is not drawn
        frame = next(item for item in out if item[0] == "frame")
        self.assertEqual((round(frame[1]), round(frame[3])), (5000, 6000))  # moved with the xref insert

    def test_a_clipped_xref_copy_draws_only_its_window(self):
        from ezdxf import xclip
        host, xref = _doc_with_xref()
        copy = host.modelspace().add_blockref("XR-PLAN", (20000, 0))
        xclip.XClip(copy).set_wcs_clipping_path([(20000, 400), (21000, 600)])  # only the brick line
        out = []
        fv._walk(host, host.modelspace(), ezdxf.math.Matrix44(), None, {"xr-plan": xref}, out)
        far = sorted(item[0] for item in out if item[1] >= 20000)
        self.assertEqual(far, ["masonry"])

    def test_a_sheet_keeps_only_what_crosses_its_frame(self):
        host, xref = _doc_with_xref()
        out = []
        fv._walk(host, host.modelspace(), ezdxf.math.Matrix44(), None, {"xr-plan": xref}, out)
        svg, counts = fv._svg(out, [4000, -100, 8000, 2900])
        self.assertEqual(counts, {"frame": 1, "masonry": 1, "opening": 1})
        self.assertIn('class="fv-frame"', svg)
        self.assertIn('viewBox="0 0 400 300"', svg)
        _empty, none = fv._svg(out, [0, 0, 1000, 1000])
        self.assertEqual(none, {})

    def test_views_are_ordered_by_building_then_floor(self):
        host, xref = _doc_with_xref()
        rows = [
            {"frame_bbox": [4000, -100, 8000, 2900], "drawing_title": title, "drawing_number": number}
            for number, title in (("A-305", "66A형 단위세대 평면도"), ("A-403", "101동 지상 1층 평면도"),
                                  ("A-401", "101동 지하 2층 평면도"), ("A-701", "지하2층 지하주차장 평면도"),
                                  ("S-113", "101동 지상 1층 구조평면도"))
        ]
        with patch("core.quantity_sheet_identity.resolve_cad_sheets", return_value={"plan.dwg": rows}):
            result = fv.build_views({"plan.dwg": host, "XR-PLAN.dwg": xref}, {"plan.dwg": {}})
        order = [(v["building"], v["floor"], v["variant"]) for v in result["views"]]
        self.assertEqual(order, [("101동", "지하2층", "건축평면"), ("101동", "1층", "건축평면"),
                                 ("101동", "1층", "구조평면"), ("지하주차장", "지하2층", "건축평면"),
                                 ("단위세대", "66A형", "건축평면")])
        self.assertEqual(result["warnings"], [])


class BuildingModelTests(SimpleTestCase):
    def square(self, x0, y0, x1, y1):
        return [[x0, y0, x1, y0], [x1, y0, x1, y1], [x1, y1, x0, y1], [x0, y1, x0, y0]]

    def test_the_floor_is_filled_inside_walls_even_with_a_door_gap(self):
        walls = self.square(0, 0, 20, 10)
        walls[0] = [0, 0, 9.5, 0]  # a 0.5 m gap closes by itself
        walls.append([10, 0, 20, 0])
        rects, outside = fv._footprint(walls)
        area = sum((r[2] - r[0]) * (r[3] - r[1]) for r in rects)
        self.assertAlmostEqual(area, 200, delta=12)
        self.assertTrue(outside(-3, 5))
        self.assertFalse(outside(10, 5))

    def test_a_wide_gap_needs_the_slab_edge_line(self):
        walls = self.square(0, 0, 20, 10)
        walls[0] = [0, 0, 8, 0]
        walls.append([9.5, 0, 20, 0])  # a 1.5 m window gap
        open_rects, _ = fv._footprint(walls)
        closed_rects, _ = fv._footprint(walls + [[8, 0, 9.5, 0]])  # the slab edge drawn across it
        area = lambda rects: sum((r[2] - r[0]) * (r[3] - r[1]) for r in rects)
        self.assertLess(area(open_rects), 50)
        self.assertAlmostEqual(area(closed_rects), 200, delta=12)

    def test_outer_faces_are_marked_for_gang_forms(self):
        outer = self.square(0, 0, 20, 10)
        inner = self.square(0.2, 0.2, 19.8, 9.8)
        members = [("wall", min(w[0], w[2]) * 1000, min(w[1], w[3]) * 1000, max(w[0], w[2]) * 1000,
                    max(w[1], w[3]) * 1000, [(w[0] * 1000, w[1] * 1000), (w[2] * 1000, w[3] * 1000)])
                   for w in outer + inner]
        model = fv._plan_model(members, [0, 0, 84100, 59400])
        flags = [w[4] for w in model["walls"]]
        self.assertEqual(flags[:4], [1, 1, 1, 1])
        self.assertEqual(flags[4:], [0, 0, 0, 0])
        self.assertFalse(model["slab_approximate"])
        self.assertAlmostEqual(model["slab_area"], 200, delta=15)

    def test_storeys_stack_on_drawn_elevations_and_ranges_repeat_a_plan(self):
        plans = [
            {"floor": fv.floor_of("101동 지하 1층 구조평면도"), "sheet": "S-112", "model": {"walls": []}},
            {"floor": fv.floor_of("101동 지상 1층 구조평면도"), "sheet": "S-113", "model": {"walls": []}},
            {"floor": fv.floor_of("101동 지상 2층~지상 4층 구조평면도"), "sheet": "S-115", "model": {"walls": []}},
            {"floor": fv.floor_of("101동 지붕 구조평면도"), "sheet": "S-116", "model": {"walls": []}},
        ]
        table = {"sections": {"101동": {"elevations": {"B1": 10.9, "GL": 16.2, "2": 19.45, "3": 22.3}}}}
        model = fv.build_model(plans, table)
        levels = model["buildings"][0]["levels"]
        self.assertEqual([l["key"] for l in levels], ["B1", "1", "2", "3", "4", "R"])
        self.assertEqual([l["elev"] for l in levels[:4]], [10.9, 16.2, 19.45, 22.3])
        self.assertEqual([l["plan"] for l in levels], [0, 1, 2, 2, 2, 3])
        self.assertAlmostEqual(levels[0]["height"], 5.3)
        self.assertTrue(model["heights_from_drawings"])

    def test_level_files_are_sections_and_elevations_of_the_buildings(self):
        self.assertTrue(fv.is_level_file("A-410,424 주동 단면도.dwg"))
        self.assertTrue(fv.is_level_file("A-407~423 동입면도.dwg"))
        self.assertFalse(fv.is_level_file("A-505~531 코아 단면도.dwg"))
        self.assertFalse(fv.is_level_file("A-401~419 주동 평면도.dwg"))
        self.assertEqual(fv.model_category("0_STR_CONC"), "wall")
        self.assertEqual(fv.model_category("0_STR_CORE"), "wall")
        self.assertIsNone(fv.model_category("0_STR_CONC_BELOW"))
        self.assertEqual(fv.model_category("0_STR_GIRDER-APT"), "beam")
        self.assertEqual(fv.model_category("0_STR_OUTLINE"), "outline")


class _NoThread:
    started = []

    def __init__(self, target=None, args=(), kwargs=None, daemon=None):
        self.args = args

    def start(self):
        _NoThread.started.append(self.args)


class FloorViewJobTests(TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        override = override_settings(QUANTITY_STATE_DB=str(Path(tmp.name) / "state.sqlite3"))
        override.enable()
        self.addCleanup(override.disable)
        self.staff = get_user_model().objects.create_user("staff", password="staff-pass-1", is_staff=True)
        self.client.force_login(self.staff)
        _NoThread.started = []

    def upload(self, name, data=b"0"):
        return SimpleUploadedFile(name, data, content_type="application/octet-stream")

    def test_the_check_starts_the_floor_job_with_plans_and_xrefs_only(self):
        files = [self.upload("A-401~419 주동 평면도.dwg", b"plan"), self.upload("XR-주동 평면도.dwg", b"xref"),
                 self.upload("S-301 아파트 기둥 일람표.dwg", b"schedule")]
        with patch.object(views, "_build_cad_precheck", return_value={"scan": {"cad_count": 3}}), \
                patch.object(views.threading, "Thread", _NoThread):
            response = self.client.post("/api/quantity/check-zip/", {"cad_files": files, "floor_views_job_id": "fv-1"})
        body = response.json()
        self.assertEqual(body["floor_views"], {"started": True, "job_id": "fv-1", "plans": 1})
        job_id, inputs = _NoThread.started[-1]
        self.assertEqual(job_id, "fv-1")
        self.assertEqual(sorted(name for name, _data in inputs), ["A-401~419 주동 평면도.dwg", "XR-주동 평면도.dwg"])
        self.assertEqual(views._quantity_state().get("job", "fv-1")["owner"], str(self.staff.pk))
        self.assertEqual(self.client.post("/api/quantity/progress/?job_id=fv-1").json()["found"], True)

    def test_without_a_plan_no_job_starts(self):
        with patch.object(views, "_build_cad_precheck", return_value={}), \
                patch.object(views.threading, "Thread", _NoThread):
            body = self.client.post("/api/quantity/check-zip/", {
                "cad_files": [self.upload("S-301 아파트 기둥 일람표.dwg")], "floor_views_job_id": "fv-2"}).json()
        self.assertFalse(body["floor_views"]["started"])
        self.assertEqual(_NoThread.started, [])

    def test_the_job_converts_plans_and_their_xrefs_then_renders_apart(self):
        plan_text = "  0\nSECTION\n  1\n..\\Xref\\XR-주동 평면도.dwg\n"
        converted = []

        def fake_convert(path, **_kw):
            converted.append(os.path.basename(path))
            return (plan_text if len(converted) == 1 else "  0\nEOF\n"), {}

        def fake_run(command, **kwargs):
            manifest = json.loads(Path(command[-2]).read_text(encoding="utf-8"))
            self.seen = manifest
            Path(command[-1]).write_text(json.dumps({"views": [{"floor": "1층"}], "warnings": []}), encoding="utf-8")
            return type("Run", (), {"returncode": 0, "stderr": b""})()

        inputs = [("A-401~419 주동 평면도.dwg", b"plan"), ("XR-주동 평면도.dwg", b"xref"),
                  ("XR-지하주차장 평면도.dwg", b"unused"), ("XR-FORM-A(A3).dwg", b"form"),
                  ("A-410,424 주동 단면도.dwg", b"section")]
        with patch.object(views, "_cbl_free_dwg_to_dxf_text_v1", side_effect=fake_convert), \
                patch.object(views.subprocess, "run", side_effect=fake_run):
            views._run_floor_views_job("fv-3", inputs)
        names = sorted(item["name"] for item in self.seen["files"])
        self.assertEqual(names, ["A-401~419 주동 평면도.dwg", "A-410,424 주동 단면도.dwg", "XR-FORM-A(A3).dwg",
                                 "XR-주동 평면도.dwg"])
        self.assertEqual(self.seen["plans"], ["A-401~419 주동 평면도.dwg"])
        self.assertEqual(self.seen["levels"], ["A-410,424 주동 단면도.dwg"])
        result = views._result_get("fv-3")
        self.assertTrue(result["ok"])
        self.assertEqual(result["results"]["floor_views"]["views"], [{"floor": "1층"}])

    def test_a_failed_render_is_reported(self):
        with patch.object(views, "_cbl_free_dwg_to_dxf_text_v1", return_value=("  0\nEOF\n", {})), \
                patch.object(views.subprocess, "run", return_value=type("Run", (), {"returncode": 1, "stderr": b"boom"})()):
            views._run_floor_views_job("fv-4", [("A-401~419 주동 평면도.dwg", b"plan")])
        result = views._result_get("fv-4")
        self.assertFalse(result["ok"])
        self.assertIn("평면도", result["error"])
