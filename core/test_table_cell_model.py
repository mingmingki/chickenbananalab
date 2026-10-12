from django.test import SimpleTestCase

from .table_cell_model import assign_text_occurrences, build_grid_topology


class CanonicalTableCellModelTests(SimpleTestCase):
    def test_grid_and_single_assignment(self):
        lines = [
            {"bbox": [0, 0, 0, 20], "source_entity_keys": ["v0"]},
            {"bbox": [10, 0, 10, 20], "source_entity_keys": ["v1"]},
            {"bbox": [20, 0, 20, 20], "source_entity_keys": ["v2"]},
            {"bbox": [0, 0, 20, 0], "source_entity_keys": ["h0"]},
            {"bbox": [0, 10, 20, 10], "source_entity_keys": ["h1"]},
            {"bbox": [0, 20, 20, 20], "source_entity_keys": ["h2"]},
        ]
        grid = build_grid_topology(lines, [0, 0, 20, 20], tolerance=0.1)
        self.assertEqual(len(grid["cells"]), 4)
        assigned = assign_text_occurrences([
            {"canonical_entity_key": "t1", "bbox": [2, 2, 3, 3], "raw_text": "A"}
        ], grid["cells"], tolerance=0.1)
        self.assertEqual(assigned[0]["terminal_status"], "assigned_single_cell")

    def test_unresolved_text_is_not_filled(self):
        assigned = assign_text_occurrences([
            {"canonical_entity_key": "t1", "bbox": [30, 30, 31, 31], "raw_text": "X"}
        ], [], tolerance=0.1)
        self.assertEqual(assigned[0]["terminal_status"], "unresolved_assignment")
        self.assertIsNone(assigned[0]["cell"])
