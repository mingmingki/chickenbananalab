import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from datetime import date

import trade_log
import pnl_reconciliation as pnl

STAMP = 1789935523857
SYMBOL = 'ETH/USDT:USDT'


class UnifiedHistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)
        self.db = sqlite3.connect(self.path / 'core_unified.sqlite3')
        self.addCleanup(self.db.close)
        self.db.execute('CREATE TABLE accounting (trade_id TEXT,intent_id TEXT,payload TEXT)')
        self.db.execute('CREATE TABLE intents (id TEXT,status TEXT,payload TEXT)')
        self.seq = 0

    def fill(self, kind, qty, price, intent=None, side='long', life='life'):
        self.seq += 1
        r = dict(trade_id=str(self.seq), intent_id=intent or str(self.seq),
                 symbol=SYMBOL, position_id=life, kind=kind, side=side,
                 qty=str(qty), price=str(price), contract_size='0.1',
                 fee_rate='0.001', filled_ms=STAMP+self.seq)
        self.db.execute('INSERT INTO accounting VALUES(?,?,?)',
                        (r['trade_id'], r['intent_id'], json.dumps(r)))
        self.db.commit()
        return r

    def test_probe_exit_is_visible_once_without_legacy_file(self):
        self.fill('ENTRY', '1.26', '2630.81', 'entry')
        self.fill('EXIT', '.26', '2630.66', 'exit')
        self.fill('EXIT', '1', '2630.66', 'exit')
        rows = trade_log.load_closed_trades(str(self.path), False)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows, trade_log.load_closed_trades(str(self.path), False))
        self.assertAlmostEqual(rows[0]['pnl'], -.0189)
        self.assertEqual(rows[0]['amount'], 1.26)
        self.assertIn('선행진입', rows[0]['reason'])
        self.assertTrue(rows[0]['time'].startswith('2026-09-21T05:18:43'))
        self.assertEqual(trade_log.load_closed_trades(str(self.path), True), [])
        self.assertFalse((self.path/'trades_log.jsonl').exists())

    def test_add_partial_and_final_exit_use_remaining_weighted_basis(self):
        self.fill('ENTRY', 2, 100)
        self.fill('REDUCE', 1, 90)
        self.fill('ADD', 1, 120)
        self.fill('EXIT', 2, 100)
        rows = pnl.load_all_records_with_reduces(str(self.path))
        self.assertEqual([r['type'] for r in rows], ['close', 'reduce'])
        self.assertAlmostEqual(rows[0]['entry_price'], 110)
        self.assertAlmostEqual(sum(r['pnl'] for r in rows), -3)
        # Entry notional 20+12, exit notional 9+20, each side 0.05%.
        self.assertAlmostEqual(sum(r['fee'] for r in rows), .0305)
        self.assertEqual(pnl.summary(str(self.path))['core']['count'], 1)

    def test_actual_order_fees_replace_estimate_without_duplicate_records(self):
        self.fill('ENTRY', 2, 100, 'entry')
        self.fill('REDUCE', 1, 110, 'reduce')
        self.fill('EXIT', 1, 90, 'exit')
        orders = []
        for intent, qty, price, fee in [('entry',2,100,-.012),('reduce',1,110,-.007),('exit',1,90,.001)]:
            orders.append(dict(order_id='okx-'+intent,client_order_id=intent,
                inst_id='ETH-USDT-SWAP',qty=qty,fee_usdt=fee,fee_ccy='USDT',
                price=price,leverage=5,contract_size=.1,margin_usdt=qty*price*.1/5))
        (self.path/'okx_margin_return_state.json').write_text(json.dumps(dict(orders=orders)))
        rows = pnl.load_all_records_with_reduces(str(self.path))
        self.assertEqual(len(rows), 2)
        self.assertAlmostEqual(sum(r['fee'] for r in rows), .018)
        self.assertEqual({r['fee_source'] for r in rows}, {'okx_order_fees'})
        self.assertEqual(rows[0]['exchange_order_id'], 'okx-exit')
        self.assertAlmostEqual(rows[0]['fixed_margin_usdt'], 4)

    def test_short_exit_and_daily_loss_not_counted_twice(self):
        self.fill('ENTRY', 1, 100, side='short')
        self.fill('EXIT', 1, 110, side='short')
        (self.path/'trades_log.jsonl').write_text(json.dumps(dict(type='close',
            time='2026-09-21T05:00:00',symbol=SYMBOL,side='long',dry_run=False,
            strategy_group='core',pnl=-2,fee=0))+'\n')
        rows = pnl.load_all_records(str(self.path))
        self.assertEqual(len(rows), 2)
        self.assertAlmostEqual(pnl.realized_pnl_for_kst_date(str(self.path),'core',date(2026,9,21)), -3.0105)
        self.assertEqual(pnl.realized_pnl_for_kst_date(str(self.path),'core',date(2026,9,21),include_unified=False), -2)

    def test_partial_exit_becomes_single_close_after_later_fills(self):
        self.fill('ENTRY', 2, 100, 'entry')
        self.fill('EXIT', 1, 90, 'exit')
        self.assertEqual(len(trade_log.load_reduces(str(self.path),False)), 1)
        self.assertEqual(trade_log.load_closed_trades(str(self.path),False), [])
        self.fill('EXIT', 1, 90, 'exit')
        self.assertEqual(trade_log.load_reduces(str(self.path),False), [])
        self.assertEqual(len(trade_log.load_closed_trades(str(self.path),False)), 1)

    def test_unknown_basis_must_not_invent_profit(self):
        self.fill('EXIT', 1, 100)
        with self.assertRaises(ValueError):
            trade_log.load_closed_trades(str(self.path),False)

    def test_same_exchange_fill_replayed_does_not_duplicate_history(self):
        self.fill('ENTRY', 1, 100)
        r = self.fill('EXIT', 1, 90)
        self.db.execute('INSERT INTO accounting VALUES(?,?,?)',
            (r['trade_id'],r['intent_id'],json.dumps(r)))
        self.db.commit()
        self.assertEqual(len(trade_log.load_closed_trades(str(self.path),False)),1)

    def test_conflicting_duplicate_fill_is_rejected(self):
        self.fill('ENTRY',1,100)
        r = self.fill('EXIT',1,90)
        r['price'] = '110'
        self.db.execute('INSERT INTO accounting VALUES(?,?,?)',
            (r['trade_id'],r['intent_id'],json.dumps(r)))
        self.db.commit()
        with self.assertRaises(ValueError):
            trade_log.load_closed_trades(str(self.path),False)

    def test_exact_margin_identity_rejects_nearby_other_order(self):
        # Run with installed project dependencies in the release environment.
        import okx_margin_return as margin
        record = dict(history_source='core_unified',exchange_order_id='mine',
            symbol=SYMBOL,side='long',time='2026-09-21T05:18:43',amount=1,pnl=0)
        event = dict(order_id='other',inst_id='ETH-USDT-SWAP',position_side='long',
            ts_ms=STAMP,closed_qty=1,gross_pnl_usdt=0)
        self.assertIsNone(margin._record_event_pair_score(record,event))

if __name__ == '__main__':
    unittest.main()
