"""v33-74 rolling CORE profit-risk sizing; no entry pause and no SL/TP changes."""
import datetime as dt
from pathlib import Path
import math
import pytest
import core_profit_risk_allocator_v33_74 as a

KST=dt.timezone(dt.timedelta(hours=9))
NOW=dt.datetime(2026,10,20,17,0,tzinfo=KST)

def row(symbol,days,net):
    when=NOW-dt.timedelta(days=days)
    return dict(_group='core',symbol=symbol, type='close',
        time=when.strftime('%Y-%m-%dT%H:%M:%S'),dry_run=False,
        _profitability_net=net)

def negatives(sym):
    # Both windows have 10 losses and 3 wins, PF 0.30, net -7.0.
    return ([row(sym,5,-1.) for _ in range(10)]+[row(sym,6,1.) for _ in range(3)]+
            [row(sym,1,-1.) for _ in range(10)]+[row(sym,2,1.) for _ in range(3)])

def test_sustained_negative_edge_halves_but_never_pauses():
    r=a.decide(negatives('BTC/USDT:USDT'),'BTC/USDT:USDT',now=NOW)
    assert r['factor']==0.5 and r['reason']=='two_window_negative_edge'
    assert r['prior']['n']==13 and r['recent']['n']==13

def test_one_winning_window_keeps_approved_risk():
    rs=negatives('ETH/USDT:USDT')
    for r in rs:
        if r['time']<'2026-10-16':r['_profitability_net']=3
    assert a.decide(rs,'ETH/USDT:USDT',now=NOW)['factor']==1

def test_small_samples_never_trigger():
    rs=negatives('XRP/USDT:USDT')[:7]
    assert a.decide(rs,'XRP/USDT:USDT',now=NOW)['factor']==1

def test_future_records_never_leak_into_decision():
    sym='BTC/USDT:USDT'
    rs=negatives(sym)
    rs.extend([row(sym,-1,1000),row(sym,9,-1000)])
    assert a.decide(rs,sym,now=NOW)['factor']==.5

def test_unrelated_group_or_dry_runs_not_counted():
    sym='BTC/USDT:USDT'
    rs=negatives(sym)
    for r in rs: r['dry_run']=True
    assert a.decide(rs,sym,now=NOW)['factor']==1
    for r in rs:r['dry_run']=False;r['_group']='candidate_c'
    assert a.decide(rs,sym,now=NOW)['factor']==1

def test_unknown_or_unrelated_symbols_never_scaled():
    rs=negatives('BTC/USDT:USDT')
    assert a.decide(rs,'DOGE/USDT:USDT',now=NOW)['factor']==1
    assert a.decide(rs,'ETH/USDT:USDT',now=NOW)['factor']==1

def test_incomplete_net_is_not_fake_zero():
    rs=negatives('BTC/USDT:USDT')
    for r in rs[:18]:r.pop('_profitability_net')
    assert a.decide(rs,'BTC/USDT:USDT',now=NOW)['factor']==1

def test_net_pf_must_be_low_in_both_windows():
    sym='XRP/USDT:USDT';rs=negatives(sym)
    for r in rs:
        if r['time']<'2026-10-16' and r['_profitability_net']>0:
            r['_profitability_net']=10
    assert a.decide(rs,sym,now=NOW)['factor']==1

def test_decision_requires_timezone():
    assert a.decide(negatives('BTC/USDT:USDT'),'BTC/USDT:USDT',
                    now=NOW.replace(tzinfo=None))['factor']==1

def test_live_evidence_matches_known_profitability_and_never_exceeds_approval():
    user='/opt/autotrader/users/chickenbananalab'
    a._cache.clear()
    vals={name:a.decision_from_live(user,name+'/USDT:USDT') for name in ('BTC','XRP','ETH','ADA')}
    assert vals['BTC']['factor']==.5
    assert vals['XRP']['factor']==.5
    assert vals['ETH']['factor']==1
    assert vals['ADA']['factor']==1
    assert all(0.5<=v['factor']<=1 for v in vals.values())
    assert all(v['prior']['n']>=10 and v['recent']['n']>=10 for v in vals.values())

def test_core_order_code_only_changes_preapproved_risk_budget():
    text=Path('trader.py').read_text()
    assert "profit_risk_allocation(cfg.user_dir,symbol)" in text
    assert "budget *= factor" in text
    assert text.index("budget *= factor") < text.index("sizing = solve_risk_capped_size(fresh_context,stop,exposure)")
    assert "if decision.get('manual_entry')" in text
    assert "loss > budget+1e-9" in text
    assert "return blocked('invalid_profitability_risk_factor')" in text

def test_candidate_c_trading_source_bitwise_unchanged():
    previous=Path('/opt/autotrader-releases/candidate_c_tail_loss_shadow_v33_73_20261020KST')
    for src in ['candidate_c_trader_adapter.py','candidate_c_hybrid_live_adapter.py',
                'candidate_c_decision_engine.py','risk_manager.py','okx_client.py',
                'adaptive_exit_policy.py','candidate_c_hybrid_cycle.py']:
        assert Path(src).read_bytes()==(previous/src).read_bytes()
