from types import SimpleNamespace
import math
import pytest
import core_profit_fee_evidence as m
import trader

def row(fee,*,symbol='BTC/USDT:USDT',qty=2,price=81000,
        proof=True,source='okx_realized'):
    return dict(symbol=symbol,type='close',reason='stop_loss',dry_run=False,
        pnl_source=source,amount=qty,close_price=price,fee=fee,
        close_execution_evidence=dict(price_basis='verified_owned_protection_fills')
            if proof else None)

def test_fee_uplift_only_with_three_proven_exchange_stops():
    # BTC contract size 0.01; fee 1.88 on 1620 notional = 0.11605%.
    rows=[row(1.62),row(1.88),row(1.87),row(2.9,proof=False)]
    result=m.cost_pct_from_proven_stops(rows,'BTC/USDT:USDT',.01)
    assert result['confirmed'] and result['count']==3
    assert 0.12<result['fee_pct']<0.14
    assert not m.cost_pct_from_proven_stops(rows[:2],'BTC/USDT:USDT',.01)['confirmed']

def test_live_market_geometric_fallback_never_weakens_original_stop():
    base=.10
    recommended=.13
    pct,why=m.select_market_feasible_fee_pct('short',100,99.7,base,recommended)
    assert pct==recommended and why=='higher_cost_floor_feasible'
    assert trader._core_profit_floor_target('short',100,99.7,110,.8,fee_pct=pct)==pytest.approx(99.87)
    pct,why=m.select_market_feasible_fee_pct('short',100,99.9,base,recommended)
    assert pct==base and why=='higher_cost_floor_not_yet_feasible'
    assert trader._core_profit_floor_target('short',100,99.7,99.8,.8,fee_pct=recommended)==99.8

def test_wrong_symbol_unproved_estimated_cost_and_invalid_px_ignored():
    rows=[row(3,source='estimated'),row(2,proof=False),row(2,symbol='ETH/USDT:USDT'),
          row(2,price=float('nan'))]
    result=m.cost_pct_from_proven_stops(rows,'BTC/USDT:USDT',.01)
    assert not result['confirmed']
    assert result['fee_pct']==.1

def test_fee_upward_cap_prevents_unbounded_tightening():
    rows=[row(4.8) for _ in range(3)]
    assert m.cost_pct_from_proven_stops(rows,'BTC/USDT:USDT',.01)['fee_pct']<=.25

def test_both_directions_fallback_when_mark_not_beyond_enhanced_floor():
    assert m.select_market_feasible_fee_pct('long',100,100.12,.1,.13)[0]==.1
    assert m.select_market_feasible_fee_pct('long',100,100.2,.1,.13)[0]==.13
    assert m.select_market_feasible_fee_pct('short',100,99.88,.1,.13)[0]==.1
    assert m.select_market_feasible_fee_pct('short',100,99.7,.1,.13)[0]==.13
