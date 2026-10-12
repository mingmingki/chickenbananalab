from django.test import SimpleTestCase

from .table_semantic_model import classify_row, normalize_header, parse_typed_scalars


class TableSemanticModelTests(SimpleTestCase):
    def test_controlled_alias_preserves_unknown(self):
        self.assertEqual(normalize_header("상부근")['semantic_field_candidate'], 'top_rebar')
        self.assertEqual(normalize_header("미확인 열")['status'], 'unknown_column')

    def test_scalar_parser_is_evidence_bound_and_keeps_raw_substring(self):
        parsed = parse_typed_scalars("400×600 / D13@200 / SD400")
        self.assertTrue(any(x['parser_rule_id'] == 'dimension_x' for x in parsed))
        self.assertTrue(any(x['parser_rule_id'] == 'rebar_spacing' for x in parsed))
        self.assertTrue(any(x['parser_rule_id'] == 'rebar_grade' for x in parsed))
        self.assertTrue(all(x['raw_substring'] for x in parsed))

    def test_missing_header_does_not_create_typed_semantics(self):
        parsed = parse_typed_scalars("400×600")
        self.assertTrue(parsed)
        self.assertIsNone(parsed[0]['field_hint'])
        self.assertEqual(classify_row(["400×600"], role='column_schedule',
                                      has_header_evidence=False), 'unresolved_row')

    def test_terminal_row_is_single_and_explicit_inheritance_only(self):
        self.assertEqual(classify_row([], role='beam_schedule', has_header_evidence=False), 'blank')
        self.assertEqual(classify_row(['〃'], role='beam_schedule', has_header_evidence=False,
                                      explicit_repeat=True), 'continuation_row')
        self.assertEqual(classify_row(['NOTE: see general'], role='beam_schedule',
                                      has_header_evidence=False), 'note')
