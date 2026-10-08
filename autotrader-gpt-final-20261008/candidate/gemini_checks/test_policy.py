import unittest
from decimal import Decimal as D
import core_unified_policy as p


class PolicyTests(unittest.TestCase):
    def test_quarter_floor_and_minimum(self):
        self.assertEqual(p.probe_qty(D('10'), D('1'), D('1')), D('2'))
        self.assertEqual(p.probe_qty(D('2'), D('1'), D('1')), D('0'))

    def test_invalid_numbers_rejected(self):
        for value in (True, None, '0.8', float('nan'), float('inf'), -1, .69, 1.01):
            with self.subTest(value=value):
                self.assertFalse(p.confidence_ok(value))
        self.assertTrue(p.confidence_ok(.70))
        self.assertTrue(p.confidence_ok(1))

    def test_approval_boundaries_and_identity(self):
        c = dict(id='a', side='long', signal_ms=1000, expires_ms=61000,
                 reference_price='100', atr5='2', snapshot_id='s', generation=1,
                 version=p.POLICY_VERSION)
        a = dict(candidate_id='a', snapshot_id='s', generation=1,
                 gemini_action='long', gemini_confidence=.8,
                 gpt_decision='approve_now', gpt_confidence=.8, completed_ms=2000)
        self.assertTrue(p.approval_valid(c,a,3000,D('100.49'))[0])
        self.assertFalse(p.approval_valid(c,a,3000,D('100.5'))[0])
        self.assertFalse(p.approval_valid(c,a,61000,D('100'))[0])
        self.assertFalse(p.approval_valid(c,a,1500,D('100'))[0])
        self.assertFalse(p.approval_valid(c,dict(a,generation=2),3000,D('100'))[0])
        self.assertFalse(p.approval_valid(c,dict(a,gpt_decision='wait'),3000,D('100'))[0])
        c['side'], a['gemini_action'] = 'short', 'short'
        self.assertTrue(p.approval_valid(c,a,3000,D('99.51'))[0])
        self.assertFalse(p.approval_valid(c,a,3000,D('99.5'))[0])
        self.assertFalse(p.approval_valid(dict(c,version='unknown'),a,3000,D('100'))[0])

    def test_bad_sizing_inputs(self):
        for lot in (D('0'), D('-1'), D('NaN')):
            with self.assertRaises(ValueError):
                p.probe_qty(D('10'),lot,D('1'))


if __name__ == '__main__':
    unittest.main()
