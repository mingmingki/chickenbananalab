import datetime as dt
import pandas as pd
import trader
import pytest
from types import SimpleNamespace
from unittest.mock import Mock
from state import TraderState

class FakeState:
    def __init__(self, memory=None):
        self.memory = memory
    def snapshot(self):
        return {"symbols":{"BTC/USDT:USDT":{"core_ai_budget":self.memory} if self.memory else {}}}

def frames(close=100.0, atr=2.0):
    idx=[dt.datetime(2026,10,4,6,0,tzinfo=dt.timezone.utc)+dt.timedelta(minutes=5*i) for i in range(7)]
    df=pd.DataFrame({"timestamp":idx,"close":[close]*7,"ema_20":[99.0]*7,"ema_50":[98.0]*7,"rsi_14":[55.0]*7,"macd":[1.0]*7,"atr_14":[atr]*7})
    return {"5m":df}

def test_gate_exists():
    assert hasattr(trader, "_core_ai_call_gate")

def test_initial_call_then_unchanged_cycle_skips():
    gate=trader._core_ai_call_gate
    now=dt.datetime(2026,10,4,6,30,tzinfo=dt.timezone.utc)
    first=gate(FakeState(),"BTC/USDT:USDT",frames(),{}, {"active":False}, None, now=now)
    assert first["call_ai"] is True and first["reason"] == "initial"
    mem=first["next_memory"]
    second=gate(FakeState(mem),"BTC/USDT:USDT",frames(),{}, {"active":False}, None, now=now+dt.timedelta(minutes=5))
    assert second["call_ai"] is False and second["reason"] == "stable_within_budget"

def test_half_atr_move_triggers_before_flat_fallback():
    gate=trader._core_ai_call_gate
    now=dt.datetime(2026,10,4,6,30,tzinfo=dt.timezone.utc)
    first=gate(FakeState(),"BTC/USDT:USDT",frames(100,2),{}, {"active":False}, None, now=now)
    moved=gate(FakeState(first["next_memory"]),"BTC/USDT:USDT",frames(101.1,2),{}, {"active":False}, None, now=now+dt.timedelta(minutes=5))
    assert moved["call_ai"] is True and moved["reason"] == "price_move_atr"

def test_flat_and_held_fallback_are_30m():
    gate=trader._core_ai_call_gate
    now=dt.datetime(2026,10,4,6,30,tzinfo=dt.timezone.utc)
    flat=gate(FakeState(),"BTC/USDT:USDT",frames(),{}, {"active":False}, None, now=now)
    assert gate(FakeState(flat["next_memory"]),"BTC/USDT:USDT",frames(),{}, {"active":False}, None, now=now+dt.timedelta(minutes=29))["call_ai"] is False
    assert gate(FakeState(flat["next_memory"]),"BTC/USDT:USDT",frames(),{}, {"active":False}, None, now=now+dt.timedelta(minutes=30))["reason"] == "fallback_interval"
    position={"side":"long","contracts":1.0,"entry_price":100.0,"pnl_pct":0.1}
    held=gate(FakeState(),"BTC/USDT:USDT",frames(),{}, {"active":False}, position, now=now)
    assert gate(FakeState(held["next_memory"]),"BTC/USDT:USDT",frames(),{}, {"active":False}, position, now=now+dt.timedelta(minutes=29))["call_ai"] is False
    assert gate(FakeState(held["next_memory"]),"BTC/USDT:USDT",frames(),{}, {"active":False}, position, now=now+dt.timedelta(minutes=30))["reason"] == "fallback_interval"

def test_timestamp_and_small_numeric_drift_do_not_change_signature():
    now=dt.datetime(2026,10,4,6,30,tzinfo=dt.timezone.utc)
    first=trader._core_ai_call_gate(FakeState(),"BTC/USDT:USDT",frames(),{}, {"active":False},None,now=now)
    changed=frames(100.1)
    changed['5m']['timestamp'] += dt.timedelta(minutes=5)
    changed['5m']['rsi_14'] = 55.2
    second=trader._core_ai_call_gate(FakeState(first['next_memory']),"BTC/USDT:USDT",changed,{}, {"active":False},None,now=now+dt.timedelta(minutes=5))
    assert second['call_ai'] is False

@pytest.mark.parametrize('change', ['trend','rsi','structure','correction'])
def test_meaningful_signature_changes_call(change):
    now=dt.datetime(2026,10,4,6,30,tzinfo=dt.timezone.utc)
    first=trader._core_ai_call_gate(FakeState(),"BTC/USDT:USDT",frames(),{}, {"active":False},None,now=now)
    data=frames(); structure={}; correction={'active':False}
    if change=='trend': data['5m']['ema_20']=97.0
    if change=='rsi': data['5m']['rsi_14']=75.0
    if change=='structure': structure={'1h':{'high_structure':'LH','low_structure':'LL','swing_low_broken':True}}
    if change=='correction': correction={'active':True}
    second=trader._core_ai_call_gate(FakeState(first['next_memory']),"BTC/USDT:USDT",data,structure,correction,None,now=now+dt.timedelta(minutes=5))
    assert second['call_ai'] is True and second['reason']=='signature_changed'

@pytest.mark.parametrize('change', [None, {'side':'short'}, {'contracts':0.5}, {'position_id':'new'}, {'entry_timestamp_ms':2}, {'entry_price':102.0}])
def test_position_lifecycle_changes_call(change):
    now=dt.datetime(2026,10,4,6,30,tzinfo=dt.timezone.utc)
    position={'side':'long','contracts':1.0,'entry_price':100.0,'position_id':'old','entry_timestamp_ms':1}
    first=trader._core_ai_call_gate(FakeState(),"BTC/USDT:USDT",frames(),{}, {'active':False},position,now=now)
    current=dict(position,**change) if change is not None else None
    second=trader._core_ai_call_gate(FakeState(first['next_memory']),"BTC/USDT:USDT",frames(),{}, {'active':False},current,now=now+dt.timedelta(minutes=5))
    assert second['call_ai'] is True and second['reason']=='position_changed'

def test_price_threshold_uses_last_success_not_previous_skipped_cycle():
    now=dt.datetime(2026,10,4,6,30,tzinfo=dt.timezone.utc)
    state=FakeState(trader._core_ai_call_gate(FakeState(),"BTC/USDT:USDT",frames(),{}, {},None,now=now)['next_memory'])
    assert not trader._core_ai_call_gate(state,"BTC/USDT:USDT",frames(100.6),{}, {},None,now=now+dt.timedelta(minutes=5))['call_ai']
    assert trader._core_ai_call_gate(state,"BTC/USDT:USDT",frames(101.0),{}, {},None,now=now+dt.timedelta(minutes=10))['reason']=='price_move_atr'

def cycle_fixture(tmp_path, monkeypatch, position=None):
    cfg=SimpleNamespace(user_dir=str(tmp_path),logger=Mock(),EXECUTION_MODE='LIVE',POLL_INTERVAL_SECONDS=300,HOLD_AUDIT_ENABLED=False)
    state=TraderState()
    if position: state.update_symbol('BTC/USDT:USDT',entry_time=dt.datetime.now(dt.timezone.utc))
    cycle_data=frames(); cycle_data['5m']=cycle_data['5m'].copy(); cycle_data['5m']['timestamp']=cycle_data['5m']['timestamp'].dt.tz_localize(None)
    client=SimpleNamespace(fetch_multi_ohlcv=lambda _:cycle_data,fetch_position=lambda:position,fetch_usdt_equity=lambda:1000)
    for name in ('_reconcile_pending_core_reduce','_reconcile_pending_core_add','_observe_exit_shadow_from_closed_dfs','_run_core_adaptive_live_held_from_decision'):
        monkeypatch.setattr(trader,name,lambda *a,**k:None)
    for name in ('_maybe_hard_loss_close','_maybe_emergency_close_near_stop','_maybe_negative_guard_review','_maybe_fast_reduce_review','_position_ai_review_candidate'):
        monkeypatch.setattr(trader,name,lambda *a,**k:False)
    monkeypatch.setattr(trader.timeframes,'for_interval',lambda _:['5m'])
    monkeypatch.setattr(trader.indicators,'add_indicators',lambda df:df)
    monkeypatch.setattr(trader.indicators,'summarize_multi_timeframe_compact',lambda _: 'summary')
    monkeypatch.setattr(trader.market_structure,'compute_multi_timeframe',lambda *a:{})
    monkeypatch.setattr(trader.core_post_runup_correction,'evaluate',lambda *a:{'active':False})
    monkeypatch.setattr(trader,'_cashflow_profit_snapshot',lambda *a,**k:dict(adjusted_profit=0,adjusted_pct=0,raw_profit=0,raw_pct=0,summary={}))
    return cfg,state,client

@pytest.mark.parametrize('failure', ['exception','parse_failure'])
def test_cycle_retries_failure_and_only_success_commits_budget(tmp_path,monkeypatch,failure):
    cfg,state,client=cycle_fixture(tmp_path,monkeypatch)
    failed=RuntimeError('AI failed') if failure=='exception' else {'action':'hold','confidence':0.0,'reasoning':'파싱 실패로 대기'}
    ai=Mock(side_effect=[failed,{'action':'hold','confidence':0.8}])
    monkeypatch.setattr(trader.gemini_analyzer,'analyze',ai)
    if failure=='exception':
        with pytest.raises(RuntimeError): trader.run_cycle(cfg,state,client,'BTC/USDT:USDT',None)
    else: trader.run_cycle(cfg,state,client,'BTC/USDT:USDT',None)
    assert not state.snapshot()['symbols']['BTC/USDT:USDT'].get('core_ai_budget')
    trader.run_cycle(cfg,state,client,'BTC/USDT:USDT',None)
    assert state.snapshot()['symbols']['BTC/USDT:USDT'].get('core_ai_budget')
    trader.run_cycle(cfg,state,client,'BTC/USDT:USDT',None)
    assert ai.call_count==2
    assert any('CORE_AI_BUDGET_SKIP' in str(c) for c in cfg.logger.info.call_args_list)

@pytest.mark.parametrize('trigger', ['_maybe_hard_loss_close','_maybe_emergency_close_near_stop','_maybe_negative_guard_review','_maybe_fast_reduce_review'])
def test_risk_paths_execute_before_budget_gate(tmp_path,monkeypatch,trigger):
    cfg,state,client=cycle_fixture(tmp_path,monkeypatch,{'side':'long','contracts':1,'entry_price':100})
    risk=Mock(return_value=True); gate=Mock(side_effect=AssertionError('gate reached')); ai=Mock()
    monkeypatch.setattr(trader,trigger,risk)
    monkeypatch.setattr(trader,'_core_ai_call_gate',gate)
    monkeypatch.setattr(trader.gemini_analyzer,'analyze',ai)
    trader.run_cycle(cfg,state,client,'BTC/USDT:USDT',None)
    risk.assert_called_once(); gate.assert_not_called(); ai.assert_not_called()

def _multi_frames(close=100.0, atr=2.0):
    base=frames(close,atr)['5m']
    return {tf:base.copy() for tf in ('3m','5m','1h','4h')}

def test_lower_tf_noise_and_swing_relabels_do_not_force_ai():
    gate=trader._core_ai_call_gate
    now=dt.datetime(2026,10,4,6,30,tzinfo=dt.timezone.utc)
    first=gate(FakeState(),"BTC/USDT:USDT",_multi_frames(),{'3m':{'high_structure':'HH','low_structure':'HL'},'5m':{'high_structure':'HH','low_structure':'HL'}},{'active':False},None,now=now)
    changed=_multi_frames()
    changed['3m']['ema_20']=97.0
    changed['3m']['rsi_14']=25.0
    noisy={'3m':{'high_structure':'LH','low_structure':'LL','swing_low_broken':True},'5m':{'high_structure':'LH','low_structure':'HL','swing_high_broken':True}}
    second=gate(FakeState(first['next_memory']),"BTC/USDT:USDT",changed,noisy,{'active':False},None,now=now+dt.timedelta(minutes=5))
    assert second['call_ai'] is False
    assert second['reason']=='stable_within_budget'

def test_five_minute_direction_change_still_triggers_immediately():
    gate=trader._core_ai_call_gate
    now=dt.datetime(2026,10,4,6,30,tzinfo=dt.timezone.utc)
    first=gate(FakeState(),"BTC/USDT:USDT",_multi_frames(),{}, {'active':False},None,now=now)
    changed=_multi_frames()
    changed['5m']['close']=96.0
    changed['5m']['ema_20']=97.0
    changed['5m']['ema_50']=98.0
    changed['5m']['macd']=-1.0
    second=gate(FakeState(first['next_memory']),"BTC/USDT:USDT",changed,{}, {'active':False},None,now=now+dt.timedelta(minutes=5))
    assert second['call_ai'] is True
    assert second['reason']=='signature_changed'

def test_event_only_default_skips_periodic_position_ai_review(tmp_path, monkeypatch):
    position = {'side':'long','contracts':1.0,'entry_price':100.0,'position_id':'p1','entry_timestamp_ms':1}
    cfg, state, client = cycle_fixture(tmp_path, monkeypatch, position)
    cfg.POSITION_AI_REVIEW_ENABLED = True
    cfg.OPENAI_API_KEY = 'test'
    cfg.POSITION_AI_REVIEW_COOLDOWN_MINUTES = 15
    monkeypatch.setattr(trader, '_position_ai_review_candidate', lambda *a, **k: True)
    monkeypatch.setattr(trader, '_min_hold_elapsed', lambda *a, **k: True)
    review = Mock()
    monkeypatch.setattr(trader, '_handle_position_ai_review', review)
    monkeypatch.setattr(trader.gemini_analyzer, 'analyze', lambda *a, **k: {'action':'hold','confidence':0.8,'reasoning':'stable'})
    trader.run_cycle(cfg, state, client, 'BTC/USDT:USDT', None)
    review.assert_not_called()


def test_periodic_position_ai_review_can_be_explicitly_opted_in(tmp_path, monkeypatch):
    position = {'side':'long','contracts':1.0,'entry_price':100.0,'position_id':'p1','entry_timestamp_ms':1}
    cfg, state, client = cycle_fixture(tmp_path, monkeypatch, position)
    cfg.POSITION_AI_REVIEW_ENABLED = True
    cfg.POSITION_AI_PERIODIC_HOLD_REVIEW_ENABLED = True
    cfg.OPENAI_API_KEY = 'test'
    cfg.POSITION_AI_REVIEW_COOLDOWN_MINUTES = 15
    monkeypatch.setattr(trader, '_position_ai_review_candidate', lambda *a, **k: True)
    monkeypatch.setattr(trader, '_min_hold_elapsed', lambda *a, **k: True)
    review = Mock()
    monkeypatch.setattr(trader, '_handle_position_ai_review', review)
    monkeypatch.setattr(trader.gemini_analyzer, 'analyze', lambda *a, **k: {'action':'hold','confidence':0.8,'reasoning':'stable'})
    trader.run_cycle(cfg, state, client, 'BTC/USDT:USDT', None)
    review.assert_called_once()
