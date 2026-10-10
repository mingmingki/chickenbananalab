import time
from types import SimpleNamespace
from dataclasses import replace
import pytest
from state import TraderState
import trader
import candidate_c_hybrid_live_adapter as live
import candidate_c_hybrid_cycle as cycle
from adaptive_exit_engine import apply_ai_price_plan
from adaptive_exit_policy import production_adaptive_exit_policy
from test_core_wide_stop_risk_sizing import _cfg
from test_entry_eligible_wide_stop_regression import geometry


def proposal(tmp_path, side):
    cfg = _cfg(tmp_path)
    cfg.logger = SimpleNamespace(warning=lambda *a, **k: None, info=lambda *a, **k: None)
    result = trader._core_adaptive_live_entry_decision(cfg, symbol='ADA/USDT:USDT',
        legacy_order_args=(side, 10, 96 if side=='long' else 104, 106 if side=='long' else 94),
        entry_price=100, equity=1000, market_features=dict(atr=2, structural_support=93,
        structural_resistance=107, near_resistance=105, near_support=95,
        continuation_resistance=120, continuation_support=80, source_timestamps=(1000,)))
    assert not result['blocked']
    return cfg, result


@pytest.mark.parametrize('side', ['long','short'])
def test_core_real_plan_includes_established_roundtrip_fee(tmp_path, side):
    cfg, r = proposal(tmp_path, side)
    quantity=r['order_args'][1]
    modeled=quantity*(abs(r['plan'].stop_price-100)+100*.001)
    assert modeled <= 50+1e-9
    assert r['context'].estimated_roundtrip_cost_rate == pytest.approx(.001)
    assert r['plan'].planned_loss_usdt == pytest.approx(modeled)


@pytest.mark.parametrize('side', ['long','short'])
def test_core_ai_rr_uses_actual_full_oco_tp2_and_fees(tmp_path, side):
    cfg, r=proposal(tmp_path, side)
    sign=1 if side=='long' else -1
    raw=dict(stop_loss_price=100-sign*4, take_profit_1_price=100+sign*4.4,
             take_profit_2_price=100+sign*8)
    applied, reason=apply_ai_price_plan(r['plan'],r['context'],raw,production_adaptive_exit_policy())
    assert reason=='ai_exit_plan_applied'
    assert applied.entry_allowed
    assert applied.planned_loss_usdt <= 50+1e-9


def boundary(tmp_path, monkeypatch, side, price, *, minimum=.01, age=0, no_pullback=False):
    cfg,r=proposal(tmp_path,side)
    cfg.EXECUTION_MODE='LIVE'
    cfg._core_loss_guards={'ADA/USDT:USDT':SimpleNamespace(allow_new_entry=lambda equity:True)}
    now=time.time()
    decision={'action':side,'confidence':.8,'_approval_started_at':now-age, '_bar_closed_at':now-age,
              '_bounded_entry_validation':dict(context=r['context'], plan=r['plan'], closed_dfs={})}
    monkeypatch.setattr(trader,'_core_entry_overextension_gate',lambda dfs,s,p: {'allowed':not no_pullback,'reason':'entry_late_exhaustion_no_pullback' if no_pullback else 'ok'})
    client=SimpleNamespace(fetch_last_price=lambda:price, fetch_usdt_equity=lambda:1000,
        ensure_markets_loaded=lambda:None, exchange=SimpleNamespace(price_to_precision=lambda symbol,p:f'{p:.4f}',market=lambda symbol:dict(contractSize=1,precision={'amount':.01,'price':.0001},limits={'amount':{'min':minimum}})),
        ensure_leverage=lambda:None,contract_size=lambda:1,fetch_position=lambda:None,fetch_order_status_by_client_id=lambda cid:None)
    sent=[]
    class ReachedMockOrder(Exception):pass
    def submit(*args,**kwargs):
        sent.append(args)
        raise ReachedMockOrder
    client.create_position_with_sl_tp=submit
    args=r['order_args']
    return cfg,client,decision,sent,ReachedMockOrder,args


@pytest.mark.parametrize('side,price',[('long',100.19),('short',99.81)])
def test_real_core_submit_reduces_quantity_after_adverse_quote(tmp_path,monkeypatch,side,price):
    cfg,c,d,sent,marker,args=boundary(tmp_path,monkeypatch,side,price)
    validation=d['_bounded_entry_validation']
    # Bind the approved budget to its modeled initial loss so a worse quote
    # actually exercises risk sizing, even after v7 bounded-stop reconciliation.
    budget=validation['plan'].planned_loss_usdt
    validation['plan']=replace(validation['plan'],trade_risk_budget_usdt=budget)
    validation['context']=replace(validation['context'],trade_risk_budget_usdt=budget)
    state=TraderState()
    assert trader._execute_entry(cfg,state,c,'ADA/USDT:USDT',side,args[1],100,args[2],args[3],decision_id='boundary-test',decision=d) is False
    assert state.snapshot()['symbols']['ADA/USDT:USDT']['last_entry_attempt']['status']=='ORDER_PENDING'
    _,qty,sl,tp=sent[0]
    assert qty*(abs(price-sl)+price*.001)<=50+1e-9
    assert qty < args[1]
    assert qty*(abs(price-sl)+price*.001)<=budget+1e-9
    assert abs(price-sl)/price*500<=20+1e-7
    assert abs(sl-args[2])<=100*.002


@pytest.mark.parametrize('side,price',[('long',99.81),('short',100.19)])
def test_real_core_submit_rejects_ai_target_over_cap_after_favorable_quote(tmp_path,monkeypatch,side,price):
    cfg,c,d,sent,marker,args=boundary(tmp_path,monkeypatch,side,price)
    sign=1 if side=='long' else -1
    # An AI target already invalid at approval cannot gain authority from a
    # favorable quote. Valid approved caps can normalize at execution instead.
    invalid_target=100+sign*12.01
    validation=d['_bounded_entry_validation']
    plan=validation['plan']
    validation['plan']=replace(plan,tp2=replace(plan.tp2,price=invalid_target))
    validation['verified_ai_price_contract']=True
    state=TraderState()
    assert trader._execute_entry(cfg,state,c,'ADA/USDT:USDT',side,args[1],100,args[2],invalid_target,decision_id='boundary-test',decision=d) is False
    assert not sent
    assert state.snapshot()['symbols']['ADA/USDT:USDT']['last_entry_attempt']['reason']=='approved_target_above_leverage_cap'


@pytest.mark.parametrize('side,price',[('long',99.81),('short',100.19)])
def test_valid_approved_target_is_capped_after_favorable_quote(tmp_path,monkeypatch,side,price):
    cfg,c,d,sent,marker,args=boundary(tmp_path,monkeypatch,side,price)
    state=TraderState()
    assert trader._execute_entry(cfg,state,c,'ADA/USDT:USDT',side,args[1],100,args[2],args[3],decision_id='boundary-test',decision=d) is False
    assert state.snapshot()['symbols']['ADA/USDT:USDT']['last_entry_attempt']['status']=='ORDER_PENDING'
    _,qty,sl,tp=sent[0]
    assert abs(tp-price)/price*500<=60+1e-7
    assert abs(tp-args[3])<=100*.002*1.12
    assert qty<=args[1] and qty*price<=1000+1e-9
    assert qty*(abs(price-sl)+price*.001)<=50+1e-9
    assert (abs(tp-price)-price*.001)/(abs(price-sl)+price*.001)>=1.1


@pytest.mark.parametrize('side',['long','short'])
@pytest.mark.parametrize('fault',['minimum','expired','chase'])
def test_real_core_submit_fails_closed_before_exchange(tmp_path,monkeypatch,side,fault):
    cfg,c,d,sent,marker,args=boundary(tmp_path,monkeypatch,side,100,
        minimum=100 if fault=='minimum' else .01,age=181 if fault=='expired' else 0,no_pullback=fault=='chase')
    assert trader._execute_entry(cfg,TraderState(),c,'ADA/USDT:USDT',side,args[1],100,args[2],args[3],decision_id='boundary-test',decision=d) is False
    assert not sent


@pytest.mark.parametrize('side',['long','short'])
def test_candidate_verified_ai_net_tp1_envelope(side):
    sign=1 if side=='long' else -1
    intent=geometry(side)
    raw=dict(stop_loss_price=100-sign*1.5,take_profit_1_price=100+sign*1.65,take_profit_2_price=100+sign*4.5)
    updated,reason=live._apply_verified_ai_exit_plan_to_intent(intent,{'ai_exit_plan':raw},entry_price=100,atr_4h=1,leverage=5)
    assert reason=='ai_post_cost_rr_below_minimum'
    assert updated==intent


@pytest.mark.parametrize('side,quote',[('long',98.98),('short',101.02)])
def test_candidate_final_quote_must_match_signal_validator(side,quote):
    now=int(time.time()*1000)
    intent=replace(geometry(side),decision_timestamp=now,source_candle_close_timestamp=now-1000)
    client=SimpleNamespace(fetch_last_price=lambda:100)
    check=cycle._build_entry_still_valid_fn(client,intent,{'donchian_upper':100,'donchian_lower':100})
    assert check()
    assert hasattr(check,'validate_price')
    assert not check.validate_price(quote,now_ms=max(now,int(time.time()*1000)))


@pytest.mark.parametrize('side',['long','short'])
def test_candidate_expired_approval_cannot_reuse_price_valid_setup(side):
    now=int(time.time()*1000)
    intent=replace(geometry(side),decision_timestamp=now-361000,source_candle_close_timestamp=now-361000)
    check=cycle._build_entry_still_valid_fn(SimpleNamespace(fetch_last_price=lambda:100),intent,{'donchian_upper':100,'donchian_lower':100})
    assert hasattr(check,'validate_price')
    assert not check.validate_price(100,now_ms=max(now,int(time.time()*1000)))

@pytest.mark.parametrize('side',['long','short'])
def test_core_execution_contract_does_not_reintroduce_tp1_only_rr(side):
    from adaptive_exit_engine import build_ai_price_contract, validate_ai_price_plan_contract
    sign=1 if side=='long' else -1
    contract=build_ai_price_contract(side,100,2,production_adaptive_exit_policy(),leverage=5,estimated_roundtrip_cost_rate=.001)
    # CORE's actual OCO targets full TP2; conditional defense is not a promised TP1 fill.
    contract['execution_target']='tp2'
    raw=dict(stop_loss_price=100-sign*4,take_profit_1_price=100+sign*4.4,take_profit_2_price=100+sign*8)
    assert validate_ai_price_plan_contract(raw,contract)=='ok'

@pytest.mark.parametrize('side,quote',[('long',98.98),('short',101.02)])
@pytest.mark.parametrize('expired',[False,True])
def test_real_candidate_submit_blocks_invalid_final_quote_or_clock(tmp_path,monkeypatch,side,quote,expired):
    from contextlib import nullcontext
    import symbol_entry_control
    cfg=SimpleNamespace(user_dir=str(tmp_path),logger=SimpleNamespace(info=lambda *a,**k:None),
        CANDIDATE_C_LIVE_EXECUTE=True,CANDIDATE_C_ORDER_MODE='FIXED_MARGIN_AUTO_EXIT',
        CANDIDATE_C_LEVERAGE=5,CANDIDATE_C_MAX_ORDER_NOTIONAL_USDT=5000,
        CANDIDATE_C_FIXED_MARGIN_USDT=150,CANDIDATE_C_RISK_PER_TRADE_PCT=1)
    now=int(time.time()*1000)
    intent=replace(geometry(side),decision_timestamp=now-(361000 if expired else 1000),source_candle_close_timestamp=now-1000)
    client=SimpleNamespace(fetch_last_price=lambda:100 if expired else quote,
        fetch_usdt_equity=lambda:1000,fetch_position=lambda:None,ensure_leverage=lambda lev:None,
        fetch_pending_protection_algo_ids=lambda:[],instrument_metadata=lambda:dict(contract_size=1,lot_step=.01,min_contracts=.01))
    check=cycle._build_entry_still_valid_fn(SimpleNamespace(fetch_last_price=lambda:100),intent,
        {'donchian_upper':100,'donchian_lower':100})
    monkeypatch.setattr(live,'_candidate_c_new_entry_allowed',lambda *a,**k:True)
    monkeypatch.setattr(live,'_candidate_manual_close_entry_block',lambda *a,**k:None)
    monkeypatch.setattr(live.gga,'verify_candidate_signal',lambda *a,**k:dict(allowed=True,gate_result='approved'))
    monkeypatch.setattr(live.ownership,'account_order_lock',lambda *a,**k:nullcontext())
    monkeypatch.setattr(symbol_entry_control,'is_paused',lambda *a,**k:False)
    ledger=SimpleNamespace(refresh=lambda:None,pending_intents=lambda:[])
    result=live._execute_entry(cfg,client,intent,{'atr_4h':2},1000,check,None,ledger=ledger,epoch_store=None)
    assert result['executed'] is False
    assert result['gate_result']=='blocked_final_signal_invalid_or_expired'

@pytest.mark.parametrize('side',['long','short'])
def test_core_gate_disabled_blocks_before_submission(tmp_path,monkeypatch,side):
    cfg,r=proposal(tmp_path,side);cfg.GPT_ENTRY_GATE_ENABLED=False;cfg.OPENAI_API_KEY=''
    monkeypatch.setattr(trader,'_reentry_blocked',lambda *a,**k:(False,None))
    monkeypatch.setattr(trader,'_evaluate_ai_close_thesis_entry_gate',lambda *a,**k:{'blocked':False})
    class Captured(Exception):pass
    def execute(*args,**kwargs):
        assert kwargs['decision']['_bounded_entry_validation']['plan'] == r['plan']
        raise Captured
    monkeypatch.setattr(trader,'_execute_approved_entry_with_optional_reversal',execute)
    state=TraderState()
    trader._handle_new_entry(cfg,state,None,'ADA/USDT:USDT',side,{'action':side,'confidence':.8},'test','entry',[], '',None,
        100,r['order_args'][1],r['order_args'][2],r['order_args'][3],adaptive_plan=r['plan'],adaptive_context=r['context'])
    assert state.snapshot()['symbols']['ADA/USDT:USDT']['last_entry_attempt']['status']=='GPT_ERROR'
    assert state.snapshot()['symbols']['ADA/USDT:USDT']['last_entry_attempt']['reason']=='core_gpt_entry_gate_disabled'

@pytest.mark.parametrize('side',['long','short'])
def test_core_expired_replacement_never_closes_existing_position(tmp_path,monkeypatch,side):
    cfg,c,d,sent,marker,args=boundary(tmp_path,monkeypatch,side,100,age=181)
    existing={'side':'short' if side=='long' else 'long','position_id':'held','entry_timestamp_ms':1000,'entry_price':100}
    c.fetch_position=lambda:existing
    monkeypatch.setattr(trader,'_approved_entry_still_valid_after_gpt',lambda *a,**k:True)
    closed=[]
    monkeypatch.setattr(trader,'_execute_close',lambda *a,**k:closed.append(True) or True)
    assert trader._execute_approved_entry_with_optional_reversal(cfg,TraderState(),c,'ADA/USDT:USDT',side,args[1],100,args[2],args[3],reversal_position=existing,decision_id='boundary-test',decision=d) is False
    assert closed==[]

@pytest.mark.parametrize('side,price',[('long',100.51),('short',99.49)])
def test_candidate_exact_final_quote_preserves_existing_chase_veto(side,price):
    now=int(time.time()*1000)
    intent=replace(geometry(side),decision_timestamp=now,source_candle_close_timestamp=now)
    recent=[dict(confirm=1,close_time_ms=now-(6-i)*300000,close=100) for i in range(7)]
    context=dict(bars_1h=[dict(confirm=1,close_time_ms=now-86400000,close=100),dict(confirm=1,close_time_ms=now,close=100)],atr14_4h=2,as_of_ms=now,bars_5m=recent,
        indicator_fn=lambda bars,**kwargs:[{'atr_14':1,'ema_20':100} for _ in bars])
    check=cycle._build_entry_still_valid_fn(SimpleNamespace(fetch_last_price=lambda:100),intent,
        {'donchian_upper':100,'donchian_lower':100,'entry_guard_context':context})
    assert check()
    assert not check.validate_price(price,now_ms=max(now,int(time.time()*1000)))

@pytest.mark.parametrize('side',['long','short'])
def test_recent_causal_bar_age_does_not_equal_approval_elapsed_time(side):
    now=int(time.time()*1000)
    intent=replace(geometry(side),decision_timestamp=now-240000,source_candle_close_timestamp=now-240000)
    check=cycle._build_entry_still_valid_fn(SimpleNamespace(fetch_last_price=lambda:100),intent,
        {'donchian_upper':100,'donchian_lower':100})
    assert check.validate_price(100,now_ms=max(now,int(time.time()*1000)))


def test_candidate_review_elapsed_expiry_blocks_even_when_candle_is_current(monkeypatch):
    now=int(time.time()*1000)
    intent=replace(geometry('long'),decision_timestamp=now,source_candle_close_timestamp=now)
    monkeypatch.setattr(cycle.time,'time',lambda:(now-181000)/1000)
    check=cycle._build_entry_still_valid_fn(SimpleNamespace(fetch_last_price=lambda:100),intent,
        {'donchian_upper':100,'donchian_lower':100})
    assert not check.validate_price(100,now_ms=max(now,int(time.time()*1000)))
