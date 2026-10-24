from datetime import datetime,timedelta,timezone
from types import SimpleNamespace
import core_profit_stop_reentry as guard
import trader

KST=timezone(timedelta(hours=9))

def proven(now=None):
    t=now or datetime.now(KST)-timedelta(minutes=11)
    return dict(type='close',symbol='BTC/USDT:USDT',strategy_group='core',
        side='short',reason='stop_loss',time=t.isoformat(),dry_run=False,
        entry_price=81759.4,close_price=81679.4,
        close_reason_evidence=dict(source='owned_algo_order_fills',reason='stop_loss',
           lifecycle_id='id1',order_id='O1',trade_ids=['F1','F2']),
        close_execution_evidence=dict(price_basis='verified_owned_protection_fills',
           lifecycle_id='id1',order_id='O1',trade_ids=['F1','F2'],vwap=81679.4))

def test_btc_real_proof_blocks_reentry_for_30_minutes():
    at=datetime(2026,10,15,8,5,44,tzinfo=KST)
    row=proven(at)
    assert guard.verified_favorable_stop(row)
    assert guard.same_side_lock(row,'short',at+timedelta(minutes=11))[0]
    assert guard.same_side_lock(row,'short',at+timedelta(minutes=29,seconds=59))[0]
    assert guard.same_side_lock(row,'short',at+timedelta(minutes=30))==(False,0.0)
    assert not guard.same_side_lock(row,'long',at+timedelta(minutes=11))[0]

def test_regular_losing_stop_and_unproved_close_keep_existing_policy():
    r=proven()
    r['close_price']=81800; r['close_execution_evidence']['vwap']=81800
    assert not guard.verified_favorable_stop(r)
    r=proven();r['close_execution_evidence']['trade_ids']=['OTHER']
    assert not guard.verified_favorable_stop(r)
    r=proven();r['close_execution_evidence'].pop('price_basis')
    assert not guard.verified_favorable_stop(r)
    r=proven();r['reason']='take_profit'
    assert not guard.verified_favorable_stop(r)

def test_reentry_gate_persists_across_restart(monkeypatch,tmp_path):
    row=proven(datetime.now(KST)-timedelta(minutes=11))
    monkeypatch.setattr(trader.trade_log,'last_current_external_close',lambda *a:row)
    monkeypatch.setattr(trader.core_manual_close,'get',lambda *a:None)
    cfg=SimpleNamespace(user_dir=str(tmp_path),logger=SimpleNamespace(info=lambda *a:None))
    class State:
        def snapshot(self):return dict(symbols={'BTC/USDT:USDT':{}})
    # A newly constructed in-memory state still consults the persisted journal.
    blocked,remain=trader._reentry_blocked(State(),'BTC/USDT:USDT',cfg,'short')
    assert blocked and 18<remain<20
    blocked2,remain2=trader._reentry_blocked(State(),'BTC/USDT:USDT',cfg,'short')
    assert blocked2 and remain2>0
    assert not trader._reentry_blocked(State(),'BTC/USDT:USDT',cfg,'long')[0]
