import unittest
from types import SimpleNamespace
from test_handoff import HandoffTests
import core_unified_service as service


class RollbackTests(HandoffTests):
    def step(self):
        self.assertTrue(hasattr(service, 'rollback_step'), 'rollback drain is missing')
        c=SimpleNamespace(symbol='X', store=self.store, handoff=self.handoff)
        return service.rollback_step(c)

    def test_flat_transfers_back_and_stays_legacy(self):
        self.handoff.transfer('X', source='legacy', target='unified')
        self.assertTrue(self.step()['ok'])
        self.assertEqual(self.store.owner_record('X')['owner'], 'legacy')
        self.assertTrue(self.step()['ok'])

    def test_held_position_is_not_transferred(self):
        self.handoff.transfer('X', source='legacy', target='unified')
        self.port.position={'contracts':1}
        self.assertFalse(self.step()['ok'])
        self.assertEqual(self.store.owner_record('X')['owner'], 'unified')

    def test_unresolved_order_remains_owned(self):
        self.handoff.transfer('X', source='legacy', target='unified')
        self.store.save_state('X',dict(filled_qty='0',side='long',position_id='p'),None)
        self.store.reserve('X',dict(id='entry',kind='ENTRY',qty='1',side='long'),0)
        self.assertFalse(self.step()['ok'])
        self.assertIsNotNone(self.store.pending('X'))

    def test_unknown_exchange_inventory_blocks_transfer(self):
        self.handoff.transfer('X', source='legacy', target='unified')
        self.port.read_error=True
        self.assertFalse(self.step()['ok'])
        self.assertEqual(self.store.owner_record('X')['owner'],'unified')
