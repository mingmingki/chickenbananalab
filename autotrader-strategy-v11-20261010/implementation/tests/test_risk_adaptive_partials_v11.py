import json
import logging
import time
from types import SimpleNamespace
import pytest
import trader
from state import TraderState
SYMBOL="ADA/USDT:USDT"

def reduce_fixture(tmp_path,monkeypatch,side):
    cfg=SimpleNamespace(user_dir=str(tmp_path),logger=logging.getLogger('reduce'),CORE_AI_STRATEGY_AUTHORITY=True,RISK_ADAPTIVE_PARTIAL_ENABLED=True,EXECUTION_MODE='LIVE',MIN_CONFIDENCE=.6)
    position=dict(side=side,contracts=10.,entry_price=100.,position_id='p1',entry_timestamp_ms=1)
    sl=98. if side=='long' else 102.;tp=104. if side=='long' else 96.
    class Client:
        symbol=SYMBOL
        def __init__(self):
            self.position=dict(position);self.protection=dict(algo_id='original-oco',side='sell' if side=='long' else 'buy',state='live',sz=10.,sl_price=sl,tp_price=tp);self.orders=[];self.amends=[];self.fill_fraction=1.;self.last_filled=0.;self.cfg=cfg;self.exchange=SimpleNamespace(market=lambda _:dict(id='ADA-USDT-SWAP',precision=dict(amount=.1,price=.1)))
        def fetch_position(self):return dict(self.position)
        def fetch_current_protection(self,*a):return dict(self.protection)
        def fetch_protection_order_by_algo_id(self,*a):return dict(self.protection)
        def contract_size(self):return 1.
        def fetch_pending_protection_orders(self):return [dict(instId='ADA-USDT-SWAP',algoId='original-oco',side=self.protection['side'],state='live',reduceOnly='true',sz=self.protection['sz'],slTriggerPx=sl,tpTriggerPx=tp)]
        def fetch_last_price(self):return 102. if side=='long' else 98.
        def fetch_multi_ohlcv(self,*a):return {'5m':object()}
        def reduce_position(self,p,contracts,client_order_id):
            self.orders.append(contracts);self.last_filled=contracts*self.fill_fraction;self.position['contracts']-=self.last_filled;return dict(id=client_order_id)
        def fetch_order_status_by_client_id(self,cid):return dict(id=cid,status='closed',filled=self.last_filled)
        def amend_protective_stop(self,aid,*,new_sl_price=None,new_sz=None):
            self.amends.append((aid,new_sl_price,new_sz));self.protection.update(sl_price=new_sl_price,sz=new_sz);return dict(ok=True,algo_id=aid)
    client=Client();state=TraderState();bar=['bar1']
    trader.reduce_v2_state.ensure_position(cfg.user_dir,SYMBOL,position,initial_contracts=10.)
    monkeypatch.setattr(trader,'_closed_indicator_tail',lambda *a,**k:[dict(ts=bar[0],macd=1.)])
    monkeypatch.setattr(trader.risk_manager,'quantize_coin_amount_to_market',lambda c,s,a:a)
    import core_reduce_fill_accounting
    monkeypatch.setattr(core_reduce_fill_accounting,'resolve_reduce_fill',lambda *a:dict(gross_pnl=5.,fee=.1,source='fixture'))
    monkeypatch.setattr(trader.trade_log,'record_reduce',lambda *a,**k:None)
    monkeypatch.setattr(trader,'_notify_telegram',lambda *a,**k:None)
    return cfg,client,state,position,bar,sl,tp

def approval(position,bar,fraction=.1,risk='medium'):
    return dict(_core_reduce_approval=dict(started_at=time.time(),path='ai_strategy',
        lifecycle_id=trader.reduce_v2_state.position_identity(position),bar_ts=bar,
        gemini_confidence=.9,gemini_risk_level=risk,gemini_reduce_fraction=fraction,
        reduce_fraction=fraction,risk_level=risk))

def execute(fixture,fraction=.1):
    cfg,c,s,p,b,sl,tp=fixture
    return trader._execute_position_ai_reduce_50_locked(cfg,s,c,SYMBOL,c.fetch_position(),
        'v11-review',.9,'thesis_intact',approval(p,b[0],fraction))

@pytest.mark.parametrize('side',['long','short'])
def test_actual_ai_variable_sizes_same_oco_and_initial_basis(tmp_path,monkeypatch,side):
    f=reduce_fixture(tmp_path,monkeypatch,side)
    cfg,c,s,p,b,sl,tp=f
    for n,fraction in enumerate([.1,.15,.35]):
        b[0]=f'bar{n}'
        assert execute(f,fraction)
        assert c.protection['algo_id']=='original-oco'
        assert c.protection['sl_price']==sl and c.protection['tp_price']==tp
        assert c.protection['sz']==pytest.approx(c.position['contracts'])
        assert trader.reduce_v2_state.get(cfg.user_dir,SYMBOL)['pending_order'] is None
    assert c.orders==pytest.approx([1.,1.5,2.5])  # cap total at original50%
    b[0]='last';assert not execute(f,.1)

@pytest.mark.parametrize('bad',[None,True,'0.25',float('nan'),float('inf'),0.,-.1,.01,.51])
def test_ai_bad_fraction_never_falls_back_to_fixed25(tmp_path,monkeypatch,bad):
    f=reduce_fixture(tmp_path,monkeypatch,'long')
    assert not execute(f,bad)
    assert f[1].orders==[]

def test_ai_bar_dedup_and_existing25pct_history_preserved(tmp_path,monkeypatch):
    f=reduce_fixture(tmp_path,monkeypatch,'long');cfg,c,s,p,b,sl,tp=f
    trader.reduce_v2_state.record_stage_executed(cfg.user_dir,SYMBOL,1,2.5,execution_id='old-v10')
    c.position['contracts']=7.5;c.protection['sz']=7.5
    assert execute(f,.1)
    assert not execute(f,.1)
    assert c.orders==[1.]
    assert trader.reduce_v2_state.get(cfg.user_dir,SYMBOL)['cumulative_reduced_ratio']==pytest.approx(.35)

def test_terminal_partial_fill_restart_pins_previous_goal(tmp_path,monkeypatch):
    f=reduce_fixture(tmp_path,monkeypatch,'short');cfg,c,s,p,b,sl,tp=f
    c.fill_fraction=.5
    assert execute(f,.3)  # fills1.5 of planned3
    saved=trader.reduce_v2_state.get(cfg.user_dir,SYMBOL)
    assert saved['adaptive_reduce_target_contracts']==3.
    assert saved['reduce_stage']==0 and saved['actual_reduced_contracts']==1.5
    b[0]='bar2';c.fill_fraction=1.
    f=(cfg,c,TraderState(),p,b,sl,tp)
    assert execute(f,.4)  # newAIapproval cannot add4 on top of unfinished3
    assert c.orders==[3.,1.5]
    saved=trader.reduce_v2_state.get(cfg.user_dir,SYMBOL)
    assert saved['cumulative_reduced_ratio']==.3 and saved['reduce_stage']==1
    assert saved.get('adaptive_reduce_target_contracts') is None

def test_new_ai_smaller_approval_limits_unfinished_target(tmp_path,monkeypatch):
    f=reduce_fixture(tmp_path,monkeypatch,'long');cfg,c,s,p,b,sl,tp=f
    c.fill_fraction=.5
    assert execute(f,.3)
    b[0]='bar2';c.fill_fraction=1.
    assert execute(f,.05)
    assert c.orders==[3.,.5]  # freshAI did not reapprove the old1.5 remainder
    assert trader.reduce_v2_state.get(cfg.user_dir,SYMBOL)['cumulative_reduced_ratio']==.2
    assert trader.reduce_v2_state.get(cfg.user_dir,SYMBOL)['adaptive_reduce_target_contracts']==3.
    assert trader.reduce_v2_state.get(cfg.user_dir,SYMBOL)['reduce_stage']==0
    b[0]='bar3'
    assert execute(f,.4)
    assert c.orders==[3.,.5,1.]
    assert trader.reduce_v2_state.get(cfg.user_dir,SYMBOL)['cumulative_reduced_ratio']==.3

@pytest.mark.parametrize('event,lo,hi',[('partial_take_profit_2r',.1,.5),
    ('unified_structure_profit_break',.1,.5),('unified_mfe_profit_giveback',.1,.5),
    ('structural_derisk',.25,.75)])
def test_chart_fraction_continuously_increases_with_risk(event,lo,hi):
    import adaptive_reduction as ar
    low=ar.chart_fraction(event,current_r=2.,mfe_r=2.,atr_r=.2,weakening_1h=False,adverse_5m=False)
    mid=ar.chart_fraction(event,current_r=1.3,mfe_r=2.,atr_r=.8,weakening_1h=False,adverse_5m=True)
    high=ar.chart_fraction(event,current_r=-.6,mfe_r=2.,atr_r=2.,weakening_1h=True,adverse_5m=True)
    assert lo<=low['fraction']<mid['fraction']<high['fraction']<=hi
    assert high['authority']=='CHART_RISK'
    assert low['basis']==('remaining' if event=='structural_derisk' else 'initial')

@pytest.mark.parametrize('side',['long','short'])
def test_chart_risk_symmetric_and_no_ai_or_confidence_input(side):
    import adaptive_reduction as ar
    out=ar.chart_fraction('structural_derisk',current_r=-.3,mfe_r=.5,atr_r=.8,
        weakening_1h=True,adverse_5m=False)
    assert .25<out['fraction']<.75
    assert out['risk_score']>0

@pytest.mark.parametrize('missing',['current_r','mfe_r','atr_r'])
def test_chart_missing_risk_evidence_does_not_invent_ratio(missing):
    import adaptive_reduction as ar
    args=dict(current_r=1.,mfe_r=2.,atr_r=.5,weakening_1h=False,adverse_5m=False)
    args[missing]=None
    assert ar.chart_fraction('partial_take_profit_2r',**args) is None

def test_config_optin_default_off_and_can_enable(tmp_path):
    from config import UserConfig
    assert UserConfig(str(tmp_path)).RISK_ADAPTIVE_PARTIAL_ENABLED is False
    (tmp_path/'.env').write_text('RISK_ADAPTIVE_PARTIAL_ENABLED=true\n')
    assert UserConfig(str(tmp_path)).RISK_ADAPTIVE_PARTIAL_ENABLED is True

def test_gemini_requests_risk_and_numeric_fraction(tmp_path,monkeypatch):
    import gemini_analyzer as g
    captured=[]
    cfg=SimpleNamespace(user_dir=str(tmp_path),logger=logging.getLogger('v11'),
        CORE_AI_STRATEGY_AUTHORITY=True,RISK_ADAPTIVE_PARTIAL_ENABLED=True,GEMINI_MODEL='fake')
    monkeypatch.setattr(g,'_get_client',lambda _:object())
    monkeypatch.setattr(g,'_generate_content_observed',lambda *a,**kw:captured.append(kw) or
        SimpleNamespace(text=json.dumps(dict(assessment='weakening',confidence=.9,risk_level='high',suggested_reduce_fraction=.35)),usage_metadata=None))
    result=g.analyze_held_position(cfg,SYMBOL,['5m'],'confirmed',
        dict(side='long',contracts=10,unrealized_pnl=1),dict(action='hold'))
    assert result['suggested_reduce_fraction']==.35
    assert 'suggested_reduce_fraction' in captured[0]['contents']
    assert 'risk_level' in captured[0]['config'].response_schema['properties']

def test_dashboard_does_not_label_adaptive_reduction_fixed25():
    from pathlib import Path
    html=(Path(__file__).resolve().parents[1]/'templates/dashboard.html').read_text()
    assert 'review.reduce_fraction' in html
    assert 'function corePositionAiActionText(action, review' in html


def chart_intent(atr=2.,enabled=True,adapter=False,monkeypatch=None,original=10.,current=5.,lot=.1,water=120.,weak=False,mfe_done=False,min_size=None,side="long"):
    import candidate_c_exit_management as cem
    import candidate_c_decision_engine as dec
    import candidate_c_setup_tracker as st
    import candidate_c_reversal_state_machine as rsm
    import candidate_c_strategy_policy as pol

    sign=1 if side=="long" else -1
    min_size=lot if min_size is None else min_size
    step=300000
    bars=[]
    for i in range(42):
        o=i*step
        close=100.+sign*(10. if i<41 else 20.)
        bars.append(dict(
            inst_id="X", timeframe="5m", price_type="last",
            open_time_ms=o, close_time_ms=o+step, confirm=1,
            open=close, high=close+1, low=close-1, close=close,
            volume_contracts=1, volume_ccy=1, volume_ccy_quote=1,
            metadata_version=1, collected_at_iso="x",
        ))
    asof=bars[-1]["open_time_ms"]
    end=bars[-1]["close_time_ms"]
    base=dict(inst_id="X",price_type="last",confirm=1,open=100.+sign*10,high=101.+sign*10,low=99.+sign*10,close=100.+sign*10)
    bars4=[dict(base,open_time_ms=-14400000,close_time_ms=0)]
    bars1=[dict(base,open_time_ms=-3600000,close_time_ms=0)]

    def indicators(rows,donchian_n=20):
        out=[]
        for bar in rows:
            row=dict(bar)
            row.update(ema_20=100.0,ema_50=100.-sign*10,atr_14=atr)
            if weak and bar['open_time_ms']==-3600000: row['ema_20']=100.+sign*15
            out.append(row)
        return out

    store=cem.PositionEpochStore.in_memory()
    store.save("p1",cem.PositionEpochState(
        original_contracts=original,remaining_contracts=current,derisk_done=True,
        profit_lock_active=True,partial_take_profit_done=False))
    store.get('p1').mfe_profit_reduce_done=mfe_done
    machine=rsm.SymbolReversalMachine("X")
    machine.request_entry(side)
    machine.confirm_entry_filled()
    ctx=dec.DecisionContext(
        account_id="a",symbol="X",strategy_id="candidate_c",
        config_version_id="v",config_hash="h",risk_per_trade_pct=1.0,
        strategy_policy=pol.production_strategy_policy())
    object.__setattr__(ctx,"risk_adaptive_partials",enabled)
    position=dict(
        side=side,position_id="p1",contracts=current,
        raw_entry_price=100.0,initial_stop_price=100.-sign*10,high_water=100.+sign*(water-100.),
        effective_entry_price=100.0,entry_fee_usdt=.1,contract_size=1.0,
        fee_rate=.0005,spread_bps=3.0,slippage_bps=3.0)
    if adapter:
        import candidate_c_backtest_signal_adapter as ba
        class Window(list):
            def latest(self): return self[-1]
        monkeypatch.setattr(ba,'build_causal_indicator_lookup',lambda *a,**k:indicators)
        state=ba.CandidateCBacktestAdapterState(setup_tracker=st.SetupTracker.in_memory(),epoch_store=store)
        signal=ba.build_candidate_c_signal('X',bars_4h=bars4,bars_1h=bars1,bars_1d=[],
            lot_step=lot,min_size=min_size,account_id='a',config_version_id='v',config_hash='h',
            strategy_policy=pol.production_strategy_policy(),risk_adaptive_partials=enabled,adapter_state=state)
        signal('X',Window(bars),dict(position,entry_price=100.))
        return state.all_intents_seen[-1]
    intent=dec.decide(
        ctx,as_of_ms=asof,bars_4h_confirmed_up_to_asof=bars4,
        bars_1h_confirmed_up_to_asof=bars1,bars_1d_confirmed_up_to_asof=[],
        bars_5m_for_10m_up_to_asof=bars,setup_tracker=st.SetupTracker.in_memory(),
        epoch_store=store,reversal_machine=machine,current_position=position,
        weakening_prev=False,lot_step=lot,min_size=min_size,indicator_fn=indicators)
    return intent


def test_candidate_actual_decide_risk_changes_partial_and_preserves_history():
    import candidate_c_decision_engine as dec
    low=chart_intent(2.)
    high=chart_intent(15.)
    assert low.kind==high.kind==dec.INTENT_REDUCE
    assert low.reduce_quantity<high.reduce_quantity
    assert low.reduction_plan["authority"]=="CHART_RISK"
    assert low.reduction_plan["basis"]=="initial"
    assert low.reduce_quantity==1. and low.target_residual==4.
    assert chart_intent(2.,False).reduce_quantity==2.5

@pytest.mark.parametrize('fraction',[.05,.13,.27,.5,None,True,'0.25',0,.01,.51,float('nan'),float('inf')])
def test_gpt_real_parser_validates_fraction_and_receives_gemini_risk(tmp_path,monkeypatch,fraction):
    import openai_analyzer as g
    captured=[]
    cfg=SimpleNamespace(user_dir=str(tmp_path),logger=logging.getLogger('v11'),
        CORE_AI_STRATEGY_AUTHORITY=True,RISK_ADAPTIVE_PARTIAL_ENABLED=True,OPENAI_MODEL='fake',OPENAI_API_KEY='fake')
    def response(**kw):
        captured.append(kw['messages'][0]['content'])
        return SimpleNamespace(usage=None,choices=[SimpleNamespace(message=SimpleNamespace(
            content=json.dumps(dict(action='REDUCE_50',confidence=.9,risk_level='high',reduce_fraction=fraction))))])
    client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=response)),max_retries=0)
    monkeypatch.setattr(g,'_get_client',lambda _:client)
    monkeypatch.setattr(g,'_ensure_response_models_warmed_up',lambda:None)
    result=g.verify_position_management(cfg,SYMBOL,['5m'],'confirmed',
        dict(side='long',contracts=10,unrealized_pnl=1),dict(action='hold'),
        dict(assessment='weakening',confidence=.9,risk_level='high',suggested_reduce_fraction=.3))
    valid=type(fraction) in (float,int) and .05<=fraction<=.5
    assert result['action']==('REDUCE_50' if valid else None)
    if valid: assert result['reduce_fraction']==fraction
    else: assert result['error_reason']=='invalid_reduce_fraction'
    assert 'suggested_reduce_fraction=0.3' in captured[0]
    assert 'equal to 25%' not in captured[0]

def test_chart_dynamic_derisk_remaining_basis_and_dust_never_full_close():
    import candidate_c_exit_management as cem
    import adaptive_reduction as ar
    risk=ar.chart_fraction('structural_derisk',current_r=-.5,mfe_r=0.,atr_r=.8,
        weakening_1h=True,adverse_5m=True)
    decision=cem.DeriskDecision(action='partial_reduce',reduce_quantity=2.5)
    out=cem.risk_adaptive_quantity(decision,plan=risk,current_quantity=5.,
        original_quantity=10.,lot_step=.1,min_size=.1)
    assert out.reduce_quantity==pytest.approx(cem.floor_to_lot_step(5.*risk['fraction'],.1))
    dust=cem.risk_adaptive_quantity(cem.DeriskDecision(action='dust_full_exit'),
        plan=risk,current_quantity=.1,original_quantity=10.,lot_step=.1,min_size=.1)
    assert dust.action=='none'

def test_candidate_adaptive_signal_not_suppressed_by_fixed25_lot_floor():
    intent=chart_intent(15.,original=3.,current=3.,lot=1.,water=200.,weak=True,mfe_done=True)
    assert intent.kind=='ReduceIntent' and intent.reduce_quantity==1.
    assert intent.target_residual==2.
    assert chart_intent(15.,enabled=False,original=3.,current=3.,lot=1.,water=200.,weak=True,mfe_done=True).kind!='ReduceIntent'

def test_candidate_adaptive_dust_reason_is_partial_not_full_exit():
    intent=chart_intent(2.,original=10.,current=2.,lot=1.)
    assert intent.reduce_quantity==1. and intent.target_residual==1.
    assert intent.reason_code=='partial_take_profit_2r'

def test_candidate_management_overlay_has_distinct_immutable_identity():
    import adaptive_reduction as ar
    adaptive=chart_intent(2.);legacy=chart_intent(2.,False)
    assert adaptive.config_hash!=legacy.config_hash
    assert adaptive.idempotency_key!=legacy.idempotency_key
    assert adaptive.reduction_policy_hash==ar.chart_policy_hash()
    assert adaptive.strategy_policy==legacy.strategy_policy

def test_actual_unknown_fill_restart_reconciles_once_with_same_goal_oco(tmp_path,monkeypatch):
    f=reduce_fixture(tmp_path,monkeypatch,'long');cfg,c,s,p,b,sl,tp=f
    c.fill_fraction=.5
    status=c.fetch_order_status_by_client_id
    c.fetch_order_status_by_client_id=lambda _:None
    assert not execute(f,.3)
    saved=trader.reduce_v2_state.get(cfg.user_dir,SYMBOL)
    assert saved['pending_order']['adaptive_goal_contracts']==3.
    assert saved['adaptive_reduce_target_contracts']==3.
    c.fetch_order_status_by_client_id=status
    assert trader._reconcile_pending_core_reduce(cfg,TraderState(),c,SYMBOL)
    saved=trader.reduce_v2_state.get(cfg.user_dir,SYMBOL)
    assert saved['pending_order'] is None
    assert saved['adaptive_reduce_target_contracts']==3.
    assert saved['actual_reduced_contracts']==1.5
    assert c.orders==[3.] and c.protection['sz']==8.5
    assert c.protection['algo_id']=='original-oco' and c.protection['sl_price']==sl and c.protection['tp_price']==tp
    assert not trader._reconcile_pending_core_reduce(cfg,TraderState(),c,SYMBOL)
    assert c.orders==[3.]

def test_held_pipeline_passes_both_ai_risk_and_fraction_to_executor(tmp_path,monkeypatch):
    from test_dual_ai_invalidated_close import Cfg,Client
    cfg=Cfg(tmp_path);cfg.CORE_AI_STRATEGY_AUTHORITY=True;cfg.RISK_ADAPTIVE_PARTIAL_ENABLED=True
    monkeypatch.setattr(trader.gemini_analyzer,'analyze_held_position',lambda *a,**k:
        dict(assessment='weakening',confidence=.9,risk_level='high',suggested_reduce_fraction=.35))
    monkeypatch.setattr(trader.openai_analyzer,'verify_position_management',lambda *a,**k:
        dict(action='REDUCE_50',confidence=.9,risk_level='medium',reduce_fraction=.17))
    captured=[]
    monkeypatch.setattr(trader,'_execute_position_ai_reduce_50',lambda *a,**k:captured.append(a[-1]['_core_reduce_approval']) or True)
    trader._handle_position_ai_review(cfg,TraderState(),Client({'sl_price':98}),SYMBOL,['5m'],'confirmed',
        dict(side='long',contracts=10,entry_price=100,position_id='p1',entry_timestamp_ms=1),dict(action='hold'),{})
    assert len(captured)==1
    assert captured[0]['reduce_fraction']==.17 and captured[0]['risk_level']=='medium'
    assert captured[0]['gemini_reduce_fraction']==.35 and captured[0]['gemini_risk_level']=='high'


@pytest.mark.parametrize('atr',[2.,8.,15.])
@pytest.mark.parametrize('side',['long','short'])
def test_candidate_backtest_equals_live_shaped_adaptive_intent(monkeypatch,atr,side):
    live=chart_intent(atr,side=side)
    backtest=chart_intent(atr,adapter=True,monkeypatch=monkeypatch,side=side)
    assert live.kind==backtest.kind
    assert live.reason_code==backtest.reason_code
    assert live.reduce_quantity==backtest.reduce_quantity
    assert live.target_residual==backtest.target_residual
    assert live.reduction_plan==backtest.reduction_plan
    assert live.idempotency_key==backtest.idempotency_key


@pytest.mark.parametrize('side',['long','short'])
def test_candidate_mfe_trigger_uses_dynamic_minimum_not_old25_step(side):
    dynamic=chart_intent(15.,original=100.,current=80.,lot=1.,min_size=10.,water=200.,side=side)
    old=chart_intent(15.,enabled=False,original=100.,current=80.,lot=1.,min_size=10.,water=200.,side=side)
    assert dynamic.kind=='ReduceIntent' and dynamic.reason_code=='unified_mfe_profit_giveback'
    assert dynamic.reduce_quantity>=10.
    assert old.reason_code!='unified_mfe_profit_giveback'
