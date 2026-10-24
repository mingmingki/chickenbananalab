"""v33-9 profitability evidence must be exact, and OCO amend must be monotonic."""
from types import SimpleNamespace
import logging
import pytest
import trader
import core_partial_profit_residual_guard as guard
import reduce_v2_state
from test_core_strong_mfe_floor_v274 import env, apply, short

S='XRP/USDT:USDT'

def evidence(eid='red1',net=1.25):
    return dict(type='reduce',symbol=S,side='long',strategy_group='core',
      dry_run=False,pnl_source='order_fills',execution_id=eid,
      entry_price=100.,amount=2.,close_price=104.,okx_net_pnl=net,
      time='2026-10-14T03:00:10+09:00',
      close_execution_evidence=dict(price_basis='verified_order_fills',
        order_id='ord1',trade_ids=['tr1'],vwap=104.,filled_contracts=2.))

def state(pos):
    return dict(lifecycle_id=reduce_v2_state.position_identity(pos),
        baseline_known=True,actual_reduced_contracts=2.,executed_ids=['red1'],
        pending_order=None)

def test_verified_positive_reduce_arms_residual_floor_below_mfe(env,monkeypatch,caplog):
    e=env
    e.obs.update(mfe_r=.1,current_r=.05,giveback_r=.05)
    p=dict(e.p)
    monkeypatch.setattr(trader.reduce_v2_state,'get',lambda *_:state(p))
    monkeypatch.setattr(trader.trade_log,'load_reduces',lambda *_args,**_k:[evidence()])
    with caplog.at_level(logging.INFO):
        assert apply(e) is True
    assert e.c.stop==pytest.approx(100.1)
    assert len(e.c.amends)==1
    assert any('verified_profitable_partial_close' in x.message for x in caplog.records)

def test_insufficient_or_unproven_partial_never_changes_stop(env,monkeypatch):
    e=env;e.obs.update(mfe_r=.1,current_r=.05)
    monkeypatch.setattr(trader.reduce_v2_state,'get',lambda *_:state(e.p))
    for case in ('no_evidence','estimated','negative_net','wrong_id',
                 'wrong_lifecycle','wrong_price','pending','stale_execution'):
        st=state(e.p)
        row=evidence()
        if case=='no_evidence': rows=[]
        else:rows=[row]
        if case=='estimated':row['pnl_source']='estimated'
        if case=='negative_net':row['okx_net_pnl']=-1.
        if case=='wrong_id':row['close_execution_evidence']['trade_ids']=[]
        if case=='wrong_lifecycle':st['lifecycle_id']='other'
        if case=='wrong_price':row['close_execution_evidence']['vwap']=106.
        if case=='pending':st['pending_order']={'id':'pending'}
        if case=='stale_execution':st['executed_ids']=['different']
        monkeypatch.setattr(trader.reduce_v2_state,'get',lambda *_,s=st:s)
        monkeypatch.setattr(trader.trade_log,'load_reduces',lambda *_a,r=rows,**_k:r)
        e.c.amends.clear();e.c.stop=90.
        assert apply(e) is False,case
        assert not e.c.amends,case

def test_verified_partial_cannot_weaken_better_sl_or_touch_wrong_qty(env,monkeypatch):
    e=env;e.obs.update(mfe_r=.1,current_r=.05)
    monkeypatch.setattr(trader.reduce_v2_state,'get',lambda *_:state(e.p))
    monkeypatch.setattr(trader.trade_log,'load_reduces',lambda *_a,**_k:[evidence()])
    e.c.stop=101.
    assert apply(e) is False
    assert not e.c.amends
    e.c.stop=90.;e.c.size=7
    assert apply(e) is False
    assert not e.c.amends

def test_helper_rejects_older_duplicate_unsettled_exec(env):
    e=env;st=state(e.p)
    row=evidence()
    assert guard.confirmed_profitable_partial(S,e.p,st,[row])
    assert not guard.confirmed_profitable_partial(S,e.p,st,[row,row])
    st['executed_ids']=['red1','red2']
    assert not guard.confirmed_profitable_partial(S,e.p,st,[row])
    st=state(e.p);st['baseline_known']=False
    assert not guard.confirmed_profitable_partial(S,e.p,st,[row])

def test_mfe_existing_threshold_is_preserved(env,monkeypatch):
    e=env
    e.obs.update(mfe_r=.6,current_r=.3,giveback_r=.3)
    monkeypatch.setattr(trader.reduce_v2_state,'get',lambda *_:None)
    assert apply(e)
    assert e.c.stop==pytest.approx(100.1)
