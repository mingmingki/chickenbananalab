from django.test import SimpleTestCase

from .table_roi_verifier import verify_ruled_table_v2


class RuledTableVerifierV2Tests(SimpleTestCase):
    def test_accepts_scale_normalized_ruled_table(self):
        result = verify_ruled_table_v2({
            "text_count": 24, "line_count": 30, "line_intersections": 20,
            "closed_cell_candidates": 12, "aligned_row_bands": 6,
            "aligned_column_bands": 5, "text_in_cell_count": 20,
            "parallel_line_families": 2, "entity_key_agreement": 1.0,
        })
        self.assertEqual(result["subtype"], "quantity_ruled_table")
        self.assertEqual(result["status"], "accept")

    def test_rejects_structural_or_title_block_dominance(self):
        result = verify_ruled_table_v2({
            "text_count": 17, "line_count": 2, "line_intersections": 0,
            "closed_cell_candidates": 0, "parallel_line_families": 1,
            "structural_geometry_dominance": 0.8, "entity_key_agreement": 1.0,
        })
        self.assertEqual(result["subtype"], "structural_grid")
        self.assertEqual(result["status"], "reject")

    def test_unruled_candidate_remains_unvalidated(self):
        result = verify_ruled_table_v2({
            "text_count": 20, "line_count": 0, "line_intersections": 0,
            "aligned_row_bands": 4, "aligned_column_bands": 4,
            "text_in_cell_count": 15, "parallel_line_families": 0,
            "entity_key_agreement": 1.0,
        })
        self.assertEqual(result["subtype"], "insufficient_evidence")
        self.assertEqual(result["status"], "review")
