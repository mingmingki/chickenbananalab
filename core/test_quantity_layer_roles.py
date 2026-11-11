"""Reading any office's drawings: sheet frames without the form file, and layer roles from geometry."""
from django.test import SimpleTestCase

from core import quantity_layer_roles as lr
from core import quantity_sheet_identity as identity


def text(value, x, y, h=300.0, handle=None):
    return {"text": value, "x": x, "y": y, "h": h, "layer": "TXT", "handle": handle or f"{value}-{x}-{y}"}


class SheetNumberTests(SimpleTestCase):
    def test_numbers_with_digits_before_the_dash_are_sheet_numbers(self):
        for raw, want in (("S-111", "S-111"), ("S02 - 001", "S02-001"), ("S1-511", "S1-511"), ("A03-101", "A03-101")):
            self.assertEqual(identity.normalize_number(raw), want)
        for raw in ("X9", "C1", "2024. 12.", "S2"):
            self.assertIsNone(identity.normalize_number(raw))


class MissingFormTests(SimpleTestCase):
    def evidence(self, form_name="XR-Form(현장)"):
        inserts = [{"name": form_name.lower(), "raw_name": form_name, "xref": True, "handle": f"F{i}",
                    "x": i * 84100.0, "y": 0.0, "sx": 100.0, "sy": 100.0, "rotation": 0.0} for i in range(3)]
        texts = []
        for i, floor in enumerate(("지하1층", "지상1층", "지상2층~15층")):
            texts.append(text(f"101동 {floor} 구조평면도", i * 84100 + 30000, 6000, h=700))
            texts.append(text(f"101동 {floor} 구조평면도", i * 84100 + 76000, 9800))
            texts.append(text(f"S02 - 00{i + 1}", i * 84100 + 78000, 2400))
        return {"inserts": inserts, "texts": texts, "templates": [], "block_texts": []}

    def test_frames_come_from_the_spacing_of_the_form_copies(self):
        rows = identity.resolve_cad_sheets({"plan.dwg": self.evidence()})["plan.dwg"]
        self.assertEqual([(r["drawing_number"], r["drawing_title"]) for r in rows],
                         [("S02-001", "101동 지하1층 구조평면도"), ("S02-002", "101동 지상1층 구조평면도"),
                          ("S02-003", "101동 지상2층~15층 구조평면도")])
        self.assertEqual([round(v) for v in rows[0]["frame_bbox"]], [0, 0, 84100, round(84100 / 2 ** 0.5)])

    def test_without_numbers_titles_still_make_sheets(self):
        evidence = self.evidence("선화B구역_form")
        evidence["texts"] = [t for t in evidence["texts"] if "-" not in t["text"]]
        rows = identity.resolve_cad_sheets({"plan.dwg": evidence})["plan.dwg"]
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[1]["drawing_title"], "101동 지상1층 구조평면도")
        self.assertIsNone(rows[1]["drawing_number"])

    def test_a_one_sheet_drawing_without_a_form_is_one_frame(self):
        evidence = {"inserts": [], "templates": [], "block_texts": [],
                    "texts": [text("지하2층 주차장 구조평면도", 500, 100, h=800), text("주기", 0, 0, h=100)]}
        rows = identity.resolve_cad_sheets({"S-053.dwg": evidence})["S-053.dwg"]
        self.assertEqual([r["drawing_title"] for r in rows], ["지하2층 주차장 구조평면도"])
        self.assertEqual(rows[0]["method"], "whole_drawing")


def pair(x0, y0, length, width, vertical=False):
    """Two parallel faces (a wall or a beam) as point lists in mm."""
    if vertical:
        return [[(x0, y0), (x0, y0 + length)], [(x0 + width, y0), (x0 + width, y0 + length)]]
    return [[(x0, y0), (x0 + length, y0)], [(x0, y0 + width), (x0 + length, y0 + width)]]


class LayerRoleTests(SimpleTestCase):
    def items(self, wall_layer="A-콘크리트", below_layer="6A", beam_layer="X1"):
        items = []
        for k in range(12):  # walls 200 mm thick, cut (solid)
            for line in pair(k * 3000, 0, 2500, 200, vertical=bool(k % 2)):
                items.append((wall_layer, "solid", line))
                items.append((below_layer, "dashed", line))  # the walls below lie under the walls
        for k in range(8):  # beams 400 mm wide, hidden, between the walls
            for line in pair(k * 3000 + 300, 6000, 2400, 400):
                items.append((beam_layer, "dashed", line))
        for k in range(10):  # columns: closed outlines
            x, y = k * 2000, 12000
            items.append(("C-COL", "solid", [(x, y), (x + 500, y), (x + 500, y + 800), (x, y + 800), (x, y)]))
        for k in range(40):  # stair treads: a ladder of equally spaced lines
            items.append(("STAIR", "solid", [(0, 20000 + k * 260), (1200, 20000 + k * 260)]))
            items.append(("A-HATCH-X", "solid", [(5000, 20000 + k * 100), (9000, 20000 + k * 100)]))
        for k in range(30):  # dry walls look like walls but the name rules them out
            for line in pair(k * 1000, 30000, 900, 120):
                items.append(("DRY_WALL", "solid", line))
        items.append(("CEN", "center", [(-5000, 0), (60000, 0)]))
        return items

    def test_geometry_decides_with_the_name_as_a_hint(self):
        roles = {layer: info["role"] for layer, info in lr.judge(self.items()).items()}
        self.assertEqual(roles["A-콘크리트"], "wall")
        self.assertEqual(roles["6A"], "wall_below")      # hidden pairs on the walls, no name hint
        self.assertEqual(roles["X1"], "beam")             # hidden pairs off the walls
        self.assertEqual(roles["C-COL"], "column")
        self.assertEqual(roles["STAIR"], "stair")
        self.assertIsNone(roles["A-HATCH-X"])
        self.assertIsNone(roles["DRY_WALL"])
        self.assertNotIn("CEN", [layer for layer, role in roles.items() if role])

    def test_an_unnamed_layer_with_wall_geometry_is_a_wall_by_geometry(self):
        judged = lr.judge(self.items(wall_layer="0"))
        self.assertEqual(judged["0"]["role"], "wall")
        self.assertEqual(judged["0"]["by"], "geometry")

    def test_a_persons_answer_in_this_job_wins(self):
        judged = lr.judge(self.items(), known={"X1": "wall_above"})
        self.assertEqual(judged["X1"]["role"], "wall_above")
        self.assertEqual(judged["X1"]["by"], "person")

    def test_xref_prefixes_and_linetypes(self):
        self.assertEqual(lr.base_name("XR-구조101동 (오정동)$0$A-하부벽체"), "A-하부벽체")
        self.assertEqual(lr.linetype_class("XR-구조101동$0$HID"), "dashed")
        self.assertEqual(lr.linetype_class("CENTER2"), "center")
        self.assertEqual(lr.linetype_class("Continuous"), "solid")


class FloorTitleAndColourTests(SimpleTestCase):
    def test_floor_ranges_without_the_first_storey_word(self):
        from core import quantity_floor_views as fv
        for title, floor in (("102동 4~23층 구조평면도", "4~23층"), ("102동 25-27층 구조평면도", "25~27층"),
                             ("101동 지상3층~15층 구조평면도", "3~15층"), ("101동 지하 2층 구조평면도", "지하2층")):
            self.assertEqual(fv.floor_of(title)["floor"], floor)

    def test_structural_plans_are_coloured_by_the_judged_roles(self):
        import ezdxf
        from core import quantity_floor_views as fv
        doc = ezdxf.new()
        msp = doc.modelspace()
        for k in range(6):
            msp.add_line((k * 3000, 0), (k * 3000 + 2500, 0), dxfattribs={"layer": "WAL"})
            msp.add_line((k * 3000, 200), (k * 3000 + 2500, 200), dxfattribs={"layer": "WAL"})
        rows = [{"frame_bbox": [-1000, -1000, 20000, 5000], "drawing_title": "101동 3층 구조평면도", "drawing_number": "S-1"}]
        self.assertEqual(fv._plan_views("s.dwg", doc, rows, {}), [])  # the name rules do not know "WAL"
        views = fv._plan_views("s.dwg", doc, rows, {}, {"WAL": "wall"})
        self.assertGreater(views[0]["counts"]["frame"], 0)


class SheetAndFoundationTests(SimpleTestCase):
    def test_title_block_and_foundation_layers_are_never_members(self):
        self.assertTrue(lr.from_sheet("칠성24지구 sheet$0$동우CI_1"))
        self.assertTrue(lr.surely_not_member("XR-FORM(현장)$0$COL"))
        self.assertTrue(lr.surely_not_member("0_기초"))
        # the wall marks' frames (boxes round "W1" and corner ticks), not walls
        self.assertTrue(lr.surely_not_member("0_벽체부호"))
        self.assertTrue(lr.surely_not_member("S-COL-MARK"))
        self.assertFalse(lr.surely_not_member("0_벽체"))
        self.assertFalse(lr.from_sheet("XR-구조101동$0$A-CONC"))


class WallShapeTests(SimpleTestCase):
    def test_long_wall_faces_cut_into_pieces_are_not_a_ladder(self):
        items = []
        for k in range(10):  # walls 250 mm thick whose faces are cut every 1.5 m (openings, crossings)
            y = k * 4000
            for x in range(0, 12000, 1500):
                items.append(("C", "solid", [(x, y), (x + 1400, y)]))
                items.append(("C", "solid", [(x, y + 250), (x + 1400, y + 250)]))
        judged = lr.judge(items)
        self.assertEqual(judged["C"]["role"], "wall")

    def test_closed_outlines_shaped_like_wall_pieces_are_walls_not_columns(self):
        from core import quantity_floor_views as fv
        strip = [(0, 0), (3000, 0), (3000, 200), (0, 200), (0, 0)]           # a 200 mm wall piece
        column = [(5000, 0), (5600, 0), (5600, 600), (5000, 600), (5000, 0)]  # a 600 mm column
        members = [("col", min(p[0] for p in pts), min(p[1] for p in pts), max(p[0] for p in pts), max(p[1] for p in pts), pts)
                   for pts in (strip, column)]
        model = fv._plan_model(members, [-1000, -1000, 20000, 10000])
        self.assertEqual(len(model["cols"]), 1)
        self.assertEqual(len(model["walls"]), 4)
        self.assertGreater(model["measure"]["area"], 0.5)  # the strip is measured as wall section


class ParkingRoofTitleTests(SimpleTestCase):
    def test_a_towers_parking_deck_roof_plan_is_its_first_floor(self):
        from core import quantity_floor_views as fv
        self.assertEqual(fv.floor_of("101동 지하주차장지붕 구조평면도")["floor"], "1층")
        self.assertEqual(fv.floor_of("102동 지하주차장 지붕 구조평면도")["floor"], "1층")
        self.assertEqual(fv.floor_of("지붕 주차장 구조평면도")["floor"], "지붕·옥탑")  # the deck itself
        self.assertEqual(fv.floor_of("101동 지붕 구조평면도")["floor"], "지붕·옥탑")


class SiteDeckTitleTests(SimpleTestCase):
    def test_untitled_basement_sheets_of_a_tower_project_are_the_parking_deck(self):
        from core import quantity_floor_views as fv
        floors = [fv.floor_of(t) for t in ("101동 3층 구조평면도", "지하1층 구조평면도", "지상1층 구조평면도", "지하2층 기초구조평면도")]
        self.assertTrue(fv.site_deck_titles(floors))
        self.assertEqual(fv._as_deck(floors[2])["floor"], "지붕·옥탑")
        self.assertEqual(fv._as_deck(floors[1])["building"], "지하주차장")
        # one building without a name: its sheets stay together
        single = [fv.floor_of(t) for t in ("지하1층 구조평면도", "지상1층 구조평면도", "지상2층 구조평면도")]
        self.assertFalse(fv.site_deck_titles(single))


class FloorCaptionTests(SimpleTestCase):
    def test_a_title_block_name_without_a_floor_gives_way_to_the_caption(self):
        evidence = {"inserts": [], "templates": [], "block_texts": [],
                    "texts": [text("101동 바닥 구조 평면도 3", 500, 100, h=800),
                              text("101동 3~25층 바닥 구조 평면도", 300, 400, h=500)]}
        rows = identity.resolve_cad_sheets({"S-205.dwg": evidence})["S-205.dwg"]
        self.assertEqual(rows[0]["drawing_title"], "101동 3~25층 바닥 구조 평면도")
        self.assertEqual(rows[0]["sheet_title"], "101동 바닥 구조 평면도 3")
