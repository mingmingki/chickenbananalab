import tempfile
import unittest
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from threading import RLock
from core_unified_store import Store
from core_unified_handoff import Handoff


class HandoffPort:
    def __init__(self):
        self.lock=RLock()
        self.position=None
        self.orders=[]
        self.protections=[]
        self.legacy_pending=False
        self.read_error=False
        self.calls=0
        self.changed_on_second_read=False

    @contextmanager
    def locked(self):
        with self.lock: yield

    def inventory(self,symbol):
        if self.read_error: raise TimeoutError('unknown')
        self.calls+=1
        position=self.position
        if self.changed_on_second_read and self.calls>=2:
            position={'contracts':'1','side':'long','position_id':'external'}
        return deepcopy(dict(symbol=symbol,position=position,orders=self.orders,
                             protections=self.protections,legacy_pending=self.legacy_pending,
                             core_owns_symbol=True,observed_ms=1000))


class HandoffTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.path=Path(self.tmp.name)/'ledger.db'
        self.store=Store(self.path)
        self.port=HandoffPort()
        self.handoff=Handoff(self.store,self.port,lambda:1000)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_claim_persists_and_prevents_other_owner_after_restart(self):
        self.assertEqual(self.handoff.owner('X'),'legacy')
        self.assertTrue(self.handoff.transfer('X',source='legacy',target='unified')['ok'])
        self.store.close()
        self.store=Store(self.path)
        self.handoff=Handoff(self.store,self.port,lambda:1000)
        self.assertEqual(self.handoff.owner('X'),'unified')
        self.assertFalse(self.handoff.transfer('X',source='legacy',target='unified')['ok'])

    def test_any_exchange_inventory_blocks_handoff(self):
        for name,value in [('position',{'contracts':'1','side':'long','position_id':'old'}),
                           ('orders',[{'id':'working'}]),('protections',[{'algoId':'old'}]),
                           ('legacy_pending',True)]:
            with self.subTest(name=name):
                old=getattr(self.port,name)
                setattr(self.port,name,value)
                self.assertFalse(self.handoff.transfer('X',source='legacy',target='unified')['ok'])
                self.assertEqual(self.handoff.owner('X'),'legacy')
                setattr(self.port,name,old)

    def test_read_failure_never_means_empty(self):
        self.port.read_error=True
        self.assertEqual(self.handoff.transfer('X',source='legacy',target='unified')['reason'],
                         'inventory_unknown')
        self.assertEqual(self.handoff.owner('X'),'legacy')

    def test_new_external_position_during_inventory_blocks_claim(self):
        self.port.changed_on_second_read=True
        self.assertFalse(self.handoff.transfer('X',source='legacy',target='unified')['ok'])
        self.assertEqual(self.handoff.owner('X'),'legacy')

    def test_unified_pending_prevents_rollback(self):
        self.assertTrue(self.handoff.transfer('X',source='legacy',target='unified')['ok'])
        self.store.save_state('X',dict(filled_qty='0',side='long',position_id='p'),None)
        self.store.reserve('X',dict(id='entry',kind='ENTRY',qty='1',side='long'),0)
        self.assertFalse(self.handoff.transfer('X',source='unified',target='legacy')['ok'])
        self.assertEqual(self.handoff.owner('X'),'unified')

    def test_explicit_flat_rollback_and_generation_invalidation(self):
        first=self.handoff.transfer('X',source='legacy',target='unified')
        self.assertTrue(first['ok'])
        second=self.handoff.transfer('X',source='unified',target='legacy')
        self.assertTrue(second['ok'])
        self.assertGreater(second['generation'],first['generation'])
        self.assertEqual(self.handoff.owner('X'),'legacy')

    def test_lost_owner_record_cannot_fall_back_to_legacy_with_pending_order(self):
        self.store.save_state('X',dict(filled_qty='0',side='long',position_id='p'),None)
        self.store.reserve('X',dict(id='entry',kind='ENTRY',qty='1',side='long'),0)
        with self.assertRaises(ValueError): self.handoff.owner('X')

    def test_compare_and_swap_allows_only_one_owner_transfer(self):
        second=Store(self.path)
        try:
            self.assertEqual(self.store.transfer_owner('X','legacy','unified',0,{}),1)
            self.assertIsNone(second.transfer_owner('X','legacy','unified',0,{}))
            self.assertEqual(second.owner_record('X')['owner'],'unified')
        finally: second.close()

    def test_zero_quantity_reconciling_state_cannot_be_handed_off(self):
        self.store.save_state('X',dict(filled_qty='0',phase='RECONCILING',pending_intent=None),None)
        with self.assertRaises(ValueError): self.handoff.owner('X')
        self.assertFalse(self.handoff.transfer('X',source='legacy',target='unified')['ok'])

    def test_known_owner_cannot_rollback_with_unresolved_zero_state(self):
        self.assertTrue(self.handoff.transfer('X',source='legacy',target='unified')['ok'])
        self.store.save_state('X',dict(filled_qty='0',phase='RECONCILING',pending_intent=None),None)
        self.assertFalse(self.handoff.transfer('X',source='unified',target='legacy')['ok'])
        self.assertEqual(self.handoff.owner('X'),'unified')


if __name__=='__main__': unittest.main()
