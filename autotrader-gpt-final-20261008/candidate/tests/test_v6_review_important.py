"""Independent review incidents exercised through real execution and cycle helpers."""
from dataclasses import replace
from types import SimpleNamespace
import json
import logging
import time
import pytest
import trader
import core_entry_events as events
from test_v6_entry_cost_notifications import pi_boundary,SYMBOL
from test_core_entry_pipeline import setup,run,SYMBOL as CORE_SYMBOL

@pytest.mark.parametrize('side,quote',[('long',99.9),('short',100.1),('long',99.99),('short',100.01)])
def test_verified_ai_final_r_contract_never_permits_drifted_out_of_range_targets(tmp_path,monkeypatch,side,quote):
    cfg,c,d,v,_,_=pi_boundary(tmp_path,monkeypatch,side,quote=quote,tick='.01')
    sign=1 if side=='long' else -1
    stop=100-sign*1.5;tp1=100+sign*3.75;tp2=100+sign*7.5
    v.update(context=replace(v['context'],entry_price=100,atr=.5,current_stop=stop),
        plan=replace(v['plan'],stop_price=stop,tp1=replace(v['plan'].tp1,price=tp1),
        tp2=replace(v['plan'].tp2,price=tp2),effective_notional=100),verified_ai_price_contract=True)
    market=c.exchange.market(SYMBOL);market['precision']['amount']=.01;market['limits']['amount']['min']=.01
    c.exchange.market=lambda _:market
    out=trader._core_final_entry_validation(cfg,c,SYMBOL,side,1,100,stop,tp2,d,v)
    if abs(quote-100)>.05:
        assert not out['allowed'],out
    else:
        assert out['allowed'],out
        risk=abs(out['entry_price']-out['stop'])
        assert .75<=abs(out['plan'].tp1.price-quote)/risk<=2.5+1e-12
        assert 1.5<=abs(out['plan'].tp2.price-quote)/risk<=5+1e-12

@pytest.mark.parametrize('side,quote',[('long',99.9),('short',100.1)])
def test_verified_ai_final_minimum_atr_cannot_widen_stop(tmp_path,monkeypatch,side,quote):
    cfg,c,d,v,_,_=pi_boundary(tmp_path,monkeypatch,side,quote=quote,tick='.01')
    sign=1 if side=='long' else -1;stop=100-sign*1.5;target=100+sign*6
    v.update(context=replace(v['context'],entry_price=100,atr=1,current_stop=stop),
        plan=replace(v['plan'],stop_price=stop,tp1=replace(v['plan'].tp1,price=100+sign*3),tp2=replace(v['plan'].tp2,price=target),effective_notional=100),verified_ai_price_contract=True)
    market=c.exchange.market(SYMBOL);market['precision']['amount']=.01;market['limits']['amount']['min']=.01
    c.exchange.market=lambda _:market
    out=trader._core_final_entry_validation(cfg,c,SYMBOL,side,1,100,stop,target,d,v)
    assert not out['allowed'] and out['reason']=='final_ai_stop_below_atr_min',out


def test_real_handle_normalized_receipt_plan_outbox_and_audit_match(tmp_path,monkeypatch):
    import ai_exit_plan_audit
    cfg,state,c,p,d,_=setup(tmp_path,monkeypatch,'long')
    c.price=99.9
    run(cfg,state,c,p,d)
    receipt=events.order_receipt(str(tmp_path),'decision-123')['payload']
    audit=json.loads((tmp_path/ai_exit_plan_audit.FILENAME).read_text().splitlines()[-1])
    assert receipt['tp_price']==pytest.approx(111.88)
    assert audit['final_tp']==receipt['tp_price']
    final=receipt['decision']['_entry_plan_context']['final_plan']
    assert final['tp2']['price']==audit['final_tp'] and final['stop_price']==audit['final_sl']
    assert final['plan_hash']==audit['final_plan']['plan_hash']
    submitted=[r for r in events.recent(str(tmp_path)) if r['status']=='ORDER_SUBMITTED'][0]
    assert submitted['final_plan']['plan_hash']==final['plan_hash']
    assert submitted['tp_price']==receipt['tp_price']

@pytest.mark.parametrize('fault',['stale','direction'])
def test_real_doge_blocked_setup_cycle_reaches_durable_outbox(tmp_path,monkeypatch,fault):
    from test_candidate_c_doge_live_entry import intent,inputs
    import candidate_c_hybrid_cycle as cycle
    import candidate_c_decision_engine as dec
    from test_candidate_c_live_backtest_per_bar_recheck import _patch_shared_inputs,_state,_prepare_live
    _patch_shared_inputs(monkeypatch)
    actual=intent(9,30)
    rejected=replace(actual,kind=dec.INTENT_NO_ACTION,entry_attempt=False,
        reason_code='setup_stale_after_30m' if fault=='stale' else 'setup_direction_mismatch_or_no_atr')
    # Drive the real production cycle with its genuine setup-bearing decision.
    monkeypatch.setattr(cycle.dec,'decide',lambda *a,**k:rejected)
    cfg=SimpleNamespace(user_dir=str(tmp_path),logger=logging.getLogger('review'),CANDIDATE_C_LIVE_EXECUTE=True)
    state=_state(tmp_path)
    prepared=_prepare_live(monkeypatch,tmp_path,state,300000)
    assert prepared['result']['setup_id']==actual.setup_id
    result=prepared['result']
    from entry_attempt_notifications import record_candidate_block
    record_candidate_block(cfg,dict(result,symbol=actual.symbol),result)
    assert len(events.recent(str(tmp_path)))==1


def test_adaptive_block_evidence_uses_adaptive_geometry_and_economics(tmp_path,monkeypatch):
    cfg,c,d,v,sl,tp=pi_boundary(tmp_path,monkeypatch)
    plan=replace(v['plan'],entry_allowed=False,reason_code='post_cost_rr_below_minimum',
                 tp2=replace(v['plan'].tp2,price=.088))
    evidence=trader._core_pre_gpt_block_evidence(cfg,d,dict(last_price=.08268,amount=100,
        sl_price=.08,tp_price=.09,adaptive_live=dict(plan=plan,context=v['context'],reason=plan.reason_code)))
    assert evidence['sl_price']==plan.stop_price and evidence['tp_price']==plan.tp2.price
    values=evidence['validation_values']
    expected=(abs(plan.tp2.price-.08268)-.08268*.001)/(abs(plan.stop_price-.08268)+.08268*.001)
    assert values['post_cost_rr']==pytest.approx(expected)
    assert values['min_post_cost_rr']==1.1 and values['risk_budget']==2
    import telegram_notify
    text=telegram_notify.format_core_entry_event(dict(evidence,decision_id='d',status='LOCAL_BLOCKED',reason=plan.reason_code))
    assert '순RR=' in text and '최소RR=1.1' in text and '위험예산=2' in text

@pytest.mark.parametrize('fault',['stale','direction','strong'])
def test_native_doge_whole_cycle_guard_event_dedup(tmp_path,monkeypatch,fault):
    from test_candidate_c_doge_live_entry import inputs,SYMBOL as DOGE
    from test_candidate_c_live_backtest_per_bar_recheck import _cfg,_state
    import candidate_c_hybrid_cycle as cycle
    import candidate_c_strategy_policy as policy
    at,book,bars=inputs(14,40) if fault=='strong' else inputs(9,30)
    cfg=_cfg(tmp_path);cfg.logger=logging.getLogger('native-cycle')
    state=_state(tmp_path)
    monkeypatch.setattr(cycle.cycle_recon,'reconcile_managed_position',lambda *a,**k:{'critical':False})
    if fault=='stale':
        state.setup_tracker.observe(DOGE,'short',at-3600000,True,entry_eligible=True)
    if fault=='direction':
        native=book.indicators
        def direction_mismatch(rows,**kw):
            out=native(rows,**kw)
            if rows and rows[-1]['close_time_ms']-rows[-1]['open_time_ms'] in (14400000,3600000):
                out=[dict(row,close=.10,ema_20=.10,ema_50=.10) for row in out]
            return out
        indicator=direction_mismatch
    else:indicator=book.indicators
    kwargs=dict(bars_4h=bars['4h'],bars_1h=bars['1h'],bars_5m=bars['5m'],state=state,
        account_id='test',config_version_id='v1',config_hash='fixture',risk_per_trade_pct=1,
        lot_step=1,min_size=1,strategy_policy=policy.production_strategy_policy(),indicator_fn=indicator)
    expected={'stale':'setup_stale_after_30m','direction':'setup_direction_mismatch_or_no_atr','strong':'entry_risk_strong_confirmation_required'}[fault]
    result=cycle.run_steady_state_cycle(cfg,SimpleNamespace(fetch_usdt_equity=lambda:1000),DOGE,**kwargs)
    assert result['reason_code']==expected
    cycle.run_steady_state_cycle(cfg,SimpleNamespace(fetch_usdt_equity=lambda:1000),DOGE,**kwargs)
    rows=events.recent(str(tmp_path))
    assert len(rows)==1 and rows[0]['reason']==expected and rows[0]['side']==result['side']
    cfg.CANDIDATE_C_LIVE_EXECUTE=False
    monkeypatch.setattr(cycle.live,'execute_intent',lambda *a,**k:dict(executed=False,reason_code='shadow_mode'))
    cycle.run_steady_state_cycle(cfg,SimpleNamespace(fetch_usdt_equity=lambda:1000),DOGE,**kwargs)
    assert len(events.recent(str(tmp_path)))==1
    if fault=='strong':
        cfg.CANDIDATE_C_LIVE_EXECUTE=True
        # Re-evaluate the same source setup from an independent pre-decision tracker.
        restored=tmp_path/'replayed-state';restored.mkdir()
        kwargs['state']=_state(restored)
        kwargs['state'].setup_tracker.observe(DOGE,'short',at-3600000,True,entry_eligible=True)
        changed=cycle.run_steady_state_cycle(cfg,SimpleNamespace(fetch_usdt_equity=lambda:1000),DOGE,**kwargs)
        assert changed['reason_code']=='setup_stale_after_30m'
        different=events.recent(str(tmp_path))
        assert len(different)==2 and different[0]['setup_id']==different[1]['setup_id']
        assert {r['reason'] for r in different}=={expected,'setup_stale_after_30m'}


def test_whole_core_cycle_adaptive_block_publishes_real_geometry(tmp_path,monkeypatch):
    import config
    import telegram_notify
    from test_core_ai_budget_gate_20261004 import cycle_fixture
    _,state,client=cycle_fixture(tmp_path,monkeypatch)
    cfg=config.UserConfig(str(tmp_path));cfg.logger=logging.getLogger('core-cycle')
    cfg.EXECUTION_MODE='LIVE';cfg.CORE_UNIFIED_MODE='OFF';cfg.HOLD_AUDIT_ENABLED=False
    cfg.MIN_CONFIDENCE=.6;cfg.LEVERAGE=5;cfg.POSITION_SIZE_MODE='FIXED';cfg.POSITION_FIXED_USDT=150
    _,_,decision,v,stop,target=pi_boundary(tmp_path,monkeypatch)
    ctx=replace(v['context'],entry_price=100,atr=2)
    plan=replace(v['plan'],entry_allowed=False,reason_code='post_cost_rr_below_minimum',
        stop_price=92,effective_notional=500,tp2=replace(v['plan'].tp2,price=107))
    monkeypatch.setattr(trader.gemini_analyzer,'analyze',lambda *a:{'action':'long','confidence':.8})
    monkeypatch.setattr(trader,'_reentry_blocked',lambda *a:(False,0))
    monkeypatch.setattr(trader.core_long_confirmation,'check_long_confirmation',lambda *a:(True,'ok'))
    monkeypatch.setattr(trader.risk_manager,'quantize_coin_amount_to_market',lambda *a:5)
    monkeypatch.setattr(trader,'_run_core_adaptive_entry_shadow',lambda *a,**k:None)
    monkeypatch.setattr(trader,'_core_adaptive_live_entry_decision',lambda *a,**k:dict(active=True,blocked=True,reason=plan.reason_code,plan=plan,context=ctx))
    trader.run_cycle(cfg,state,client,'BTC/USDT:USDT',SimpleNamespace(allow_new_entry=lambda _:True))
    row=events.recent(str(tmp_path))[0]
    assert row['reason']==plan.reason_code and row['sl_price']==92 and row['tp_price']==107
    assert row['validation_values']['post_cost_rr']==pytest.approx((7-.1)/(8+.1))
    text=telegram_notify.format_core_entry_event(row)
    assert 'SL=92' in text and 'TP=107' in text and '순RR=' in text and '최소RR=1.1' in text


def test_second_quote_cannot_replace_original_approval_anchor(tmp_path,monkeypatch):
    cfg,c,d,v,sl,tp=pi_boundary(tmp_path,monkeypatch,quote=.08259)
    c.contract_size=lambda:1
    first=trader._core_final_entry_validation(cfg,c,SYMBOL,'long',100,.08268,sl,tp,d,v)
    assert first['allowed']
    trader._commit_core_final_entry_plan(cfg,c,SYMBOL,d,v,first,'pre_close')
    c.fetch_last_price=lambda:.08243
    second=trader._core_final_entry_validation(cfg,c,SYMBOL,'long',first['amount'],first['entry_price'],first['stop'],first['target'],d,d['_bounded_entry_validation'])
    assert not second['allowed'] and second['reason']=='post_gpt_price_drift'

@pytest.mark.parametrize('side,quote',[('long',100.1),('short',99.9)])
def test_verified_ai_final_stop_leverage_cap_after_quote_drift(tmp_path,monkeypatch,side,quote):
    cfg,c,d,v,_,_=pi_boundary(tmp_path,monkeypatch,side,quote=quote,tick='.01')
    sign=1 if side=='long' else -1;stop=100-sign*4;target=100+sign*8
    v.update(context=replace(v['context'],entry_price=100,atr=1,current_stop=stop),
        plan=replace(v['plan'],stop_price=stop,tp1=replace(v['plan'].tp1,price=100+sign*4),tp2=replace(v['plan'].tp2,price=target),effective_notional=100),verified_ai_price_contract=True)
    market=c.exchange.market(SYMBOL);market['precision']['amount']=.01;market['limits']['amount']['min']=.01
    c.exchange.market=lambda _:market
    out=trader._core_final_entry_validation(cfg,c,SYMBOL,side,1,100,stop,target,d,v)
    assert out['allowed'],out
    assert abs(out['stop']-quote)/quote*5*100<=20+1e-12
    assert out['stop']>=stop if side=='long' else out['stop']<=stop


def test_reversal_carries_normalized_plan_across_close_and_submit(tmp_path,monkeypatch):
    cfg,state,c,p,d,_=setup(tmp_path,monkeypatch,'long')
    old=dict(side='short',contracts=3,entry_price=100,position_id='old-short',entry_timestamp_ms=500000)
    c.position=old;c.price=99.91
    seen=[]
    def close(*a,**kw):
        seen.append(d['_entry_plan_context']['final_plan'])
        c.position=None;c.price=99.90
        return True
    monkeypatch.setattr(trader,'_execute_close',close)
    _,quantity,stop,target=p['order_args']
    trader._handle_new_entry(cfg,state,c,CORE_SYMBOL,'long',d,'decision-123','entry',[],'',old,100,quantity,stop,target,
        reversal_position=old,closed_dfs={},adaptive_plan=p['plan'],adaptive_context=p['context'])
    assert len(c.orders)==1 and seen
    final=events.order_receipt(str(tmp_path),'decision-123')['payload']['decision']['_entry_plan_context']
    assert final['approval_anchor']['entry_price']==100
    assert final['quantity_coin']<=seen[0]['effective_notional']/99.91+1e-12
    assert final['tp_price']==pytest.approx(111.88) and seen[0]['tp2']['price']==pytest.approx(111.89)
    assert final['final_plan']['tp2']['price']==final['tp_price']


@pytest.mark.parametrize('hour,minute',[(9,30),(14,40),(11,0)])
def test_native_doge_live_and_replay_adapter_emit_identical_intent_contract(tmp_path,monkeypatch,hour,minute):
    from test_candidate_c_doge_live_entry import inputs,SYMBOL as DOGE
    from test_candidate_c_live_backtest_per_bar_recheck import _cfg,_state,_Window
    import candidate_c_hybrid_cycle as cycle
    import candidate_c_backtest_signal_adapter as ba
    import candidate_c_setup_tracker as st
    import candidate_c_exit_management as cem
    import candidate_c_strategy_policy as policy
    at,book,bars=inputs(hour,minute)
    # Both paths receive the same persisted, causal HTF checkpoint and native candles.
    monkeypatch.setattr(ba,'build_causal_indicator_lookup',lambda *a,**k:book.indicators)
    monkeypatch.setattr(cycle.cycle_recon,'reconcile_managed_position',lambda *a,**k:{'critical':False})
    prepared=cycle._prepare_steady_state_decision_locked(_cfg(tmp_path),object(),DOGE,
        bars_4h=bars['4h'],bars_1h=bars['1h'],bars_5m=bars['5m'],state=_state(tmp_path),
        account_id='test',config_version_id='v1',config_hash='fixture',risk_per_trade_pct=1,
        lot_step=1,min_size=1,strategy_policy=policy.production_strategy_policy(),indicator_fn=book.indicators)
    replay=ba.CandidateCBacktestAdapterState(st.SetupTracker.in_memory(),cem.PositionEpochStore.in_memory())
    signal=ba.build_candidate_c_signal(DOGE,bars_4h=bars['4h'],bars_1h=bars['1h'],bars_1d=[],
        lot_step=1,min_size=1,account_id='test',config_version_id='v1',config_hash='fixture',
        strategy_policy=policy.production_strategy_policy(),adapter_state=replay)
    signal(DOGE,_Window(bars['5m']),None)
    actual=replay.all_intents_seen[-1]
    expected=prepared.get('intent')
    if expected is None:
        result=prepared['result']
        assert actual.kind==result['intent_kind'] and actual.reason_code==result['reason_code']
        assert actual.setup_id==result.get('setup_id')
    else:
        for key in ('kind','side','setup_id','reason_code','raw_stop_price','raw_target_price',
                    'entry_size_fraction','requested_risk_pct','decision_timestamp','source_candle_close_timestamp'):
            assert getattr(actual,key)==getattr(expected,key),key
