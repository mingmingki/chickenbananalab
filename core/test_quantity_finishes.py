"""Rooms from the architectural plans and their finishes (floor, wall, ceiling, baseboard,
waterproofing, plaster)."""
import json

from django.test import SimpleTestCase

from core import quantity_arch_finishes as fin
from core import quantity_arch_rooms as rooms_of


def rect(x0, y0, x1, y1):
    return [(x0, y0, x1, y0), (x1, y0, x1, y1), (x1, y1, x0, y1), (x0, y1, x0, y0)]


def plan(door=True):
    """A 10 x 6 m flat: concrete outer walls (200) with a 2 m window in the south wall, a masonry
    partition (100) at x = 6 m with a 0.9 m door; 거실 on the left, 욕실 on the right."""
    frame = rect(0, 0, 2, 0.2) + rect(4, 0, 10, 0.2) + rect(0, 5.8, 10, 6) + rect(0, 0.2, 0.2, 5.8) + rect(9.8, 0.2, 10, 5.8)
    masonry = rect(6.0, 0.2, 6.1, 1.0) + rect(6.0, 1.9, 6.1, 5.8)
    openings = [("AW1", "window", 2.0, 0.0, 2.0)] + ([("WD1", "door", 6.05, 1.0, 0.9)] if door else [])
    names = [("거실", 3.0, 3.0), ("욕실", 8.0, 3.0)]
    return {"frame": frame, "masonry": masonry}, openings, names


class RoomTests(SimpleTestCase):
    def test_the_walls_enclose_two_rooms_with_their_names_openings_and_wall_kinds(self):
        found = rooms_of.find_rooms(*plan())
        self.assertEqual(len(found), 2)
        living, bath = found
        self.assertEqual((living["names"], living["class"], bath["names"], bath["class"]), (["거실"], "room", ["욕실"], "wet"))
        self.assertAlmostEqual(living["area"], 5.8 * 5.6, delta=0.5)
        self.assertAlmostEqual(bath["area"], 3.7 * 5.6, delta=0.4)
        self.assertAlmostEqual(bath["perimeter"], 2 * (3.7 + 5.6), delta=0.4)
        self.assertAlmostEqual(bath["faces"]["masonry"], 5.6 - 0.9, delta=0.3)  # the plaster goes on these
        self.assertEqual(sorted(o["name"] for o in living["openings"]), ["AW1", "WD1"])
        self.assertEqual([o["name"] for o in bath["openings"]], ["WD1"])
        json.dumps(found)  # kept with the floor job

    def test_a_gap_without_a_door_stays_open(self):
        found = rooms_of.find_rooms(*plan(door=False))
        self.assertEqual(len(found), 1)  # an open plan: one room with both names
        self.assertEqual(sorted(found[0]["names"]), ["거실", "욕실"])

    def test_room_names(self):
        self.assertEqual([rooms_of.room_class([n]) for n in ("거실", "침실2", "욕실1", "PS", "발코니", "현관", "계단실", "전기실", "창고")],
                         ["room", "room", "wet", "shaft", "balcony", "entrance", "common", "service", "service"])
        # a shaft's label inside a room another name names: the room, not a shaft; rooms a missing door
        # left open into one space: the class most of the names have (the rooms on a tie)
        self.assertEqual([rooms_of.room_class(n) for n in (["AD", "발코니"], ["거실", "PS"], ["PS", "DA"], ["욕실2", "PS"],
                                                            ["침실3", "발코니1", "침실1", "현관", "주방/식당", "PS", "거실"],
                                                            ["PS", "ELEV", "ELEV.홀", "발코니2"], ["발코니", "거실"])],
                         ["balcony", "room", "shaft", "wet", "room", "common", "room"])
        self.assertEqual([rooms_of.is_room_name(t) for t in ("거실", "주방/식당", "D10@200", "101동", "2,000 x 1,500")],
                         [True, True, False, False, False])


class FinishTests(SimpleTestCase):
    def sizes(self, name, kind, width):
        return {"AW1": (2.0, 1.5), "WD1": (0.9, 2.1)}.get(name, (width, 2.1))

    def test_each_room_takes_the_finishes_of_its_kind(self):
        found = rooms_of.find_rooms(*plan())
        done = fin.storey_finishes(found, clear=2.64, sizes=self.sizes)
        living, bath = found
        self.assertAlmostEqual(done["floor"]["강마루"], living["area"], places=1)
        self.assertAlmostEqual(done["floor"]["바닥타일"], bath["area"], places=1)
        self.assertAlmostEqual(done["ceiling"]["욕실 천장재"], bath["area"], places=1)
        # the living room's walls up to the 2.3 m ceiling, less the window (2 x 1.5) and the door (0.9 x 2.1)
        self.assertAlmostEqual(done["wall"]["벽지"], living["perimeter"] * 2.3 - 2.0 * 1.5 - 0.9 * 2.1, places=1)
        self.assertAlmostEqual(done["wall"]["벽타일"], bath["perimeter"] * 2.3 - 0.9 * 2.1, places=1)
        self.assertAlmostEqual(done["base"], living["perimeter"] - 0.9, places=1)  # baseboard in the living room only
        self.assertAlmostEqual(done["waterproof"], bath["area"] + bath["perimeter"] * 1.2, places=1)
        self.assertGreater(done["plaster"], 0)  # the masonry partition's faces

    def test_the_ceiling_never_rises_above_the_clear_height_and_unnamed_rooms_are_kept_out(self):
        found = rooms_of.find_rooms(*plan())
        low = fin.storey_finishes(found, clear=2.0, sizes=self.sizes, ceiling_h=2.3)
        self.assertEqual(low["height"], 2.0)
        found[1]["class"], found[1]["names"] = None, []
        kept_out = fin.storey_finishes(found, clear=2.64, sizes=self.sizes)
        self.assertEqual(kept_out["unnamed"]["count"], 1)
        self.assertNotIn("바닥타일", kept_out["floor"])
        total = fin.add_up([kept_out, kept_out])
        self.assertAlmostEqual(total["floor"]["강마루"], 2 * kept_out["floor"]["강마루"], places=1)
        self.assertEqual(total["unnamed"]["count"], 2)


class FinishTakeoffTests(SimpleTestCase):
    def views(self):
        from core import quantity_floor_views as fv
        from core.test_quantity_arch import arch_drawing, form_doc
        from core.test_quantity_sheet_identity import evidence
        doc = arch_drawing()
        msp = doc.modelspace()
        for i in range(2):  # room names on both floors: the left room (west of the masonry) and the rest
            ox, oy = i * 841 * 200.0 + 40 * 200.0, 60 * 200.0
            msp.add_text("침실", dxfattribs={"insert": (ox + 3000, oy + 9000), "height": 300, "layer": "A-ROOM"})
            msp.add_text("거실", dxfattribs={"insert": (ox + 15000, oy + 11000), "height": 300, "layer": "A-ROOM"})
        docs = {"A-101~102 101동 평면도.dxf": doc, "XR-FORM-A(A3).dxf": form_doc()}
        return fv.build_views(docs, evidence(**docs))["views"]

    def test_the_takeoff_gives_finishes_per_storey_and_lists_what_it_assumed(self):
        from core import quantity_arch_takeoff as at
        from core.test_quantity_arch import ArchTakeoffTests
        views = self.views()
        result = at.build(views, ArchTakeoffTests().model())
        building, = result["buildings"]
        row = building["rows"][2]
        done = row["finishes"]
        self.assertEqual(sorted(r["names"][0] for r in done["rooms"] if r["class"]), ["거실", "침실"])
        self.assertAlmostEqual(done["floor"]["강마루"], 6.8 * 14.6 + 22.7 * 14.6, delta=6)  # the floor inside the outer walls
        self.assertGreater(done["wall"]["벽지"], 0)
        totals = building["totals"]["finishes"]
        self.assertAlmostEqual(totals["floor"]["강마루"], 5 * done["floor"]["강마루"], delta=1)
        keys = {a["key"] for a in result["assumed"]}
        self.assertTrue({"ceiling_height", "finish_rules"} <= keys)
        taller = at.build(views, ArchTakeoffTests().model(), overrides={"ceiling_height": 2.4})
        self.assertGreater(taller["buildings"][0]["rows"][2]["finishes"]["wall"]["벽지"], done["wall"]["벽지"])
        self.assertFalse(any("마감 (건축 평면에서" in text for text in result["not_included"]))


class FinishExcelTests(SimpleTestCase):
    def test_the_excel_gets_the_finish_sheets(self):
        import io
        import openpyxl
        from django.contrib.auth import get_user_model
        from django.test import RequestFactory
        from core import quantity_arch_takeoff as at
        from core import quantity_views as views
        from core.test_quantity_arch import ArchTakeoffTests
        arch = json.loads(json.dumps(at.build(FinishTakeoffTests().views(), ArchTakeoffTests().model())))
        request = RequestFactory().post("/api/quantity/download-excel/", json.dumps({"results": {"floor_arch": arch}}),
                                        content_type="application/json")
        request.user = get_user_model()(is_staff=True)
        workbook = openpyxl.load_workbook(io.BytesIO(views._build_excel_response(request).content))
        self.assertTrue({"마감 집계(층별)", "실별 면적"} <= set(workbook.sheetnames))
        rooms = [r for r in workbook["실별 면적"].iter_rows(min_row=2, values_only=True)]
        self.assertEqual(sorted({r[2] for r in rooms if r[1] == "3층"}), ["거실", "침실"])
        self.assertEqual({r[6] for r in rooms}, {"강마루"})
        headers = [c.value for c in next(workbook["마감 집계(층별)"].iter_rows(min_row=4, max_row=4))]
        self.assertIn("바닥: 강마루(㎡)", headers)


class DoorBesideAWallTests(SimpleTestCase):
    def test_a_door_between_a_wall_end_and_a_wall_across_closes_the_room(self):
        # a 2 x 2 m bathroom in the corner: its partition stops 0.9 m short of the wall across, the door there
        frame = rect(0, 0, 6, 0.2) + rect(0, 3.8, 6, 4) + rect(0, 0.2, 0.2, 3.8) + rect(5.8, 0.2, 6, 3.8)
        dry = rect(2.2, 0.2, 2.3, 1.3) + rect(0.2, 2.2, 2.3, 2.3)   # the bath's east wall ends at y 1.3; the door 1.3..2.2
        openings = [("WD1", "door", 2.25, 1.3, 0.9)]
        names = [("욕실", 1.2, 1.2), ("거실", 4.0, 2.5)]
        found = rooms_of.find_rooms({"frame": frame, "dry": dry}, openings, names)
        by_name = {r["names"][0]: r for r in found if r["names"]}
        self.assertEqual(set(by_name), {"욕실", "거실"})
        self.assertAlmostEqual(by_name["욕실"]["area"], 2.0 * 2.0, delta=0.25)


class FinishPageTests(SimpleTestCase):
    def test_the_arch_card_and_the_summary_show_the_finishes(self):
        from pathlib import Path
        html = (Path(__file__).resolve().parent / "templates" / "core" / "home.html").read_text(encoding="utf-8")
        self.assertIn("function archFinishTable(building, arch) {", html)
        self.assertIn("function archFinishSource(arch) {", html)  # whether the 실내재료마감표 was used
        self.assertIn("[bar, table, types, checkBox, finish]", html)
        self.assertIn('<span>마감 (바닥)</span>', html)
        self.assertIn('"CAD로 층별 수량·철근·토공·외부와 건축 벽체·창호·마감을 냈어요."', html)


# ── the finish schedule (실내재료마감표) ──

def finish_table(x0=0.0, y0=200.0, rows=None, with_height=True):
    """[(text, x, y, h)] of a finish schedule table: a header row and a row per room, cells of several lines."""
    out = []
    columns = [("실명", 10), ("바닥", 50), ("걸레받이", 90), ("벽", 130), ("천장", 170)] + ([("천장고", 205)] if with_height else [])
    for text, x in columns:
        out.append((text, x0 + x, y0, 3.0))
    rows = rows or [
        ("거실/주방", ["강마루 THK7.5", "시멘트몰탈 THK40"], ["걸레받이"], ["실크벽지"], ["천장지"], ["2,300"]),
        ("침실1", ["강마루"], ["걸레받이"], ["합지벽지"], ["천장지"], ["2,300"]),
        ("욕실(공용)", ["포세린타일 300x300", "액체방수 2회"], ["-"], ["벽타일 300x600"], ["SMC 천장재"], ["2,200"]),
        ("발코니", ["바닥타일", "액체방수"], ["-"], ["수성페인트 2회"], ["수성페인트"], [""]),
    ]
    for i, (room, *cells) in enumerate(rows):
        y = y0 - 20 - i * 20
        out.append((room, x0 + 10, y, 3.0))
        for (_header, x), lines in zip(columns[1:], cells):
            for j, line in enumerate(lines):
                if line:
                    out.append((line, x0 + x, y + 3 - j * 5, 2.5))
    return out


class FinishScheduleTests(SimpleTestCase):
    def test_the_rows_give_each_rooms_finishes_and_ceiling_height(self):
        from core import quantity_arch_finish_schedule as fs
        rooms = fs.read_finish_schedule(finish_table(), "A-701")["rooms"]
        self.assertEqual(set(rooms), {"거실", "주방", "침실", "욕실", "발코니"})
        living = rooms["거실"]
        self.assertEqual((living["floor"], living["base"], living["wall"], living["ceiling"]), ("강마루", "걸레받이", "실크벽지", "천장지"))
        self.assertIs(rooms["주방"], living)  # one row for both names
        bath = rooms["욕실"]
        self.assertEqual((bath["floor"], bath["base"], bath["wall"], bath["ceiling"]), ("포세린타일", None, "벽타일", "SMC 천장재"))
        self.assertTrue(bath["waterproof"])
        self.assertEqual((bath["ceiling_height"], living["ceiling_height"], rooms["발코니"]["ceiling_height"]), (2.2, 2.3, None))
        self.assertFalse(living["waterproof"])
        self.assertEqual(fs.match({"rooms": rooms}, ["침실2"])["wall"], "합지벽지")
        self.assertEqual(fs.room_keys("욕실1(공용), 파우더룸"), ["욕실", "파우더룸"])

    def test_two_tables_on_one_sheet_and_the_file_name(self):
        from core import quantity_arch_finish_schedule as fs
        shared = finish_table(x0=300, rows=[("계단실", ["화강석"], ["화강석"], ["수성페인트"], ["텍스"], ["2,400"])])
        rooms = fs.read_finish_schedule(finish_table() + shared)["rooms"]
        self.assertEqual((rooms["계단실"]["floor"], rooms["계단실"]["ceiling"]), ("화강석", "텍스"))
        self.assertEqual(rooms["거실"]["floor"], "강마루")
        self.assertEqual([fs.is_finish_schedule_file(n) for n in ("A-701 실내재료마감표.dwg", "실내마감표.dxf", "A-501 창호도.dwg")],
                         [True, True, False])

    def test_the_schedule_replaces_the_finishes_picked_by_name(self):
        from core import quantity_arch_finish_schedule as fs
        schedule = fs.read_finish_schedule(finish_table(), "A-701")
        found = rooms_of.find_rooms(*plan())
        living, bath = found
        done = fin.storey_finishes(found, clear=2.64, sizes=FinishTests().sizes, schedule=schedule)
        self.assertEqual(sorted(done["floor"]), ["강마루", "포세린타일"])
        self.assertEqual(sorted(done["wall"]), ["벽타일", "실크벽지"])
        self.assertEqual(sorted(done["ceiling"]), ["SMC 천장재", "천장지"])
        self.assertAlmostEqual(done["wall"]["벽타일"], bath["perimeter"] * 2.2 - 0.9 * 2.1, places=1)  # its own ceiling height
        self.assertAlmostEqual(done["waterproof"], bath["area"] + bath["perimeter"] * 1.2, places=1)
        self.assertAlmostEqual(done["base"], living["perimeter"] - 0.9, places=1)
        self.assertEqual((done["from_schedule"], done["rule_area"], done["wall_by_height"]), (2, 0.0, 0.0))
        self.assertEqual({r["source"] for r in done["rooms"]}, {"schedule"})


class FinishScheduleJobTests(SimpleTestCase):
    databases = "__all__"

    def test_the_floor_job_reads_the_finish_schedule_and_the_takeoff_uses_it(self):
        import os
        import tempfile
        import ezdxf
        from core import quantity_arch_takeoff as at
        from core import quantity_views as views
        from core.test_quantity_arch import ArchTakeoffTests
        doc = ezdxf.new()
        for text, x, y, h in finish_table():
            doc.modelspace().add_text(text, dxfattribs={"insert": (x, y), "height": h})
        with tempfile.TemporaryDirectory() as work:
            path = os.path.join(work, "000.dxf")
            doc.saveas(path)
            schedule = views._cad_finish_schedule("fv-9", [{"name": "A-701 실내재료마감표.dxf", "path": path}], [])
        self.assertEqual(schedule["rooms"]["침실"]["sheet"], "A-701 실내재료마감표")
        result = at.build(FinishTakeoffTests().views(), ArchTakeoffTests().model(), finish_schedule=schedule)
        row = result["buildings"][0]["rows"][2]
        self.assertEqual(sorted(row["finishes"]["wall"]), ["실크벽지", "합지벽지"])  # 거실 and 침실 rows
        check = result["finish_check"]
        self.assertEqual((check["read"], check["used"]), (True, ["거실/주방", "침실1"]))
        self.assertIn("욕실(공용)", check["unused"])
        self.assertNotIn("finish_rules", {a["key"] for a in result["assumed"]})  # every plan room had its row
        self.assertNotIn("ceiling_height", {a["key"] for a in result["assumed"]})  # and its ceiling height


class LineDoorTests(SimpleTestCase):
    def test_a_door_drawn_without_a_block_closes_the_room_and_counts(self):
        from core import quantity_arch
        frame, masonry = plan()[0]["frame"], plan()[0]["masonry"]

        def path(kind, segments):
            out = []
            for ax, ay, bx, by in segments:  # metres → millimetres, as the plan paths come
                pts = [(ax * 1000, ay * 1000), (bx * 1000, by * 1000)]
                out.append(((kind, "A-WALL"), min(ax, bx) * 1000, min(ay, by) * 1000, max(ax, bx) * 1000, max(ay, by) * 1000, pts))
            return out
        paths = path("frame", frame) + path("masonry", masonry)
        import math
        arc = [(6050 + 900 * math.cos(math.radians(a)), 1000 + 900 * math.sin(math.radians(a))) for a in range(0, 91, 10)]
        paths.append((("opening", "A-DOOR"), 6050, 1000, 6950, 1900, arc))  # the swing, hinged on the partition at y = 1.0 m
        names = [("거실", 3000.0, 3000.0), ("욕실", 8000.0, 3000.0)]
        window = ("AW1", "window", 2000.0, 0.0, 2000.0, None, False)  # the outer wall's window (a block)
        measured = quantity_arch.measure(paths, (0, 0, 10000, 6000), inserts=[window], tags=(), names=names)
        doors = {o["name"]: o for o in measured["openings"]}
        self.assertEqual(doors[quantity_arch.LINE_DOOR]["count"], 1)
        self.assertAlmostEqual(doors[quantity_arch.LINE_DOOR]["width"], 0.9, places=2)
        self.assertEqual(sorted(r["names"][0] for r in measured["rooms"] if r["names"]), ["거실", "욕실"])  # not one room
        # a door block on the same spot: its own arc is no second door
        inserts = [window, ("D1", "door", 6050.0, 1000.0, 900.0, None, True)]
        again = quantity_arch.measure(paths, (0, 0, 10000, 6000), inserts=inserts, tags=(), names=names)
        self.assertEqual({o["name"]: o["count"] for o in again["openings"]}, {"AW1": 1, "D1": 1})


class FinishScheduleVariantTests(SimpleTestCase):
    def test_headers_with_finish_words_and_a_second_header_line(self):
        from core import quantity_arch_finish_schedule as fs
        texts = [("실명", 10, 200, 3), ("바닥 마감", 60, 200, 3), ("굽도리", 110, 200, 3), ("벽체 마감재", 160, 200, 3), ("천정", 210, 200, 3),
                 ("반자고", 250, 200, 3),
                 # the second header line: each column's finish and its base
                 ("마감", 50, 192, 2.5), ("바탕", 70, 192, 2.5), ("마감", 150, 192, 2.5), ("바탕", 170, 192, 2.5),
                 ("거실", 10, 175, 3), ("강마루", 50, 175, 2.5), ("시멘트몰탈", 70, 175, 2.5), ("PVC 굽도리", 110, 175, 2.5),
                 ("실크벽지", 150, 175, 2.5), ("석고보드 2겹", 170, 175, 2.5), ("천장지", 210, 175, 2.5), ("2,400", 250, 175, 2.5)]
        room = fs.read_finish_schedule(texts)["rooms"]["거실"]
        self.assertEqual((room["floor"], room["base"], room["wall"], room["ceiling"], room["ceiling_height"]),
                         ("강마루", "PVC 굽도리", "실크벽지", "천장지", 2.4))

    def test_each_column_split_into_its_base_finish_and_note_takes_the_finish(self):
        from core import quantity_arch_finish_schedule as fs
        heads = [("바 닥", 49, 137), ("걸 레 받 이", 79, 137), ("벽 체", 113, 137), ("천 장", 148, 137),
                 ("층 별", 12, 135.5), ("실 명", 24, 135.5), ("비 고", 175, 135.5)]
        subs = [(t, x, 134) for x0 in (39, 72, 102, 135) for t, x in (("바 탕", x0), ("마 감", x0 + 11), ("비 고", x0 + 21))]
        rows = [("거 실", 128.4, ["판넬히팅/시멘트몰탈", "강마루", "-", "", "PVC 걸레받이", "-", "콘크리트면처리 or 석고보드", "실크 벽지", "-",
                                 "천장틀/석고보드", "실크 천장지", "-"]),
                ("주방 및 식당", 125.4, ["판넬히팅/시멘트몰탈", "강마루", "-", "", "PVC 걸레받이", "-", "or 방수석고보드", "실크 벽지", "-",
                                    "천장틀/석고보드", "실크 천장지", "-"]),
                ("욕 실", 119.4, ["시멘트 액체방수/", "지정 타일", "-", "", "", "-", "시멘트 액체방수", "지정 타일", "방수높이 : 비고참조",
                                 "천장틀", "PVC 천장재", "-"])]
        texts = [(t, x, y, 1.0) for t, x, y in heads + subs]
        for name, y, cells in rows:
            texts.append((name, 24, y, 0.75))
            texts += [(cell, x, y, 0.75) for cell, (_t, x, _y) in zip(cells, subs) if cell]
        texts.append(("지 상 층", 11, 123, 1.0))  # the 층별 column: a group, not a room
        texts.append(("* 욕실방수 높이", 168, 128.4, 0.75))  # the table's own note column
        rooms = fs.read_finish_schedule(texts)["rooms"]
        self.assertNotIn("지상층", rooms)
        living, kitchen, bath = rooms["거실"], rooms["주방"], rooms["욕실"]
        self.assertEqual((living["floor"], living["base"], living["wall"], living["ceiling"]), ("강마루", "PVC 걸레받이", "실크 벽지", "실크 천장지"))
        self.assertEqual((bath["floor"], bath["wall"], bath["ceiling"]), ("지정 타일", "지정 타일", "PVC 천장재"))
        self.assertTrue(bath["waterproof"])  # the waterproofing written under the tiles
        self.assertFalse(living["waterproof"] or kitchen["waterproof"])  # a water-resistant board is no waterproofing


class PastedGroupDoorTests(SimpleTestCase):
    def test_a_pasted_group_drawing_a_door_far_from_its_insert_point_is_one_door(self):
        import math
        import ezdxf
        from core import quantity_arch
        from core import quantity_floor_views as fv
        doc = ezdxf.new()
        doc.layers.add("A-DOOR")
        group = doc.blocks.new("A$Ce0037f0f")  # a door pasted with its frame, base point at the sheet origin
        group.add_lwpolyline([(6050, 1000), (6050, 1900)], dxfattribs={"layer": "A-DOOR"})
        group.add_arc((6050, 1000), 900, 0, 90, dxfattribs={"layer": "A-DOOR"})
        group.add_lwpolyline([(6000, 950), (7000, 950), (7000, 1050), (6000, 1050)], close=True, dxfattribs={"layer": "A-DOOR"})
        doc.modelspace().add_blockref("A$Ce0037f0f", (0, 0), dxfattribs={"layer": "A-DOOR"})
        inserts = quantity_arch.opening_inserts(doc, {}, is_opening=fv._is_opening_insert)
        (name, kind, x, y, _width, _tag, _arc), = inserts
        self.assertEqual((name, kind), ("A$Ce0037f0f", "door"))
        self.assertLess(math.hypot(x - 6450, y - 1400), 300)  # where its lines are, not the paste point
        frame, masonry = plan()[0]["frame"], plan()[0]["masonry"]
        paths = []
        for category, segments in (("frame", frame), ("masonry", masonry)):
            for ax, ay, bx, by in segments:
                pts = [(ax * 1000, ay * 1000), (bx * 1000, by * 1000)]
                paths.append(((category, "A-WALL"), min(ax, bx) * 1000, min(ay, by) * 1000, max(ax, bx) * 1000, max(ay, by) * 1000, pts))
        arc = [(6050 + 900 * math.cos(math.radians(a)), 1000 + 900 * math.sin(math.radians(a))) for a in range(0, 91, 10)]
        paths.append((("opening", "A-DOOR"), 6050, 1000, 6950, 1900, arc))  # the group's swing, as the plan paths give it
        measured = quantity_arch.measure(paths, (0, 0, 10000, 6000), inserts=inserts, tags=(), names=())
        self.assertEqual({o["name"]: o["count"] for o in measured["openings"]}, {"A$Ce0037f0f": 1})


    def test_an_xref_clipped_to_one_floor_gives_only_that_floors_openings(self):
        import ezdxf
        from ezdxf import xclip
        from core import quantity_arch
        from core import quantity_floor_views as fv
        doc = ezdxf.new()
        doc.layers.add("A-DOOR")
        door = doc.blocks.new("SD-900")
        door.add_line((0, 0), (900, 0), dxfattribs={"layer": "A-DOOR"})
        door.add_arc((0, 0), 900, 0, 90, dxfattribs={"layer": "A-DOOR"})
        building = doc.blocks.new("XR-101동")  # a bound building xref: every floor side by side
        for x in (1000, 31000, 61000):          # 1층, 2층, 지붕 plans 30 m apart
            building.add_blockref("SD-900", (x, 1000), dxfattribs={"layer": "A-DOOR"})
        building.add_text("거실", dxfattribs={"insert": (31500, 2000), "height": 300})
        building.add_text("거실", dxfattribs={"insert": (61500, 2000), "height": 300})
        sheet = doc.modelspace().add_blockref("XR-101동", (-30000, 0))  # the 2층 plan moved into this frame
        xclip.XClip(sheet).set_wcs_clipping_path([(-5000, -5000), (20000, 20000)])
        names = []
        inserts = quantity_arch.opening_inserts(doc, {}, None, fv._is_opening_insert, names=names)
        self.assertEqual([(n, round(x)) for n, _k, x, *_ in inserts], [("SD-900", 1000)])  # not the 1층 or the roof's
        self.assertEqual([(n, round(x)) for n, x, _y in names], [("거실", 1500)])


class WallLayerTests(SimpleTestCase):
    def test_one_letter_layers_c_concrete_and_b_block(self):
        from core import quantity_floor_views as fv
        self.assertEqual([fv.layer_category(n) for n in ("C", "B", "XREF-연결평면도$0$C", "F", "X", "CONC", "BEAM")],
                         ["frame", "masonry", "frame", None, None, "frame", None])

    def test_dry_partitions_drawn_by_their_boards_and_board_linings(self):
        from core import quantity_arch
        concrete = [(0.0, 0.0, 6.0, 0.0), (0.0, 0.2, 6.0, 0.2)]          # a 200 mm concrete wall
        lining = [(0.0, 0.29, 6.0, 0.29), (0.0, 0.30, 6.0, 0.30)]         # a board 90 mm off its face: a lining
        one_strip = [(2.0, 1.0, 2.0, 4.0), (2.03, 1.0, 2.03, 4.0)]        # a partition drawn as one 30 mm strip
        two_boards = [(4.0, 1.0, 4.0, 3.5), (4.0125, 1.0, 4.0125, 3.5),  # both boards of a partition, the studs between not drawn
                      (4.0875, 1.0, 4.0875, 3.5), (4.1, 1.0, 4.1, 3.5)]
        lone = [(5.0, 1.0, 5.0, 2.0)]                                       # a lone line is no partition
        found = quantity_arch.board_partitions(lining + one_strip + two_boards + lone, concrete)
        self.assertAlmostEqual(found["length"], 3.0 + 2.5, places=2)
        segments = [((kind, "L"), min(a, c) * 1000, min(b, d) * 1000, max(a, c) * 1000, max(b, d) * 1000, [(a * 1000, b * 1000), (c * 1000, d * 1000)])
                    for kind, lines in (("frame", concrete), ("dry", lining + one_strip + two_boards + lone)) for a, b, c, d in lines]
        measured = quantity_arch.measure(segments, (-1000, -1000, 8000, 6000))
        self.assertAlmostEqual(measured["walls"]["dry"]["length"], 5.5, places=1)
