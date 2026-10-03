from django.test import SimpleTestCase

from . import quantity_views


def _rect(x0, y0, x1, y1):
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


def _split(*outlines):
    polys = [(pts, quantity_views._polygon_area(pts)) for pts in outlines]
    return quantity_views._split_outer_and_holes(polys)


SLAB = _rect(0, 0, 10000, 5000)
OPENING = _rect(2000, 2000, 3000, 3000)


class QuantityDuplicateOutlineTests(SimpleTestCase):
    """An outline drawn twice is one outline, not an outline and its opening.

    S-501 has such duplicates: which copy became the "opening" depended on
    the last digits of the coordinates, so ODA and the free converter gave
    different slab areas.
    """

    def test_slab_drawn_twice_keeps_its_area(self):
        # Second copy: other start vertex, reversed, coordinates off by float noise.
        copy = [(10000.000000001, 5000), (10000, 0), (0, 0), (0, 5000.000000001)]
        for outlines in ((SLAB, copy, OPENING), (copy, SLAB, OPENING), (OPENING, copy, SLAB)):
            with self.subTest(order=[len(o) for o in outlines]):
                outer, holes, count = _split(*outlines)
                self.assertAlmostEqual(outer, 50000000.0, places=0)
                self.assertEqual((round(holes), count), (1000000, 1))

    def test_opening_drawn_twice_is_one_opening(self):
        outer, holes, count = _split(SLAB, OPENING, list(reversed(OPENING)))
        self.assertEqual((round(outer), round(holes), count), (50000000, 1000000, 1))

    def test_closing_vertex_repeated_is_the_same_outline(self):
        outer, holes, count = _split(SLAB, SLAB + [SLAB[0]])
        self.assertEqual((round(outer), round(holes), count), (50000000, 0, 0))

    def test_separate_outlines_of_equal_area_both_count(self):
        outer, holes, count = _split(_rect(0, 0, 1000, 1000), _rect(5000, 0, 6000, 1000))
        self.assertEqual((round(outer), round(holes), count), (2000000, 0, 0))

    def test_different_shapes_with_the_same_box_area_and_centre_both_count(self):
        # Point-symmetric "S" and "Z" shapes: same bounding box, area and centroid.
        s_shape = [(0, 0), (2, 0), (2, 1), (3, 1), (3, 2), (1, 2), (1, 1), (0, 1)]
        z_shape = [(1, 0), (3, 0), (3, 1), (2, 1), (2, 2), (0, 2), (0, 1), (1, 1)]
        outer, holes, count = _split(_rect(-10, -10, 10, 10), s_shape, z_shape)
        self.assertEqual(count, 2)
        self.assertEqual(round(holes), 8)
