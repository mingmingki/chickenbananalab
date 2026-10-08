import unittest
from decimal import Decimal as D
from core_unified_state import new_position, advance
from core_unified_policy import POLICY


def position(side='long'):
    return new_position('p',side,D('100'),D('1.5'),D('100'),D('24'),D('150'))


class StateTests(unittest.TestCase):
    def test_latched_mfe_and_reduce_denominator(self):
        for side,peak in [('long','100.75'),('short','99.25')]:
            s = advance(position(side),dict(kind='MARK',price=peak),POLICY)['state']
            s = advance(s,dict(kind='MARK',price='100'),POLICY)['state']
            self.assertTrue(s['protection_armed'])
            self.assertEqual(s['protect_base_qty'],D('24'))
            r = advance(s,dict(kind='WEAK_5M',bar_ms=600000,confirmed=True,
                              weak_1h=False,lot='1',minimum='1'),POLICY)
            self.assertEqual(r['intent']['qty'],D('6'))
            self.assertEqual(r['state']['filled_qty'],D('24'))

    def test_fill_only_advances_stage_and_is_idempotent(self):
        s = advance(position(),dict(kind='MARK',price='101'),POLICY)['state']
        i = advance(s,dict(kind='WEAK_5M',bar_ms=600000,confirmed=True,
                          weak_1h=False,lot='1',minimum='1'),POLICY)['intent']
        self.assertIsNotNone(i)
        s = advance(s,dict(kind='INTENT_RESERVED',intent=i),POLICY)['state']
        self.assertIsNone(advance(s,dict(kind='WEAK_5M',bar_ms=600000,confirmed=True),POLICY)['intent'])
        fill = dict(kind='FILL',intent_id=i['id'],trade_id='t1',qty='3',terminal=False)
        s = advance(s,fill,POLICY)['state']
        self.assertEqual(s['filled_qty'],D('21'))
        self.assertEqual(s['reduce_stage'],0)
        self.assertEqual(advance(s,fill,POLICY)['state']['filled_qty'],D('21'))
        s = advance(s,dict(fill,trade_id='t2',terminal=True),POLICY)['state']
        self.assertEqual(s['filled_qty'],D('18'))
        self.assertEqual(s['reduce_stage'],0)
        self.assertIsNotNone(s['pending_intent'])
        s = advance(s,dict(kind='PROTECTION_CONFIRMED',intent_id=i['id'],
                          confirmed=True),POLICY)['state']
        self.assertEqual(s['reduce_stage'],1)
        self.assertIsNone(advance(s,dict(kind='WEAK_5M',bar_ms=600000,confirmed=True,
                           weak_1h=True,lot='1',minimum='1'),POLICY)['intent'])
        self.assertEqual(advance(s,dict(kind='WEAK_5M',bar_ms=900000,confirmed=True,
                         weak_1h=True,lot='1',minimum='1'),POLICY)['intent']['qty'],D('6'))

    def test_add_requires_all_fresh_gates_and_preserves_initial_r(self):
        s = position()
        e = dict(kind='TREND_CONFIRMED',bar_ms=600000,confirmed=True,
                 approved=True,net_profit='1',one_h_opposed=False,
                 higher_confirmed=True,risk_available_qty='100',lot='1',minimum='1')
        self.assertEqual(advance(s,e,POLICY)['intent']['qty'],D('25'))
        for key,value in [('approved',False),('net_profit','0'),('one_h_opposed',True)]:
            self.assertIsNone(advance(s,dict(e,**{key:value}),POLICY)['intent'])
        self.assertEqual(advance(s,e,POLICY)['state']['initial_r'],D('1.5'))

    def test_probe_failure_needs_two_different_closed_bars(self):
        s = position()
        e = dict(kind='PROBE_INVALIDATED',bar_ms=60000,confirmed=True)
        r = advance(s,e,POLICY)
        self.assertIsNone(r['intent'])
        self.assertIsNone(advance(r['state'],e,POLICY)['intent'])
        self.assertEqual(advance(r['state'],dict(e,bar_ms=120000),POLICY)['intent']['kind'],'EXIT')

    def test_reversal_is_exit_only_and_unknown_blocks_add(self):
        s = position()
        r = advance(s,dict(kind='REVERSE_APPROVED',approved=True,bar_ms=600000),POLICY)
        self.assertEqual(r['intent']['kind'],'EXIT')
        s = advance(s,dict(kind='ORDER_UNKNOWN'),POLICY)['state']
        self.assertIsNone(advance(s,dict(kind='TREND_CONFIRMED',approved=True),POLICY)['intent'])

    def test_unknown_order_remains_reconciling_after_profit_arm(self):
        s = advance(position(),dict(kind='ORDER_UNKNOWN'),POLICY)['state']
        s = advance(s,dict(kind='MARK',price='101'),POLICY)['state']
        self.assertEqual(s['phase'],'RECONCILING')
        self.assertTrue(s['protection_armed'])
        self.assertIsNone(advance(s,dict(kind='WEAK_5M',bar_ms=600000,confirmed=True,
                     weak_1h=False,lot='1',minimum='1'),POLICY)['intent'])

    def test_partial_terminal_fill_keeps_reservation(self):
        s = position()
        i = advance(s,dict(kind='REVERSE_APPROVED',approved=True,bar_ms=600000),POLICY)['intent']
        s = advance(s,dict(kind='INTENT_RESERVED',intent=i),POLICY)['state']
        s = advance(s,dict(kind='FILL',intent_id=i['id'],trade_id='t1',qty='3',
                          terminal=True),POLICY)['state']
        self.assertIsNotNone(s['pending_intent'])
        self.assertEqual(s['filled_qty'],D('21'))

    def test_unknown_baseline_adoption_cannot_emit_strategy_intents(self):
        s = advance({},dict(kind='ADOPT',position_id='old',side='long',
                    filled_qty='10',entry='100',mark='101'),POLICY)['state']
        self.assertFalse(s.get('baseline_known',True))
        s = advance(s,dict(kind='MARK',price='110'),POLICY)['state']
        self.assertFalse(s['protection_armed'])
        self.assertIsNone(advance(s,dict(kind='REVERSE_APPROVED',approved=True,
                         bar_ms=600000),POLICY)['intent'])


if __name__ == '__main__': unittest.main()
