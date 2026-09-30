import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock
from core_unified_store import Store
from core_unified_service import owner, Coordinator
from test_market import snapshot

class ServiceTests(unittest.TestCase):
    def test_legacy_write_is_blocked_after_durable_handoff(self):
        import sys
        from contextlib import nullcontext
        from unittest.mock import patch
        from core_unified_service import legacy_writer
        with tempfile.TemporaryDirectory() as directory:
            cfg=SimpleNamespace(user_dir=directory); calls=[]
            @legacy_writer
            def write(cfg,symbol): calls.append(symbol); return True
            with patch.dict(sys.modules,{'candidate_c_hybrid_ownership':SimpleNamespace(account_order_lock=lambda d:nullcontext())}):
                self.assertTrue(write(cfg,'X'))
                store=Store(directory+'/core_unified.sqlite3')
                store.transfer_owner('X','legacy','unified',0,{'flat':True})
                self.assertFalse(write(cfg,'X'))
                self.assertEqual(calls,['X']); store.close()

    def test_missing_database_does_not_create_one_and_corruption_blocks(self):
        from pathlib import Path
        with tempfile.TemporaryDirectory() as directory:
            cfg=SimpleNamespace(user_dir=directory)
            self.assertEqual(owner(cfg,'X'),'legacy')
            path=Path(directory)/'core_unified.sqlite3'
            self.assertFalse(path.exists())
            path.write_text('broken')
            self.assertEqual(owner(cfg,'X'),'unknown')

    def test_waiting_ai_does_not_block_risk_and_stop_invalidates(self):
        s=snapshot(); feed=Mock(); feed.snapshot.return_value=s
        broker=Mock(); broker.drain.return_value=[]; broker.offer.return_value=True
        controller=Mock(); controller.status.return_value={'owner':'unified','state':None}
        coordinator=Coordinator('X',feed,broker,controller,lambda:3600000,lambda:0,lambda:('1',False))
        coordinator.tick(); coordinator.tick()
        self.assertEqual(controller.risk_tick.call_count,1)
        self.assertEqual(broker.offer.call_count,1)
        coordinator.stop()
        broker.invalidate.assert_called_with('X'); controller.close.assert_called_once()

    def test_control_change_discards_finished_approval(self):
        s=snapshot(); feed=Mock(); feed.snapshot.return_value=s
        broker=Mock(); broker.drain.return_value=[]; broker.offer.return_value=True
        controller=Mock(); controller.status.return_value={'owner':'unified','state':None}
        controls=['1',False]
        c=Coordinator('X',feed,broker,controller,lambda:3600000,lambda:0,lambda:tuple(controls))
        c.tick(); candidate=broker.offer.call_args.args[0]
        broker.drain.return_value=[dict(candidate_id=candidate['id'])]
        controls[0]='2'; controls[1]=True
        c.tick()
        controller.entry.assert_not_called(); controller.management.assert_not_called()

if __name__=='__main__': unittest.main()
