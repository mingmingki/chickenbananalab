import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from contextlib import nullcontext
from copy import deepcopy
from core_unified_inventory import LegacyInventoryPort
from core_unified_handoff import Handoff
from core_unified_store import Store


class ReadOnlyExchange:
    def __init__(self):
        self.positions=[]
        self.orders=[]
        self.trade_order=dict(id='order',symbol='X',clientOrderId='client',status='closed',filled='2',remaining='0')
        self.trades=[dict(id='fill',order='order',symbol='X',amount='2',price='100')]
    def fetch_positions(self,symbols): return deepcopy(self.positions)
    def fetch_open_orders(self,symbol): return deepcopy(self.orders)
    def fetch_order(self,order_id,symbol,params): return deepcopy(self.trade_order)
    def fetch_my_trades(self,symbol,since,limit,params): return deepcopy(self.trades)
    def __getattr__(self,name): raise AssertionError('Unexpected exchange operation: '+name)


class InventoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name)
        self.exchange=ReadOnlyExchange()
        self.cfg=SimpleNamespace(user_dir=str(self.root),ENABLED_SYMBOLS=['X'],CANDIDATE_C_SYMBOLS=['Y'])
        self.client=SimpleNamespace(symbol='X',exchange=self.exchange,fetch_pending_protection_orders=lambda:[])
        self.port=LegacyInventoryPort(self.cfg,self.client,lambda:1000,lock_factory=nullcontext)

    def tearDown(self): self.tmp.cleanup()

    def write(self,name,data): (self.root/name).write_text(json.dumps(data))

    def test_existing_client_contract_reports_flat_without_exchange_writes(self):
        inventory=self.port.inventory('X')
        self.assertIsNotNone(inventory)
        self.assertIsNone(inventory['position'])
        self.assertEqual(inventory['orders'],[])
        self.assertFalse(inventory['legacy_pending'])
        with StoreContext(self.root/'test.db') as store:
            h=Handoff(store,self.port,lambda:1000)
            self.assertTrue(h.transfer('X',source='legacy',target='unified')['ok'])

    def test_old_pending_orders_and_manual_close_are_preserved(self):
        for name,data in [('reduce_v2_state.json',{'X':{'pending_order':{'id':'r'}}}),
                          ('core_add_position_state.json',{'X':{'pending_order':{'id':'a'}}}),
                          ('core_manual_close.json',{'X':{'status':'confirmed','cleanup_pending':True}})]:
            with self.subTest(name=name):
                self.write(name,data)
                before=(self.root/name).read_bytes()
                inventory=self.port.inventory('X')
                self.assertIsNotNone(inventory)
                self.assertTrue(inventory['legacy_pending'])
                self.assertEqual((self.root/name).read_bytes(),before)
                (self.root/name).unlink()

    def test_unknown_positions_or_broken_ledger_are_not_flat(self):
        self.exchange.positions=None
        with self.assertRaises(ValueError): self.port.inventory('X')

    def test_confirmed_journaled_cooldown_is_not_unresolved_order(self):
        record={'X':{'status':'confirmed','confirmed_at':1,'release_at':9999999999,'journaled':True}}
        self.write('core_manual_close.json',record)
        before=(self.root/'core_manual_close.json').read_bytes()
        self.assertFalse(self.port.inventory('X')['legacy_pending'])
        self.assertEqual((self.root/'core_manual_close.json').read_bytes(),before)
        self.exchange.positions=[]
        (self.root/'reduce_v2_state.json').write_text('{invalid')
        with self.assertRaises(ValueError): self.port.inventory('X')

    def test_hedged_positions_are_not_hidden_by_first_position_lookup(self):
        self.exchange.positions=[dict(symbol='X',side='long',contracts=2),
                                 dict(symbol='X',side='short',contracts=2)]
        inventory=self.port.inventory('X')
        self.assertIsNotNone(inventory)
        self.assertIsNotNone(inventory['position'])

    def test_candidate_c_overlap_never_claims_core_symbol(self):
        self.cfg.CANDIDATE_C_SYMBOLS=['X']
        inventory=self.port.inventory('X')
        self.assertIsNotNone(inventory)
        self.assertFalse(inventory['core_owns_symbol'])

    def test_lookup_uses_actual_client_id_and_complete_fill_history(self):
        order=self.port.lookup('client',500)
        self.assertIsNotNone(order)
        self.assertEqual(order['filled'],'2')
        self.exchange.trade_order['clientOrderId']='different'
        with self.assertRaises(ValueError): self.port.lookup('client',500)


class StoreContext:
    def __init__(self,path): self.path=path
    def __enter__(self): self.store=Store(self.path); return self.store
    def __exit__(self,*args): self.store.close()


if __name__=='__main__': unittest.main()
