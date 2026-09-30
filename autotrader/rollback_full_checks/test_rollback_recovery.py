import importlib.util, unittest
from core_unified_store import Store

class RecoveryTests(unittest.TestCase):
    def test_only_exact_external_exit_can_settle_pending_reservation(self):
        self.assertIsNotNone(importlib.util.find_spec('rollback_recovery'))
        from rollback_recovery import record_proven_external_exit
        s=Store(':memory:');self.addCleanup(s.close)
        s.save_state('X',dict(position_id='p',side='long',filled_qty='2',contract_size='1',cost_rate='.001'),None)
        s.reserve('X',dict(id='pending',kind='EXIT',side='long',qty='2'),0)
        evidence=[dict(order_id='actual',trade_id='fill',qty='1',price='110',filled_ms=1000)]
        with self.assertRaises(ValueError):record_proven_external_exit(s,'X','pending',evidence)
        self.assertIsNotNone(s.pending('X'));self.assertEqual(s.accounting_records(),[])
        evidence[0]['qty']='2'
        record_proven_external_exit(s,'X','pending',evidence)
        self.assertIsNone(s.pending('X'));self.assertEqual(s.state('X')['filled_qty'],'0')
        self.assertEqual(s.accounting_records()[0]['intent_id'],'actual')
        self.assertEqual(s.accounting_records()[0]['trade_id'],'fill')
