import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from decimal import Decimal as D
from types import SimpleNamespace as NS
from core_unified_accounting import realized_for_day, allow_new_entry, journal_complete, KST

NOW=int(datetime(2026,9,20,18,tzinfo=timezone.utc).timestamp()*1000)
TODAY=datetime.fromtimestamp(NOW/1000,KST).date()


def fill(i,kind,qty,price,**kw):
    return dict(trade_id=str(i),intent_id='i'+str(i),symbol='BTC',position_id='life1',
                kind=kind,side='long',qty=str(qty),price=str(price),contract_size='1',
                filled_ms=NOW-10000+i,**kw)


class AccountingTests(unittest.TestCase):
    def test_partial_add_reduce_and_exit_use_actual_weighted_basis(self):
        records=[fill(1,'ENTRY',2,100),fill(2,'REDUCE',1,90),
                 fill(3,'ADD',1,120),fill(4,'EXIT',2,100)]
        # First reduce -10-.1; remaining average (100+120)/2=110.
        self.assertEqual(realized_for_day(records,TODAY,NOW),D('-30.32'))
        self.assertTrue(journal_complete(NS(accounting_records=lambda:records),'BTC','life1',clock_ms=lambda:NOW))
        self.assertFalse(journal_complete(NS(accounting_records=lambda:records[:2]),'BTC','life1',clock_ms=lambda:NOW))

    def test_short_and_kst_date_use_exit_timestamp(self):
        rows=[fill(1,'ENTRY',1,100),fill(2,'EXIT',1,90)]
        for r in rows: r['side']='short'
        # 15:00 UTC starts next KST date; entry prior-day does not change attribution.
        rows[0]['filled_ms']=int(datetime(2026,9,20,14,59,tzinfo=timezone.utc).timestamp()*1000)
        rows[1]['filled_ms']=int(datetime(2026,9,20,15,0,tzinfo=timezone.utc).timestamp()*1000)
        self.assertEqual(realized_for_day(rows,TODAY,NOW),D('9.9'))
        self.assertEqual(realized_for_day(rows,datetime(2026,9,20).date(),NOW),0)

    def test_combines_legacy_and_unified_loss(self):
        records=[fill(1,'ENTRY',1,100),fill(2,'EXIT',1,90)]
        cfg=NS(user_dir='fake',MAX_DAILY_LOSS_PCT=5,ACCOUNT_HARD_DAILY_LOSS_PCT=10)
        kwargs=dict(baseline_reader=lambda _:('ok',dict(trading_date=TODAY.isoformat(),start_equity=1000)),
                    legacy_reader=lambda *args:-40)
        store=NS(accounting_records=lambda:records)
        self.assertFalse(allow_new_entry(cfg,store,990,lambda:NOW,**kwargs))
        kwargs['legacy_reader']=lambda *args:-39
        self.assertTrue(allow_new_entry(cfg,store,990,lambda:NOW,**kwargs))
        self.assertFalse(allow_new_entry(cfg,store,899,lambda:NOW,**kwargs))
        kwargs['baseline_reader']=lambda _:('corrupted',None)
        self.assertFalse(allow_new_entry(cfg,store,990,lambda:NOW,**kwargs))

    def test_persisted_replay_after_restart_and_duplicate(self):
        records=[fill(1,'ENTRY',2,100),fill(2,'REDUCE',1,90)]
        with tempfile.NamedTemporaryFile(suffix='.sqlite3') as file:
            db=sqlite3.connect(file.name)
            db.execute('create table accounting(payload text)')
            db.executemany('insert into accounting values(?)',[(json.dumps(r),) for r in records]); db.commit(); db.close()
            db=sqlite3.connect(file.name)
            loaded=[json.loads(row[0]) for row in db.execute('select payload from accounting')]; db.close()
            self.assertEqual(realized_for_day(loaded,TODAY,NOW),D('-10.1'))
            self.assertEqual(realized_for_day(loaded+[loaded[-1]],TODAY,NOW),D('-10.1'))

    def test_store_atomic_fill_external_close_and_restart(self):
        from core_unified_store import Store
        with tempfile.TemporaryDirectory() as path:
            file=path+'/state.sqlite3'
            store=Store(file)
            state=dict(revision=0,filled_qty='0',position_id='life1',side='long',
                       contract_size='1',cost_rate='.001',stop_price='95')
            store.save_state('BTC',state,None)
            store.reserve('BTC',dict(id='open',kind='ENTRY',qty='2'),0)
            store.apply_fill('open','entry1','2','100',terminal=True,filled_ms=NOW-100)
            store.complete('open',protection_confirmed=True)
            revision=store.state('BTC')['revision']
            store.external_flat('BTC',revision,[dict(order_id='stop',trade_id='close1',qty='2',price='90',filled_ms=NOW-50)])
            store.close()
            store=Store(file)
            try:
                rows=store.accounting_records()
                self.assertEqual(realized_for_day(rows,TODAY,NOW),D('-20.2'))
                self.assertTrue(journal_complete(store,'BTC','life1',clock_ms=lambda:NOW))
                self.assertEqual(rows[-1]['fee_source'],'conservative_roundtrip_estimate')
            finally: store.close()

    def test_unknown_basis_timestamps_and_fee_floor_fail_closed(self):
        bad_sets=[[fill(1,'EXIT',1,90)],
                  [dict(fill(1,'ENTRY',1,100),filled_ms=None)],
                  [dict(fill(1,'ENTRY',1,100),filled_ms=NOW+1)],
                  [fill(1,'ENTRY',1,100),fill(2,'EXIT',1,90,fee_rate='.0001')]]
        for rows in bad_sets:
            with self.assertRaises((ValueError,TypeError)):
                realized_for_day(rows,TODAY,NOW)
            self.assertFalse(journal_complete(NS(accounting_records=lambda:rows),'BTC','life1',clock_ms=lambda:NOW))

if __name__=='__main__': unittest.main()
