import json
import pytest

from trade_learning_lifecycle import build_completed_lifecycles


def write_rows(tmp_path, rows):
    (tmp_path / 'trades_log.jsonl').write_text(
        '\n'.join(json.dumps(r) for r in rows) + '\n', encoding='utf-8')


def test_open_reduce_close_is_one_completed_trade(tmp_path):
    write_rows(tmp_path, [
        {'type':'open','symbol':'BTC/USDT:USDT','side':'long','price':100.0,'amount':10.0,'dry_run':False,'strategy_group':'core','time':'2026-09-24T10:00:00'},
        {'type':'reduce','symbol':'BTC/USDT:USDT','side':'long','entry_price':100.0,'amount':4.0,'pnl':8.0,'fee':1.0,'okx_net_pnl':6.5,'dry_run':False,'strategy_group':'core','time':'2026-09-24T10:10:00'},
        {'type':'close','symbol':'BTC/USDT:USDT','side':'long','entry_price':100.0,'amount':6.0,'pnl':12.0,'fee':1.5,'okx_net_pnl':10.0,'dry_run':False,'strategy_group':'core','time':'2026-09-24T10:20:00'},
    ])
    trades = build_completed_lifecycles(str(tmp_path))
    assert len(trades) == 1
    assert [e['type'] for e in trades[0]['events']] == ['open','reduce','close']
    assert trades[0]['net_pnl'] == pytest.approx(16.5)
    assert trades[0]['holding_minutes'] == pytest.approx(20.0)


def test_adds_remain_inside_same_lifecycle(tmp_path):
    write_rows(tmp_path, [
        {'type':'open','symbol':'ETH/USDT:USDT','side':'short','price':200.0,'amount':2.0,'dry_run':False,'strategy_group':'core','time':'2026-09-24T11:00:00'},
        {'type':'add','symbol':'ETH/USDT:USDT','side':'short','add_price':205.0,'add_amount':1.0,'new_total_amount':3.0,'new_entry_price':201.67,'strategy_group':'core','time':'2026-09-24T11:05:00'},
        {'type':'close','symbol':'ETH/USDT:USDT','side':'short','entry_price':201.67,'amount':3.0,'pnl':9.0,'fee':1.0,'okx_net_pnl':8.0,'dry_run':False,'strategy_group':'core','time':'2026-09-24T11:30:00'},
    ])
    trades = build_completed_lifecycles(str(tmp_path))
    assert len(trades) == 1
    assert sum(e['type']=='add' for e in trades[0]['events']) == 1


def test_unmatched_legacy_close_survives_as_partial_coverage(tmp_path):
    write_rows(tmp_path, [
        {'type':'close','symbol':'XRP/USDT:USDT','side':'long','entry_price':1.0,'amount':100.0,'pnl':2.0,'fee':0.2,'okx_net_pnl':1.7,'dry_run':False,'time':'2026-09-24T12:00:00'}
    ])
    trade = build_completed_lifecycles(str(tmp_path))[0]
    assert trade['coverage'] == 'partial'
    assert trade['net_pnl'] == pytest.approx(1.7)


def test_zero_reduce_reconciliation_fields(tmp_path):
    write_rows(tmp_path, [
        {'type':'open','symbol':'BTC/USDT:USDT','side':'long','price':100.0,'amount':2.0,'dry_run':False,'strategy_group':'core','time':'2026-09-24T13:00:00'},
        {'type':'close','symbol':'BTC/USDT:USDT','side':'long','entry_price':100.0,'amount':2.0,'pnl':5.0,'fee':0.5,'okx_net_pnl':4.4,'reason':'take_profit','dry_run':False,'strategy_group':'core','time':'2026-09-24T13:30:00'},
    ])
    life = build_completed_lifecycles(str(tmp_path))[0]
    assert life['lifecycle_net'] == pytest.approx(4.4)
    assert life['final_close_net'] == pytest.approx(4.4)
    assert life['reduce_net'] == pytest.approx(0.0)
    assert life['difference_due_to_reduces'] == pytest.approx(0.0)
    assert life['final_close_reason'] == 'take_profit'


def test_two_reduces_reconcile_to_lifecycle_net(tmp_path):
    write_rows(tmp_path, [
        {'type':'open','symbol':'XRP/USDT:USDT','side':'long','price':1.55,'amount':16.12,'dry_run':False,'strategy_group':'core','time':'2026-09-25T11:09:00'},
        {'type':'reduce','symbol':'XRP/USDT:USDT','side':'long','entry_price':1.55,'amount':4.03,'pnl':-6.5,'fee':0.25,'okx_net_pnl':-6.75,'dry_run':False,'strategy_group':'core','time':'2026-09-25T11:38:00'},
        {'type':'reduce','symbol':'XRP/USDT:USDT','side':'long','entry_price':1.55,'amount':4.03,'pnl':-9.6,'fee':0.30,'okx_net_pnl':-9.90,'dry_run':False,'strategy_group':'core','time':'2026-09-25T11:48:00'},
        {'type':'close','symbol':'XRP/USDT:USDT','side':'long','entry_price':1.55,'amount':8.06,'pnl':-18.0,'fee':0.63,'okx_net_pnl':-18.63,'reason':'position_ai_close_all','dry_run':False,'strategy_group':'core','time':'2026-09-25T13:24:00'},
    ])
    life = build_completed_lifecycles(str(tmp_path))[0]
    assert life['lifecycle_net'] == pytest.approx(-35.28, abs=.02)
    assert life['reduce_net'] == pytest.approx(-16.65, abs=.02)
    assert life['final_close_net'] == pytest.approx(-18.63, abs=.02)
    assert life['difference_due_to_reduces'] == pytest.approx(-16.65, abs=.02)
    assert life['net_pnl'] == life['lifecycle_net']


def test_partial_close_keeps_partial_coverage_and_reconciliation(tmp_path):
    write_rows(tmp_path, [
        {'type':'close','symbol':'PI/USDT:USDT','side':'short','entry_price':0.09,'amount':1000.0,'pnl':-3.0,'fee':0.5,'okx_net_pnl':-3.4,'reason':'external_close_unknown','dry_run':False,'time':'2026-09-25T12:00:00'}
    ])
    life = build_completed_lifecycles(str(tmp_path))[0]
    assert life['coverage'] == 'partial'
    assert life['lifecycle_net'] == pytest.approx(-3.4)
    assert life['final_close_net'] == pytest.approx(-3.4)
    assert life['reduce_net'] == pytest.approx(0.0)
    assert life['final_close_reason'] == 'external_close_unknown'
