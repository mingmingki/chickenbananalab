import json
import tempfile
import unittest
from decimal import Decimal
from types import SimpleNamespace as NS
from threading import Event
from core_unified_store import Store
from core_unified_live import LiveController
from core_unified_adapters import _position_summary


class ChurnTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=self.tmp.name+'/journal.sqlite3'
        self.store=Store(self.path);self.addCleanup(lambda:self.store.close())
        self.store.transfer_owner('X','legacy','unified',0,{})
        self.now=2000000
        self.cfg=NS(MIN_HOLD_MINUTES=15,POLL_INTERVAL_SECONDS=300)
        self.client=NS(symbol='X')
        self.c=LiveController(self.cfg,None,self.client,'X',None,self.store,lambda:self.now,Event())
        self.candidate=dict(id='decision',purpose='ENTRY',side='long',signal_ms=self.now)

    def fill(self,i,kind,qty,price,stamp):
        r=dict(trade_id=str(i),intent_id='order'+str(i),symbol='X',position_id='life',
            side='long',kind=kind,qty=str(qty),price=str(price),contract_size='1',
            fee_rate='.001',filled_ms=stamp)
        self.store.db.execute('INSERT INTO accounting VALUES(?,?,?)',(r['trade_id'],r['intent_id'],json.dumps(r)))
        self.store.db.commit()

    def test_recent_close_blocks_reentry_and_persists_after_restart(self):
        self.assertTrue(callable(getattr(self.c,'reentry_reason',None)))
        self.fill(1,'ENTRY',2,100,1000000);self.fill(2,'EXIT',2,99,1900000)
        self.assertEqual(self.c.reentry_reason(self.candidate),'reentry_cooldown')
        self.store.close();self.store=Store(self.path);self.c.store=self.store
        self.assertEqual(self.c.reentry_reason(self.candidate),'reentry_cooldown')
        self.now=2800000
        self.assertEqual(self.c.reentry_reason(self.candidate),'fresh_entry_review_required')
        self.candidate['signal_ms']=self.now
        self.assertIsNone(self.c.reentry_reason(self.candidate))

    def test_only_bound_opposite_reversal_can_skip_reentry_wait(self):
        self.assertTrue(callable(getattr(self.c,'reentry_reason',None)))
        self.fill(1,'ENTRY',2,100,1000000);self.fill(2,'EXIT',2,99,1900000)
        self.candidate.update(purpose='REVERSE',side='short')
        self.assertEqual(self.c.reentry_reason(self.candidate),'reentry_cooldown')
        self.c.pending_reverse=(dict(self.candidate),{},'life')
        self.assertIsNone(self.c.reentry_reason(self.candidate))
        self.c.pending_reverse=(dict(self.candidate),{},'other-life')
        self.assertEqual(self.c.reentry_reason(self.candidate),'reentry_cooldown')

    def test_reduction_does_not_create_flat_entry_cooldown(self):
        self.assertTrue(callable(getattr(self.c,'reentry_reason',None)))
        self.fill(1,'ENTRY',2,100,1000000);self.fill(2,'REDUCE',1,99,1900000)
        self.assertIsNone(self.c.reentry_reason(self.candidate))

    def test_ai_gets_weighted_cost_basis_and_estimated_net_after_add_reduce(self):
        self.fill(1,'ENTRY',2,100,1000000);self.fill(2,'REDUCE',1,90,1100000)
        self.fill(3,'ADD',1,120,1200000)
        state=dict(position_id='life',phase='ACTIVE',side='long',filled_qty='2',initial_entry='100')
        self.store.save_state('X',dict(state,revision=0),None)
        self.c.enabled=lambda:True
        p=self.c.status()['state']
        self.assertIn('average_entry_price',p)
        self.assertEqual(Decimal(str(p['average_entry_price'])),Decimal('110'))
        summary=_position_summary(p,dict(now_ms=self.now,frames={'1m':[dict(close=110)]}))
        self.assertAlmostEqual(summary['estimated_net_pnl_usdt'], -.22)
        self.assertAlmostEqual(summary['estimated_break_even_price'], 110.055/0.9995,places=3)
        self.assertFalse(summary['funding_included'])

    def test_live_entry_guard_enforces_cooldown_before_other_preflights(self):
        self.fill(1,'ENTRY',2,100,1000000);self.fill(2,'EXIT',2,99,1900000)
        self.c.review_settings_guard=lambda candidate:None
        self.assertEqual(self.c.entry_guard(self.candidate),'reentry_cooldown')

    def test_missing_inventory_never_fabricates_net_pnl(self):
        self.store.save_state('X',dict(position_id='missing',phase='PROBE',side='long',
            filled_qty='2',initial_entry='100',revision=0),None)
        self.c.enabled=lambda:True
        p=self.c.status()['state']
        self.assertFalse(p['cost_basis_known'])
        summary=_position_summary(p,dict(now_ms=self.now,frames={'1m':[dict(close=110)]}))
        self.assertIsNone(summary['estimated_net_pnl_usdt'])
