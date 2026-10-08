"""Production-shaped v6 incidents. All exchange and Telegram boundaries are fake."""
import datetime as dt
import logging
import time
from dataclasses import replace
from decimal import Decimal, ROUND_HALF_UP
from types import SimpleNamespace
import pytest
import trader
import core_entry_events as events
from adaptive_exit_engine import AdaptiveExitContext, AdaptiveExitPlan, TargetLeg
from test_core_ai_budget_gate_20261004 import FakeState, frames

SYMBOL='PI/USDT:USDT'

def pi_boundary(tmp_path,monkeypatch,side='long',quote=None,tick='.00001'):
    sign=1 if side=='long' else -1
    reviewed=.08268
    entry=quote if quote is not None else reviewed-sign*.00009
    stop=.07661569896301881 if side=='long' else 2*reviewed-.07661569896301881
    tp2=reviewed*(1+sign*.12); tp1=reviewed*(1+sign*.05)
    ctx=AdaptiveExitContext(SYMBOL,side,reviewed,100,stop,1000,2,.002,leverage=5,
                           estimated_roundtrip_cost_rate=.001,execution_target='tp2',mode='LIVE_BOUNDED')
    plan=AdaptiveExitPlan(SYMBOL,side,True,'ok',stop,reviewed*100,reviewed*100,.62,2,
        TargetLeg(tp1,.5,'ai'),TargetLeg(tp2,.5,'ai'),0,1,'LIVE_BOUNDED','policy','input','plan')
    cfg=SimpleNamespace(user_dir=str(tmp_path),logger=logging.getLogger('v6'),LEVERAGE=5,
        _core_loss_guards={SYMBOL:SimpleNamespace(allow_new_entry=lambda _:True)})
    def precision(symbol,p):
        return str((Decimal(str(p))/Decimal(tick)).to_integral_value(rounding=ROUND_HALF_UP)*Decimal(tick))
    market=dict(contractSize=1,precision={'amount':1,'price':float(tick)},limits={'amount':{'min':1}})
    client=SimpleNamespace(fetch_last_price=lambda:entry,fetch_usdt_equity=lambda:1000,
        ensure_markets_loaded=lambda:None,exchange=SimpleNamespace(market=lambda _:market,price_to_precision=precision))
    decision=dict(action=side,confidence=.72,_approval_started_at=time.time(),_bar_closed_at=time.time(),
                  _entry_plan_context=dict(quantity_coin=100,sl_price=stop,tp_price=tp2))
    validation=dict(context=ctx,plan=plan,closed_dfs={})
    monkeypatch.setattr(trader,'_core_entry_overextension_gate',lambda *a:dict(allowed=True,reason='ok'))
    return cfg,client,decision,validation,stop,tp2

@pytest.mark.parametrize('side',['long','short'])
def test_pi_small_contract_drift_normalizes_target_and_complete_plan(tmp_path,monkeypatch,side):
    cfg,c,d,v,sl,tp=pi_boundary(tmp_path,monkeypatch,side)
    out=trader._core_final_entry_validation(cfg,c,SYMBOL,side,100,.08268,sl,tp,d,v)
    assert out['allowed'], out
    assert abs(out['target']-out['entry_price'])/out['entry_price']*5 <= .60+1e-12
    assert out['stop']>=sl if side=='long' else out['stop']<=sl
    p=out['plan']
    assert p.entry_allowed and p.reason_code=='ok'
    assert p.stop_price==out['stop'] and p.tp2.price==out['target']
    assert p.effective_notional==pytest.approx(out['amount']*out['entry_price'])
    assert p.planned_loss_usdt==pytest.approx(out['planned_loss'])
    assert out['amount']<=100 and out['risk_budget']<=2
    assert float(c.exchange.price_to_precision(SYMBOL,out['target']))==out['target']
    assert out['post_cost_rr']>=trader.production_adaptive_exit_policy()['min_post_cost_rr']

@pytest.mark.parametrize('reason',['confidence','daily_loss','post_cost_rr_below_minimum'])
def test_pre_gpt_block_persists_durable_event(tmp_path,reason):
    cfg=SimpleNamespace(user_dir=str(tmp_path),logger=logging.getLogger('v6'))
    state=SimpleNamespace(update_symbol=lambda *a,**k:None)
    d=dict(action='long',confidence=.5)
    trader._record_core_entry_attempt(state,SYMBOL,'long','LOCAL_BLOCKED',reason=reason,
                                     cfg=cfg,decision=d,decision_id='actual-decision')
    rows=events.recent(str(tmp_path))
    assert len(rows)==1 and rows[0]['reason']==reason
    assert rows[0]['gate_processing']=='NOT_REQUESTED'

def test_different_block_reasons_same_decision_are_not_suppressed(tmp_path):
    cfg=SimpleNamespace(user_dir=str(tmp_path),logger=logging.getLogger('v6'))
    for reason in ('confidence','daily_loss','confidence'):
        events.record(cfg,dict(decision_id='d',status='LOCAL_BLOCKED',reason=reason))
    assert len(events.recent(str(tmp_path)))==2

@pytest.mark.parametrize('kind',['price','indicator'])
def test_low_importance_ai_change_coalesces_until_ten_minutes_without_committing_skip(kind):
    now=dt.datetime(2026,10,9,tzinfo=dt.timezone.utc)
    first=trader._core_ai_call_gate(FakeState(), 'BTC/USDT:USDT', frames(), {}, {}, None,now=now,entry_timing={})
    data=frames(101.1 if kind=='price' else 100)
    if kind=='indicator': data['5m']['rsi_14']=75
    state=FakeState(first['next_memory'])
    for minute in (5,9):
        out=trader._core_ai_call_gate(state,'BTC/USDT:USDT',data,{}, {},None,now=now+dt.timedelta(minutes=minute),entry_timing={})
        assert not out['call_ai'] and out['reason']=='low_importance_coalesced'
    out=trader._core_ai_call_gate(state,'BTC/USDT:USDT',data,{}, {},None,now=now+dt.timedelta(minutes=10),entry_timing={})
    assert out['call_ai']
    assert state.memory==first['next_memory']

@pytest.mark.parametrize('side,close',[('long',120),('short',80)])
def test_partial_profit_below_exchange_minimum_does_not_preempt_stop_lock(side,close):
    import candidate_c_exit_management as cem
    out=cem.evaluate_partial_take_profit(side=side,current_quantity=2,original_quantity=2,
        lot_step=.1,min_size=1,raw_entry_price=100,price_r=10,confirmed_5m_close=close,
        epoch=cem.PositionEpochState(original_contracts=2))
    assert out.action=='none' and out.reason=='partial_take_profit_below_minimum'


def test_candidate_actual_entry_block_is_durable_but_hold_is_silent(tmp_path,monkeypatch):
    import candidate_c_hybrid_live_adapter as live
    from test_entry_eligible_wide_stop_regression import geometry
    cfg=SimpleNamespace(user_dir=str(tmp_path),logger=logging.getLogger('v6'),CANDIDATE_C_LIVE_EXECUTE=True)
    intent=replace(geometry('short'),decision_timestamp=int(time.time()*1000),setup_id='fresh-setup')
    monkeypatch.setattr(live,'_candidate_c_new_entry_allowed',lambda *a:False)
    out=live.execute_intent(cfg,SimpleNamespace(),intent,snapshot={'close':100},equity=1000,is_still_valid_fn=lambda:True)
    assert not out['executed']
    rows=events.recent(str(tmp_path))
    assert len(rows)==1 and rows[0]['engine']=='Candidate C' and rows[0]['reason']=='blocked_daily_loss_guard'
    hold=replace(intent,kind=live.dec.INTENT_NO_ACTION,setup_id=None,entry_attempt=False,reason_code='hold')
    live.execute_intent(cfg,SimpleNamespace(),hold,snapshot={},equity=1000,is_still_valid_fn=lambda:True)
    assert len(events.recent(str(tmp_path)))==1


def test_core_stable_cycle_kicks_pending_outbox_without_new_entry(tmp_path,monkeypatch):
    from test_core_ai_budget_gate_20261004 import cycle_fixture
    cfg,state,client=cycle_fixture(tmp_path,monkeypatch)
    called=[]
    monkeypatch.setattr(events,'kick',lambda _:called.append(True))
    monkeypatch.setattr(trader.gemini_analyzer,'analyze',lambda *a:{'action':'hold','confidence':.8})
    trader.run_cycle(cfg,state,client,'BTC/USDT:USDT',None)
    assert called


def test_fresh_candidate_guard_block_is_reported_even_when_returned_as_noaction(tmp_path):
    from test_candidate_c_doge_live_entry import intent
    import entry_attempt_notifications as notify
    actual=intent(14,40)
    cfg=SimpleNamespace(user_dir=str(tmp_path),logger=logging.getLogger('v6'),CANDIDATE_C_LIVE_EXECUTE=True)
    notify.record_candidate_block(cfg,actual,{'executed':False,'reason_code':actual.reason_code})
    rows=events.recent(str(tmp_path))
    assert len(rows)==1 and rows[0]['reason']=='entry_risk_strong_confirmation_required'


def test_transient_coalesced_change_is_not_lost_and_failed_call_cannot_commit_success():
    now=dt.datetime(2026,10,9,tzinfo=dt.timezone.utc);symbol='BTC/USDT:USDT'
    first=trader._core_ai_call_gate(FakeState(),symbol,frames(),{}, {},None,now=now,entry_timing={})
    memory=first['next_memory']
    pending=trader._core_ai_call_gate(FakeState(memory),symbol,frames(101.1),{}, {},None,now=now+dt.timedelta(minutes=5),entry_timing={})
    state=SimpleNamespace(snapshot=lambda:{'symbols':{symbol:dict(core_ai_budget=memory,core_ai_pending=pending['pending_memory'])}})
    due=trader._core_ai_call_gate(state,symbol,frames(),{}, {},None,now=now+dt.timedelta(minutes=10),entry_timing={})
    assert due['call_ai'] and due['reason']=='coalesced_change_due'
    retry=trader._core_ai_call_gate(state,symbol,frames(),{}, {},None,now=now+dt.timedelta(minutes=15),entry_timing={})
    assert retry['call_ai'] and retry['reason']=='coalesced_change_due'
    assert state.snapshot()['symbols'][symbol]['core_ai_budget']==memory

@pytest.mark.parametrize('trigger',['one_atr','early_setup','position','structure','external'])
def test_material_ai_events_do_not_wait_for_coalescing(trigger):
    now=dt.datetime(2026,10,9,tzinfo=dt.timezone.utc);symbol='BTC/USDT:USDT'
    first=trader._core_ai_call_gate(FakeState(),symbol,frames(),{}, {},None,now=now,entry_timing={},market_event_key='stable')
    data=frames(102 if trigger=='one_atr' else 100)
    out=trader._core_ai_call_gate(FakeState(first['next_memory']),symbol,data,
        {'1h':{'swing_low_broken':True}} if trigger=='structure' else {}, {},
        {'side':'long','position_id':'new','contracts':1,'entry_price':100} if trigger=='position' else None,
        now=now+dt.timedelta(minutes=5),entry_timing={'status':'ok','phase':'early','event_key':'fresh'} if trigger=='early_setup' else {},
        market_event_key='important' if trigger=='external' else 'stable')
    assert out['call_ai'],out

@pytest.mark.parametrize('side',['long','short'])
def test_below_minimum_partial_profit_continues_to_real_decision_stop_lock(side):
    import candidate_c_decision_engine as dec
    import candidate_c_exit_management as cem
    import candidate_c_reversal_state_machine as rsm
    import candidate_c_setup_tracker as st
    import candidate_c_strategy_policy as policy
    close=120 if side=='long' else 80
    bars=[dict(inst_id='X',timeframe='5m',price_type='last',open_time_ms=i*300000,
        close_time_ms=(i+1)*300000,confirm=1,open=close,high=close+1,low=close-1,close=close,
        volume_contracts=1,volume_ccy=1,volume_ccy_quote=1,metadata_version=1,collected_at_iso='fixture') for i in range(42)]
    end=bars[-1]['close_time_ms'];higher=[dict(bars[-1],open_time_ms=0,close_time_ms=end-1)]
    def indicators(rows,**kw):
        return [dict(row,ema_20=110 if side=='long' else 90,ema_50=90 if side=='long' else 110,atr_14=5) for row in rows]
    store=cem.PositionEpochStore.in_memory()
    store.save('p',cem.PositionEpochState(original_contracts=2,remaining_contracts=2,derisk_done=True))
    machine=rsm.SymbolReversalMachine('X');machine.request_entry(side);machine.confirm_entry_filled()
    ctx=dec.DecisionContext(account_id='fake',symbol='X',strategy_id='candidate_c',config_version_id='v',
        config_hash='h',risk_per_trade_pct=1,strategy_policy=policy.production_strategy_policy())
    position=dict(side=side,position_id='p',contracts=2,raw_entry_price=100,initial_stop_price=90 if side=='long' else 110,
        high_water=close,effective_entry_price=100,entry_fee_usdt=.1,contract_size=1,fee_rate=.0005,spread_bps=3,slippage_bps=3)
    actual=dec.decide(ctx,as_of_ms=bars[-1]['open_time_ms'],bars_4h_confirmed_up_to_asof=higher,
        bars_1h_confirmed_up_to_asof=higher,bars_1d_confirmed_up_to_asof=[],bars_5m_for_10m_up_to_asof=bars,
        setup_tracker=st.SetupTracker.in_memory(),epoch_store=store,reversal_machine=machine,current_position=position,
        weakening_prev=False,lot_step=.1,min_size=1,indicator_fn=indicators)
    assert actual.kind==dec.INTENT_STOP_UPDATE and actual.reason_code=='profit_lock_activated'


def test_illegal_partial_quantity_is_stopped_before_durable_order_intent(tmp_path):
    import candidate_c_hybrid_live_adapter as live
    from unittest.mock import Mock
    from test_entry_eligible_wide_stop_regression import geometry
    intent=replace(geometry('long'),kind=live.dec.INTENT_REDUCE,reduce_quantity=.5)
    epoch=SimpleNamespace(raw_entry_price=100,min_contracts=1)
    ledger=Mock();store=SimpleNamespace(get=lambda _:epoch)
    out=live._execute_managed(SimpleNamespace(user_dir=str(tmp_path)),object(),intent,ledger,store,
        dict(entry_record=SimpleNamespace(intent_id='original'),position=dict(side='long',contracts=2)))
    assert not out['executed'] and out['reason']=='management_quantity_below_exchange_minimum'
    ledger.persist_intent.assert_not_called()
