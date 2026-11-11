"""The 3D site: plans cleaned of sheet clutter, towers put on the parking deck where their walls
meet its plan, each wall drawn once, and every building's floors at one height datum."""
import copy
import random

from django.test import SimpleTestCase

from core import quantity_floor_views as fv
from core import quantity_site as site


def wall(x0, y0, x1, y1, t=0.2):
    """A wall as its two faces (plan metres)."""
    if abs(y1 - y0) < 1e-9:
        return [[x0, y0, x1, y1], [x0, y0 + t, x1, y1 + t]]
    return [[x0, y0, x1, y1], [x0 + t, y0, x1 + t, y1]]


def tower(seed=1):
    """An irregular tower plan (no symmetry, so one place fits): outer walls and rooms."""
    rnd = random.Random(seed)
    lines = wall(0, 0, 30, 0) + wall(0, 0, 0, 14) + wall(0, 14, 22, 14) + wall(22, 14, 22, 18) + wall(22, 18, 30, 18) + wall(30, 0, 30, 18)
    x = 3.0
    while x < 28:
        lines += wall(x, 0, x, rnd.uniform(5, 12))
        x += rnd.uniform(2.5, 5.0)
    lines += wall(4, 7, 18, 7) + wall(9, 10, 20, 10)
    return lines


def plan(lines, **extra):
    out = {"walls": [l + [0] for l in lines], "cols": [], "beams": [], "opens": [], "slab": [[0, 0, 30, 18]],
           "slab_area": 540.0, "slab_approximate": False}
    out.update(extra)
    return out


def moved(lines, turn, dx, dy):
    return [[a + dx, b + dy, c + dx, d + dy] for a, b, c, d in site._turn(lines, turn)]


def parking(*towers):
    """A parking plan: its own outer walls and a column grid, with towers' walls drawn on it."""
    lines = wall(-10, -10, 150, -10) + wall(-10, -10, -10, 80) + wall(-10, 80, 150, 80) + wall(150, -10, 150, 80)
    cols = [[[x, y], [x + 0.6, y], [x + 0.6, y + 0.6], [x, y + 0.6]] for x in range(0, 140, 8) for y in range(0, 70, 8)]
    for t in towers:
        lines += t
    return {"walls": [l + [0] for l in lines], "cols": cols, "beams": [], "opens": [], "slab": [[-10, -10, 150, 80]],
            "slab_area": 14400.0, "slab_approximate": False}


def levels(keys, elevs):
    return [{"key": k, "label": k, "elev": e, "height": 3.0, "plan": i} for i, (k, e) in enumerate(zip(keys, elevs))]


class CleanPlanTests(SimpleTestCase):
    def test_legend_samples_borders_and_specks_are_dropped(self):
        lines = tower()
        legend = wall(60, 5, 66, 5)                     # a wall sample beside the plan
        border = [[-20, -20, 80, -20], [80, -20, 80, 60], [80, 60, -20, 60], [-20, 60, -20, -20]]  # sheet border on a wall layer
        p = plan(lines + legend + border)
        dropped = site.clean_plan(p)
        self.assertEqual(dropped, len(legend) + len(border))
        self.assertEqual(len(p["walls"]), len(lines))

    def test_free_standing_columns_inside_any_kept_drawing_stay(self):
        p = parking(moved(tower(), 0, 100, 40))
        before = len(p["cols"])
        site.clean_plan(p)
        self.assertEqual(len(p["cols"]), before)

    def test_a_plan_of_only_specks_shows_nothing(self):
        p = plan([[0, 0, 0.3, 0], [0, 0.2, 0.3, 0.2]])
        site.clean_plan(p)
        self.assertEqual(p["walls"], [])


class FitTests(SimpleTestCase):
    def test_a_tower_is_found_on_the_parking_plan_turned_and_moved(self):
        t = tower()
        deck = site._wall_lines(parking(moved(t, 1, 70, 20)))
        best = site.fits(t, deck)[0]
        self.assertGreater(best[0], 0.9)
        self.assertEqual(best[1], 1)
        self.assertAlmostEqual(best[2], 70, delta=0.4)
        self.assertAlmostEqual(best[3], 20, delta=0.4)

    def dense(self):
        rnd = random.Random(7)
        lines = []
        for _ in range(1500):
            x, y = rnd.uniform(0, 120), rnd.uniform(0, 80)
            lines += wall(x, y, x + rnd.uniform(1, 6), y) if rnd.random() < 0.5 else wall(x, y, x, y + rnd.uniform(1, 6))
        return lines

    def test_a_tower_not_on_a_dense_plan_does_not_fit_by_chance(self):
        self.assertLess(site.fits(tower(5), self.dense())[0][0], site.PLACE_SURE)

    def test_a_small_drawing_is_never_used_to_place_a_building(self):
        small = wall(0, 0, 5, 0) + wall(0, 0, 0, 3) + wall(5, 0, 5, 4)
        host = {"name": "지하주차장", "levels": levels(["B1"], [0.0]), "plans": [plan(self.dense() + small)]}
        shed = {"name": "경비실", "levels": levels(["B1", "1"], [0.0, 3.5]), "plans": [plan(small), plan(small)]}
        self.assertEqual(site.place_buildings([host, shed])["placed"], [])


class PlaceTests(SimpleTestCase):
    def buildings(self):
        a, b = tower(1), tower(2)
        host = {"name": "지하주차장", "levels": levels(["B1", "R"], [7.6, 11.1]),
                "plans": [parking(moved(a, 0, 20, 30)), plan([])]}
        a_bldg = {"name": "101동", "levels": levels(["B1", "1", "2"], [0.0, 3.5, 6.75]),
                  "plans": [plan(copy.deepcopy(a)), plan(copy.deepcopy(a)), plan(copy.deepcopy(a))]}
        b_bldg = {"name": "102동", "levels": levels(["1", "2"], [0.0, 3.25]),
                  "plans": [plan(copy.deepcopy(b)), plan(copy.deepcopy(b))]}
        return host, a_bldg, b_bldg

    def test_a_tower_drawn_on_the_parking_goes_there_and_the_rest_stay_beside(self):
        host, a, b = self.buildings()
        summary = site.assemble([host, a, b])
        self.assertEqual(summary["host"], "지하주차장")
        self.assertEqual(summary["placed"], ["101동"])
        self.assertEqual(summary["beside"], ["102동"])
        self.assertTrue(a["site"]["placed"])
        self.assertEqual(a["site"]["turn"], 0)
        xs = [c for w in a["plans"][1]["walls"] for c in (w[0], w[2])]
        self.assertAlmostEqual(min(xs), 20, delta=0.4)   # the plan now in the parking's coordinates
        # its basement floor meets the parking's: B1 at 7.6 like the parking's B1
        self.assertAlmostEqual(a["site"]["lift"], 7.6)
        # beside the site, the ground floor stands at the parking deck's height
        self.assertAlmostEqual(b["site"]["lift"], 11.1)
        self.assertEqual((a["kind"], b["kind"], host["kind"]), ("tower", "tower", "parking"))

    def test_a_wall_both_draw_is_drawn_once(self):
        host, a, b = self.buildings()
        site.assemble([host, a, b])
        basement = a["plans"][0]
        self.assertEqual(basement["walls"], [])           # the parking draws this basement
        self.assertEqual(basement["slab"], [])
        self.assertEqual(basement["shared_with"], "지하주차장")
        self.assertGreater(len(a["plans"][1]["walls"]), 20)  # its ground floor is its own

    def test_two_buildings_never_take_one_place(self):
        host, a, b = self.buildings()
        twin = copy.deepcopy(a)
        twin["name"] = "103동"
        summary = site.assemble([host, a, twin])
        self.assertEqual(len(summary["placed"]), 1)

    def test_a_sheet_drawn_elsewhere_in_its_frame_is_lined_up(self):
        t = tower(3)
        building = {"name": "101동", "levels": levels(["1", "2", "3"], [0.0, 3.25, 6.1]),
                    "plans": [plan(copy.deepcopy(t)), plan(moved(t, 0, 40, -12)), plan(copy.deepcopy(t))]}
        moves = site.align_floors(building)
        self.assertEqual([m[0] for m in moves], [1])
        xs = [c for w in building["plans"][1]["walls"] for c in (w[0], w[2])]
        self.assertAlmostEqual(min(xs), 0, delta=0.4)


class HeightAndNameTests(SimpleTestCase):
    def test_basements_below_a_known_ground_floor_count_down_from_it(self):
        table = {"sections": {"101동": {"elevations": {"1": 11.1}}}}
        elev = fv._elevations(["B2", "B1", "1", "2"], table, "101동")
        self.assertEqual((elev["B2"], elev["B1"], elev["1"]), (4.1, 7.6, 11.1))
        # a parking deck's roof is where the towers' ground floor is
        deck = fv._elevations(["B2", "B1", "R"], {"sections": {"공통": {"elevations": {"1": 11.1}}}}, "지하주차장")
        self.assertEqual((deck["B2"], deck["B1"], deck["R"]), (4.1, 7.6, 11.1))
        # nothing known: from 0 upward as before
        self.assertEqual(fv._elevations(["B1", "1"], None, "x"), {"B1": 0.0, "1": 3.5})

    def test_facilities_keep_their_own_names(self):
        self.assertEqual(fv.floor_of("관리사무소 및 경로당 1층 구조평면도")["building"], "관리사무소 및 경로당")
        self.assertEqual(fv.floor_of("경비실 PIT층 구조평면도")["building"], "경비실")
        self.assertEqual(fv.floor_of("주민공동시설 지하1층 구조평면도")["building"], "주민공동시설")
        self.assertEqual(fv.floor_of("부대시설 1층 구조평면도")["building"], "부대시설")
        self.assertEqual(fv.floor_of("101동 지하1층 구조평면도")["building"], "101동")
        self.assertEqual(site.building_kind("관리사무소 및 경로당"), "facility")
        self.assertEqual(site.building_kind("지하주차장"), "parking")

    def test_the_model_carries_the_site_but_partial_results_skip_it(self):
        plans = [{"floor": fv.floor_of("101동 1층 구조평면도"), "sheet": "S-1", "model": plan(tower())},
                 {"floor": fv.floor_of("101동 2층 구조평면도"), "sheet": "S-2", "model": plan(tower())}]
        self.assertIn("site", fv.build_model(copy.deepcopy(plans), None))
        self.assertNotIn("site", fv.build_model(copy.deepcopy(plans), None, site=False))
