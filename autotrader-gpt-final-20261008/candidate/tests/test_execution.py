import tempfile
import unittest
from pathlib import Path
from core_unified_execution import ExecutionManager
from core_unified_store import Store
from core_unified_policy import POLICY_VERSION
from core_unified_fakes import FakePort


class ExecutionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name)/'state.db')
        self.store.save_state('X',dict(revision=0,filled_qty='0',position_id='p',side='long'),None)
        self.port = FakePort(self.store)
        self.manager = ExecutionManager(self.store,self.port,lambda:1000)
        c = dict(id='candidate',symbol='X',version=POLICY_VERSION,snapshot_id='snapshot',generation=1,
                 side='long',signal_ms=1000,expires_ms=61000,reference_price='100',atr5='2')
        a = dict(candidate_id='candidate',snapshot_id='snapshot',generation=1,
                 gemini_action='long',gemini_confidence=.8,gpt_decision='approve_now',
                 gpt_confidence=.8,completed_ms=1000)
        self.intent = dict(id='one',position_id='p',kind='ENTRY',side='long',qty='2',
                           expected_revision=0,expires_ms=61000,candidate=c,approval=a,
                           risk_required='2')

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_timeout_after_fill_survives_restart_without_second_submit(self):
        self.port.fill_then_timeout = True
        self.port.available = False
        result = self.manager.execute('X',self.intent)
        self.assertEqual(result['status'],'reconciling')
        self.assertIsNotNone(self.store.pending('X'))
        path = Path(self.tmp.name)/'state.db'
        self.store.close()
        self.store = Store(path)
        self.port.store = self.store
        self.port.available = True
        restarted = ExecutionManager(self.store,self.port,lambda:1000)
        self.assertEqual(restarted.reconcile('X')['status'],'complete')
        self.assertEqual(self.port.submitted_ids,['one'])
        self.assertEqual(self.store.state('X')['filled_qty'],'2')
        self.assertIsNone(self.store.pending('X'))

    def test_pause_after_approval_makes_no_order(self):
        self.port.allowed = False
        self.assertEqual(self.manager.execute('X',self.intent)['status'],'blocked')
        self.assertEqual(self.port.submitted_ids,[])
        self.assertIsNone(self.port.pos)

    def test_partial_exit_retains_position_and_pending(self):
        self.store.save_state('X',dict(filled_qty='4',position_id='p',side='long'),0)
        self.port.pos = dict(contracts='4',side='long',position_id='p')
        self.port.fill_ratio = __import__('decimal').Decimal('.5')
        i = dict(self.intent,kind='EXIT',qty='4',expected_revision=1)
        self.assertEqual(self.manager.execute('X',i)['status'],'reconciling')
        self.assertEqual(self.store.state('X')['filled_qty'],'2.0')
        self.assertEqual(self.port.pos['contracts'],'2.0')
        self.assertIsNotNone(self.store.pending('X'))
        self.manager.reconcile('X')
        self.assertEqual(self.port.submitted_ids,['one'])

    def test_protection_failure_blocks_completion(self):
        self.port.protected = False
        self.assertEqual(self.manager.execute('X',self.intent)['status'],'reconciling')
        self.assertTrue(self.port.killed)
        self.assertIsNotNone(self.store.pending('X'))

    def test_cleanup_failure_never_confirms_flat(self):
        self.store.save_state('X',dict(filled_qty='4',position_id='p',side='long'),0)
        self.port.pos = dict(contracts='4',side='long',position_id='p')
        self.port.cleanup_ok = False
        self.assertEqual(self.manager.execute('X',dict(self.intent,kind='EXIT',qty='4',
                         expected_revision=1))['status'],'reconciling')
        self.assertIsNone(self.port.pos)
        self.assertIsNotNone(self.store.pending('X'))

    def test_stale_quote_and_chased_price_block_entry(self):
        for quote in (None,'100.5'):
            self.port.quote = quote
            self.assertEqual(self.manager.execute('X',self.intent)['status'],'blocked')
        self.assertEqual(self.port.submitted_ids,[])

    def test_external_position_blocks_entry(self):
        self.port.pos = dict(contracts='3',side='short',position_id='external')
        self.assertEqual(self.manager.execute('X',self.intent)['status'],'blocked')
        self.assertEqual(self.port.submitted_ids,[])

    def test_partial_entry_is_protected_before_waiting_for_terminal(self):
        self.port.fill_ratio = __import__('decimal').Decimal('.5')
        self.port.protected = False
        self.assertEqual(self.manager.execute('X',self.intent)['status'],'reconciling')
        self.assertEqual(self.port.pos['contracts'],'1.0')
        self.assertTrue(self.port.killed)
        self.assertIsNotNone(self.store.pending('X'))

    def test_terminal_partial_add_also_checks_protection(self):
        self.store.save_state('X',dict(filled_qty='4',position_id='p',side='long'),0)
        self.port.pos = dict(contracts='4',side='long',position_id='p')
        self.port.fill_ratio = __import__('decimal').Decimal('.5')
        i = dict(self.intent,kind='ADD',expected_revision=1)
        self.manager.execute('X',i)
        self.port.orders['one'].update(terminal=True,remaining='0')
        self.port.protected = False
        self.assertEqual(self.manager.reconcile('X')['status'],'reconciling')
        self.assertTrue(self.port.killed)
        self.assertIsNotNone(self.store.pending('X'))

    def test_opposite_side_approval_cannot_be_used_for_intent(self):
        self.store.save_state('X',dict(filled_qty='0',position_id='p',side='short'),0)
        i = dict(self.intent,side='short',expected_revision=1)
        self.assertEqual(self.manager.execute('X',i)['status'],'blocked')
        self.assertEqual(self.port.submitted_ids,[])

    def test_cross_symbol_approval_cannot_be_used_for_intent(self):
        i = dict(self.intent,candidate=dict(self.intent['candidate'],symbol='Y'))
        self.assertEqual(self.manager.execute('X',i)['status'],'blocked')
        self.assertEqual(self.port.submitted_ids,[])

    def test_protection_exception_still_escalates(self):
        def failed_install(symbol,intent): raise TimeoutError('unknown_stop_result')
        self.port.protected = False
        self.port.install_protection = failed_install
        self.assertEqual(self.manager.execute('X',self.intent)['status'],'reconciling')
        self.assertTrue(self.port.killed)
        self.assertIsNotNone(self.store.pending('X'))


if __name__ == '__main__':
    unittest.main()
