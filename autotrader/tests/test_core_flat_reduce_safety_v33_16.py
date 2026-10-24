"""v33-16 regression: genuine HOLD stays; expired flat-reduce errors reconcile."""
import datetime as dt
import logging
from types import SimpleNamespace

import pytest
import trader,reduce_v2_state as reduce,core_kill_switch as kill
import ops_observability as obs
import mfe_profit_shadow as mfe
from state import TraderState

S='BTC/USDT:USDT'
def position():
    return dict(position_id='p',entry_timestamp_ms=1791900000000,
                side='short',entry_price=100.,contracts=8.)
def cfg(tmp_path):
    return SimpleNamespace(user_dir=str(tmp_path),
       logger=logging.getLogger('v33-16-regression'),
       EXECUTION_MODE='LIVE',CORE_AI_STRATEGY_AUTHORITY=True,
       CORE_GEMINI_MANAGEMENT_ONLY=True,POSITION_AI_REVIEW_ENABLED=True,
       POSITION_AI_LIVE_EXECUTE=True,GEMINI_API_KEY='offline',
       POSITION_AI_REVIEW_COOLDOWN_MINUTES=15,MIN_CONFIDENCE=.6)
def setup_reducer(tmp_path,monkeypatch,*,flat_after_failure=True,finalized=True,query_raises=False):
    cfg_=cfg(tmp_path);p=position()
    state=TraderState()
    reduce.ensure_position(str(tmp_path),S,p,initial_contracts=8.)
    observation=mfe.observe(str(tmp_path),S,p,sl_price=110.,price=97.,
       favorable_price=94.,bar_time=(dt.datetime.now(dt.timezone.utc)-dt.timedelta(minutes=1)).isoformat())
    class Client:
        filled=2.
        calls=0
        reduction_submitted=False
        after_reduce_calls=0
        def fetch_position(self):
            self.calls+=1
            if not self.reduction_submitted:
                return dict(p)
            self.after_reduce_calls+=1
            if self.after_reduce_calls>=2:
                if query_raises:raise RuntimeError('exchange state unknown')
                if flat_after_failure:return None
            return dict(p,contracts=6.)
        def fetch_last_price(self):return 97.
        def contract_size(self):return 1.
        def fetch_current_protection(self,*_):
            return dict(algo_id='oco',sz=8.,sl_price=110.,tp_price=90.,
              side='buy',state='live')
        def reduce_position(self,*a,**kw):
            self.reduction_submitted=True
            return dict(id='order')
        def fetch_order_status_by_client_id(self,*a):return dict(id='order',
            status='closed',filled=2.,average=97.)
    client=Client()
    import core_reduce_fill_accounting
    monkeypatch.setattr(trader.trade_log,'last_unclosed_open',lambda *_:dict(
        type='open',symbol=S,side='short',price=100.,entry_price=100.,
        amount=8.,dry_run=False,time='2026-10-13T00:00:00',sl_price=110.))
    monkeypatch.setattr(trader.risk_manager,'quantize_coin_amount_to_market',
                        lambda _c,_s,amount:amount)
    monkeypatch.setattr(trader,'_closed_indicator_tail',lambda *a,**k:[])
    monkeypatch.setattr(trader,'_restore_core_reduce_protection',
                        lambda *a: (_ for _ in ()).throw(RuntimeError('protection_resize_unresolved')))
    monkeypatch.setattr(trader.order_safety,'notify_critical',lambda *a,**k:None)
    monkeypatch.setattr(trader,'_notify_telegram',lambda *a,**k:None)
    monkeypatch.setattr(trader,'_maybe_apply_core_profit_floor',lambda *_:False)
    monkeypatch.setattr(core_reduce_fill_accounting,'resolve_reduce_fill',
            lambda *_:dict(gross_pnl=6.,fee=.2,source='offline',net_pnl=5.8))
    finalized_calls=[]
    def finalize(*a,**kw):
        finalized_calls.append((a,kw))
        return finalized
    monkeypatch.setattr(trader,'_finalize_flat_after_core_reduce',finalize)
    return cfg_,state,client,p,observation,finalized_calls

def test_flat_race_is_resolved_once_without_global_stop(tmp_path,monkeypatch):
    c,state,client,p,obs_,calls=setup_reducer(tmp_path,monkeypatch)
    assert trader._maybe_execute_mfe_profit_live(c,state,client,S,p,{},obs_) is True
    assert len(calls)==1
    assert calls[0][0][5]==2.
    assert not kill.is_active(str(tmp_path))

@pytest.mark.parametrize('flat,finalized,unknown',[
    (False,True,False), (True,False,False), (False,True,True),
])
def test_unconfirmed_flat_or_failed_reconciliation_keeps_kill_switch(
        tmp_path,monkeypatch,flat,finalized,unknown):
    c,state,client,p,obs_,calls=setup_reducer(
        tmp_path,monkeypatch,flat_after_failure=flat,
        finalized=finalized,query_raises=unknown)
    assert trader._maybe_execute_mfe_profit_live(c,state,client,S,p,{},obs_) is False
    assert kill.is_active(str(tmp_path))
    assert '보호주문 재설정 실패' in kill.get_reason(str(tmp_path))

def test_operator_cannot_release_unrelated_safety_reason(tmp_path):
    args=dict(verified_exchange_flat=True,no_open_orders=True,
              no_live_algos=True,no_pending_reduces=True)
    kill.activate(str(tmp_path),'BTC/USDT:USDT UNKNOWN_ORDER_STATE')
    assert not kill.clear_reconciled_flat_reduce(str(tmp_path),S,**args)
    assert kill.is_active(str(tmp_path))
    kill.activate(str(tmp_path),f'{S} POSITION_AI_REDUCE_50 보호주문 재설정 실패')
    assert not kill.clear_reconciled_flat_reduce(str(tmp_path),S,
        **dict(args,no_live_algos=False))
    assert kill.is_active(str(tmp_path))
    assert kill.clear_reconciled_flat_reduce(str(tmp_path),S,**args)
    assert not kill.is_active(str(tmp_path))
    assert not kill.clear_reconciled_flat_reduce(str(tmp_path),S,**args)

def test_watchdog_surfaces_core_kill_switch_even_while_flat(tmp_path):
    kill.activate(str(tmp_path),f'{S} POSITION_AI_REDUCE_50 보호주문 재설정 실패')
    state=dict(running=True,settings={},symbols={})
    report=obs.evaluate_health(tmp_path,live_state=state,
         recent_journal_text='[BTC/USDT:USDT] TF=1m,3m,5m,1h,4h,1d')
    issue=[i for i in report['issues'] if i['code']=='core_kill_switch_active']
    assert len(issue)==1
    assert issue[0]['severity']=='critical'
    assert report['critical_count']>=1
    kill.reset(str(tmp_path))
    second=obs.evaluate_health(tmp_path,live_state=state,
         recent_journal_text='[BTC/USDT:USDT] TF=1m,3m,5m,1h,4h,1d')
    assert not any(i['code']=='core_kill_switch_active' for i in second['issues'])
