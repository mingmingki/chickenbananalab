"""What the check against four sites' manual takeoffs showed and fixed: key maps read as walls,
an unread drawing index dropping every schedule, beam schedules drawn as one box per beam, wall
lists with merged cells and side-by-side copies, window and door blocks known only by their
lines, finish boards taken for dry walls, and beam lines on wall-type floors."""
import ezdxf
from django.test import SimpleTestCase
from ezdxf.math import Matrix44

from core import quantity_arch as qa
from core import quantity_floor_rebar as fr
from core import quantity_floor_takeoff as ft
from core import quantity_floor_views as fv
from core import quantity_schedules as qs
from core import quantity_sheet_identity as sid
from core.test_quantity_sheet_identity import evidence, form_doc, index_host, xref_host


def text(value, x, y, h=120.0):
    return {"text": value, "x": float(x), "y": float(y), "h": float(h), "layer": "T", "handle": f"{value}-{x}-{y}"}


class LayerRuleTests(SimpleTestCase):
    def test_wall_materials_named_in_english_and_finishes_left_out(self):
        self.assertEqual(fv.layer_category("XR-101동$0$A-WALL-MASONRY"), "masonry")
        self.assertEqual(fv.layer_category("A-WALL-GYP"), "dry")
        self.assertEqual(fv.layer_category("A-DRY"), "dry")
        self.assertEqual(fv.layer_category("WAL경량"), "dry")
        self.assertIsNone(fv.layer_category("A-03-마감(석고보드)"))  # a board lining is a finish
        self.assertIsNone(fv.layer_category("경량철골천장틀"))         # a ceiling grid
        self.assertIsNone(fv.layer_category("A-WALL-MASONRY-HATCH"))
        self.assertEqual(fv.layer_category("단위세대-Brick"), "masonry")


class KeyMapTests(SimpleTestCase):
    def test_a_key_map_in_the_sheet_corner_is_not_walked(self):
        self.assertTrue(fv.is_key_map("XR-KEY MAP(오정동)"))
        self.assertTrue(fv.is_key_map("XR-keyplan"))
        self.assertFalse(fv.is_key_map("XR-구조101동 (오정동)"))
        doc = ezdxf.new()
        key_map = doc.blocks.new("XR-KEY MAP(오정동)")
        key_map.add_line((0, 0), (5000, 0), dxfattribs={"layer": "A-CONC"})
        key_map.add_line((0, 300), (5000, 300), dxfattribs={"layer": "A-CONC"})
        msp = doc.modelspace()
        msp.add_blockref("XR-KEY MAP(오정동)", (0, 0), dxfattribs={"layer": "0"})
        msp.add_line((0, 10000), (6000, 10000), dxfattribs={"layer": "A-CONC"})
        out = []
        fv._walk(doc, msp, Matrix44(), None, {}, out)
        self.assertEqual(len(out), 1)  # only the plan's own line


class OpeningByContentTests(SimpleTestCase):
    def doc(self):
        doc = ezdxf.new()
        window = doc.blocks.new("PW-1500(D)")  # named by its type only, drawn on the window layer
        window.add_lwpolyline([(0, 0), (1500, 0), (1500, 200), (0, 200)], close=True, dxfattribs={"layer": "단위세대-WIN"})
        window.add_line((0, 100), (1500, 100), dxfattribs={"layer": "단위세대-WIN"})
        window.add_line((0, 60), (1500, 60), dxfattribs={"layer": "단위세대-WIN"})
        door = doc.blocks.new("SD-800")
        door.add_line((0, 0), (800, 0), dxfattribs={"layer": "DOOR"})
        door.add_line((0, 0), (0, 800), dxfattribs={"layer": "DOOR"})
        door.add_arc((0, 0), 800, 0, 90, dxfattribs={"layer": "DOOR"})
        unit = doc.blocks.new("Xr-66A형 단위세대")  # a unit plan: some window lines among many others
        for i in range(10):
            unit.add_line((0, i * 1000), (12000, i * 1000), dxfattribs={"layer": "단위세대-Conc"})
        unit.add_line((0, 0), (2000, 0), dxfattribs={"layer": "단위세대-WIN"})
        unit.add_blockref("PW-1500(D)", (3000, 0), dxfattribs={"layer": "0"})
        unit.add_blockref("SD-800", (6000, 0), dxfattribs={"layer": "0"})
        msp = doc.modelspace()
        msp.add_blockref("Xr-66A형 단위세대", (0, 0), dxfattribs={"layer": "0"})
        msp.add_blockref("PW-1500(D)", (20000, 0), dxfattribs={"layer": "0"})
        msp.add_blockref("PW-1500(D)", (20000, 0), dxfattribs={"layer": "0"})  # pasted twice on one spot
        return doc

    def test_blocks_known_by_their_lines_are_windows_and_doors(self):
        inserts = qa.opening_inserts(self.doc(), {}, None, fv._is_opening_insert, None, tags=[], layer_kind=fv.layer_category)
        kinds = sorted((name, kind) for name, kind, *_rest in inserts)
        self.assertEqual(kinds, [("PW-1500(D)", "window"), ("PW-1500(D)", "window"), ("PW-1500(D)", "window"),
                                 ("SD-800", "door")])

    def test_without_the_layer_reader_nothing_is_found_by_its_lines(self):
        self.assertEqual(qa.opening_inserts(self.doc(), {}, None, fv._is_opening_insert, None, tags=[]), [])

    def test_a_block_pasted_twice_on_one_spot_counts_once(self):
        inserts = qa.opening_inserts(self.doc(), {}, None, fv._is_opening_insert, None, tags=[], layer_kind=fv.layer_category)
        measured = qa.measure([], (-1000, -1000, 30000, 30000), inserts)
        count = sum(o["count"] for o in measured["openings"] if o["kind"] == "window")
        self.assertEqual(count, 2)  # the unit's window and one of the two pasted copies


class FinishBoardTests(SimpleTestCase):
    def test_board_lining_lines_are_no_dry_wall(self):
        def path(layer, x0, y0, x1, y1):
            points = [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)]
            return ((("dry", layer)), x0, y0, x1, y1, points)
        lining = [path("석고보드", 0, 0, 6000, 30), path("석고보드", 0, 2000, 6000, 2030)]
        self.assertNotIn("dry", qa.measure(lining, (-100, -100, 10000, 10000))["walls"])
        partition = [path("WALL-Gboard", 0, 0, 6000, 100)]
        self.assertIn("dry", qa.measure(partition, (-100, -100, 10000, 10000))["walls"])


class DrawingIndexTests(SimpleTestCase):
    def test_an_index_read_as_only_its_own_number_does_not_drop_the_set(self):
        ev = evidence(**{"XR-FORM-A(A3).dwg": form_doc()}, idx=index_host([[("S-001", "도면목록표", "NONE")]]),
                      beams=xref_host([("S-301", "보 일람표"), ("S-302", "기둥 일람표")]))
        registry = sid.build_sheet_registry(sid.resolve_cad_sheets(ev), ev)
        numbers = [slot["drawing_number"] for slot in registry["sequence"]["S"] if slot.get("cad_sheet")]
        self.assertIn("S-301", numbers)
        self.assertIn("S-302", numbers)
        self.assertTrue(any(i["reason"] == "drawing_index_unread" for i in registry["issues"]))


class BoxScheduleTests(SimpleTestCase):
    def test_one_box_per_beam_with_the_size_under_its_label(self):
        texts = []
        for mark, x, size, top in (("2TG101", 1600, "(900X1400)", "22-HD 25"), ("2TG106", 12490, "(500X1400)", "7-HD 25")):
            texts += [text(mark, x, 45126), text("B(mm) X D(mm)", x - 215, 44385, 90), text(size, x - 130, 43884),
                      text("상     부     근", x - 120, 43179, 90), text(top, x + 2170, 43179, 90),
                      text("-", x + 5590, 43179, 90), text("하     부     근", x - 120, 42939, 90),
                      text("19-HD 25", x + 2170, 42939, 90), text("스     터     럽", x - 120, 42699, 90),
                      text("ST. 6-HD 13 @ 125", x + 1870, 42699, 90)]
        tables = {t["mark"]: t for t in qs.read_tables(texts)}
        self.assertEqual(set(tables), {"2TG101", "2TG106"})
        values = tables["2TG101"]["rows"][0]["values"]
        self.assertEqual(tables["2TG101"]["category"], "beams")
        self.assertEqual(values["size"], "(900X1400)")
        self.assertEqual(values["top_bar"], "22-HD 25")
        self.assertEqual(qs.parse_spaced(values["stirrup"]), (13, 125))
        self.assertEqual(tables["2TG106"]["rows"][0]["values"]["top_bar"], "7-HD 25")


class WallListTests(SimpleTestCase):
    def texts(self):
        out = [text(v, x, 50108) for v, x in (("WALL", 1646), ("층", 3000), ("두께", 4011), ("수직철근", 4885),
                                               ("CLOSED HOOP", 5774), ("수평철근", 7145))]
        # W101: three floor rows, the mark on the middle one, the horizontal bars between rows
        out += [text("7F ~ 15F", 2741, 49658), text("12 - D10", 4859, 49658), text("D10 @140", 7069, 49545),
                text("W101", 1664, 49433), text("2F ~ 6F", 2785, 49433), text("200", 4024, 49433), text("12 - D13", 4859, 49433),
                text("B2 ~ 1F", 2779, 49208), text("12 - D16", 4859, 49208), text("D10 @120", 7069, 49208),
                text("W102", 1650, 48758), text("B2 ~ 15F", 2773, 48758), text("200", 4024, 48758),
                text("D16 @100", 4809, 48758), text("D10 @120", 7069, 48758)]
        # the same list drawn again beside the header, its first rows above the header
        for dx in (7126,):
            out += [text("7F ~ 15F", 2795 + dx, 52133), text("D10 @450", 4863 + dx, 52133),
                    text("CW103", 1646 + dx, 51910), text("4F ~ 6F", 2839 + dx, 51910), text("200", 4078 + dx, 51910),
                    text("D10 @300", 4863 + dx, 51910), text("B2 ~ 3F", 2833 + dx, 51683), text("D10 @150", 4863 + dx, 51683),
                    text("12F ~ 15F", 2833 + dx, 51250), text("D10 @300", 4863 + dx, 51250),
                    text("CW104", 1646 + dx, 51061), text("B2 ~ 11F", 2833 + dx, 51061), text("200", 4078 + dx, 51061),
                    text("D13 @150", 4865 + dx, 51061), text("D10 @280", 7123 + dx, 51061)]
        return out

    def test_a_mark_spans_its_floor_rows_and_copies_beside_read_with_the_header(self):
        tables = {t["mark"]: t for t in qs.read_tables(self.texts())}
        self.assertEqual(set(tables), {"W101", "W102", "CW103", "CW104"})
        w101 = tables["W101"]["rows"]
        self.assertEqual([r["levels_text"] for r in w101], ["7F ~ 15F", "2F ~ 6F", "B2 ~ 1F"])
        self.assertEqual(w101[0]["values"]["horizontal_bar"], "D10 @140")  # the merged cell fills the row above
        self.assertEqual(w101[2]["values"]["vertical_bar"], "12 - D16")
        self.assertEqual(tables["W101"]["category"], "walls")
        cw103 = tables["CW103"]["rows"]
        self.assertEqual([r["levels_text"] for r in cw103], ["7F ~ 15F", "4F ~ 6F", "B2 ~ 3F"])
        self.assertEqual(cw103[1]["values"]["vertical_bar"], "D10 @300")
        self.assertNotIn("col_7f~15f", tables["W101"]["labels"])


class WallTypeBeamTests(SimpleTestCase):
    def links(self, unlabelled_mm, measured_mm):
        return {"plans": [{"drawing_number": "S-105", "kind": "floor_plan", "section": "101동",
                           "geometry": {"beams": {"measured_mm": measured_mm, "labelled_mm": measured_mm - unlabelled_mm,
                                                  "unlabelled": {"runs": 5, "length_mm": unlabelled_mm, "widths": {"400": unlabelled_mm}}}}}],
                "marks": [{"category": "beams", "mark": "LB0", "specs": [], "occurrences": [
                    {"plan": 0, "count": 30, "measured": {"widths": {"500": measured_mm - unlabelled_mm}}}]},
                          {"category": "beams", "mark": "G1", "specs": [], "occurrences": []}],
                "schedule_rows": []}

    def test_mostly_unmarked_beam_lines_are_left_out(self):
        members = ft._link_members(self.links(856000, 1075000), "101동", "S-105")
        self.assertEqual(members["beams"], [])  # the unmarked runs and the depthless LB0 runs
        self.assertAlmostEqual(members["left_out_unlabelled_m"], 1075.0)

    def test_a_beam_framed_plan_keeps_its_unmarked_runs(self):
        members = ft._link_members(self.links(100000, 1200000), "101동", "S-105")
        self.assertEqual(sorted(b["mark"] or "" for b in members["beams"]), ["", "LB0"])
        self.assertEqual(members["left_out_unlabelled_m"], 0)


class WallRebarTests(SimpleTestCase):
    def test_walls_marked_on_the_floor_plan_and_bars_listed_by_count(self):
        links = {"plans": [{"drawing_number": "S-105", "kind": "floor_plan", "section": "101동"}],
                 "marks": [{"category": "walls", "mark": "W101", "typical_plans": [],
                            "occurrences": [{"plan": 0, "count": 2, "measured": {"widths": {"200": 3000}}}]}]}
        self.assertEqual(fr._wall_runs(links, "101동", "S-105"), [("W101", 3.0, 0.2)])
        tally = fr._Tally(None)
        fr._wall(tally, {"vertical_bar": "12 - D13", "horizontal_bar": "D10 @150"}, 3.0, 0.2, 2.65, False)
        self.assertGreater(tally.kg["wall"], 0)
        vertical = tally.total()
        tally = fr._Tally(None)
        fr._wall(tally, {"horizontal_bar": "D10 @150"}, 3.0, 0.2, 2.65, False)
        self.assertGreater(vertical, tally.total())  # the counted vertical bars are in


class AnonymousBlockTests(SimpleTestCase):
    def test_a_pasted_group_is_not_taken_for_one_opening_by_its_lines(self):
        doc = ezdxf.new()
        group = doc.blocks.new("A$C2CEE7391")
        group.add_line((0, 0), (900, 0), dxfattribs={"layer": "A-DOOR"})
        group.add_line((0, 0), (0, 900), dxfattribs={"layer": "A-DOOR"})
        group.add_arc((0, 0), 900, 0, 90, dxfattribs={"layer": "A-DOOR"})
        doc.modelspace().add_blockref("A$C2CEE7391", (0, 0), dxfattribs={"layer": "0"})
        self.assertEqual(qa.opening_inserts(doc, {}, None, fv._is_opening_insert, None, tags=[], layer_kind=fv.layer_category), [])


class SectionLevelTests(SimpleTestCase):
    def test_slab_levels_written_sl_and_legends_and_typos(self):
        from core import quantity_levels as ql
        self.assertEqual(ql.parse_elevation("SL +4.70"), 4.7)
        self.assertEqual(ql.parse_elevation("FL : +1.30"), 1.3)
        self.assertEqual(ql.parse_elevation("GL±0=EL+16.20"), 16.2)
        self.assertIsNone(ql.parse_elevation("GL =  EL+ 9.70 / 1FL+10.30"))  # a legend naming several values
        self.assertIsNone(ql.level_key("101층 도로"))                         # 101동 mistyped
        texts = [text("SL +15.70", 10550, 27523, 450), text("지상2층", 9000, 27523, 450),
                 text("SL +52.10", 10550, 63923, 450), text("지상15층", 9000, 63923, 450),
                 text("SL +55.10", 10550, 66923, 450), text("지붕", 9000, 66923, 450)]
        self.assertEqual(ql.read_sheet_levels(texts)["elevations"], {"2": 15.7, "15": 52.1, "R": 55.1})

    def test_unmarked_typical_floors_share_the_gap(self):
        from core import quantity_levels as ql
        heights, _rejected = ql._storeys({"1": 11.6, "2": 15.7, "15": 52.1, "R": 55.1})
        self.assertEqual(heights["1"], 4.1)
        self.assertEqual(heights["15"], 3.0)
        self.assertTrue(all(heights[str(f)] == 2.8 for f in range(2, 15)))
        table = {"sections": {"101동": {"elevations": {"1": 11.6, "2": 15.7, "15": 52.1}}}}
        elevations = fv._elevations(["1"] + [str(f) for f in range(2, 16)] + ["R"], table, "101동")
        self.assertEqual(elevations["3"], 18.5)
        self.assertEqual(round(elevations["14"] - elevations["13"], 2), 2.8)

    def test_a_deck_under_sloping_ground_keeps_a_basement_height(self):
        table = {"sections": {"공통": {"elevations": {"B2": 1.1, "B1": 4.7, "1": 10.9}}}}
        elevations = fv._elevations(["B2", "B1", "R"], table, "지하주차장")
        self.assertEqual(round(elevations["R"] - elevations["B1"], 2), fv.DEFAULT_STOREY_M["basement"])
        flat = {"sections": {"공통": {"elevations": {"B2": 1.1, "B1": 4.7, "1": 8.4}}}}
        self.assertEqual(fv._elevations(["B2", "B1", "R"], flat, "지하주차장")["R"], 8.4)
