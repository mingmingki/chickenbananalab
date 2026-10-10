import logging
import time
from types import SimpleNamespace
import pytest
import trader
import candidate_c_hybrid_live_adapter as candidate
from config import UserConfig
from state import TraderState
from test_final_entry_submission_regression import boundary, proposal

SYMBOL='ADA/USDT:USDT'

@pytest.mark.parametrize('side',['long','short'])
def test_ai_approved_core_is_not_vetoed_by_local_chase(tmp_path,monkeypatch,side):
    cfg,c,d,sent,marker,args=boundary(tmp_path,monkeypatch,side,100.,no_pullback=True)
    cfg.CORE_AI_STRATEGY_AUTHORITY=True
    validation=d['_bounded_entry_validation']
    out=trader._core_final_entry_validation(cfg,c,SYMBOL,side,args[1],100,args[2],args[3],d,validation)
    assert out['allowed'],out
    assert out['values']['freshness']['allowed'] is False
    assert out['values']['freshness_authority']=='ai_review_evidence'

@pytest.mark.parametrize('fault',['minimum','expired','daily','drift'])
def test_core_ai_authority_preserves_execution_limits(tmp_path,monkeypatch,fault):
    cfg,c,d,sent,marker,args=boundary(tmp_path,monkeypatch,'short',101 if fault=='drift' else 100.,minimum=100 if fault=='minimum' else .01,age=181 if fault=='expired' else 0)
    cfg.CORE_AI_STRATEGY_AUTHORITY=True
    if fault=='daily':cfg._core_loss_guards[SYMBOL].allow_new_entry=lambda _:False
    out=trader._core_final_entry_validation(cfg,c,SYMBOL,'short',args[1],100,args[2],args[3],d,d['_bounded_entry_validation'])
    assert not out['allowed']
    assert not sent

@pytest.mark.parametrize('name,args',[
 ('_maybe_apply_core_profit_floor',('client','symbol','position','observation')),
 ('_maybe_execute_mfe_profit_live',('state','client','symbol','position','raw','observation')),
 ('_maybe_execute_profit_structure_live',('state','client','symbol','position','raw','closed','structures','observation')),
 ('_maybe_hard_loss_close',('state','client','symbol','position','raw','closed')),
 ('_maybe_emergency_close_near_stop',('state','client','symbol','position')),
 ('_core_adaptive_live_manage_held',('client','symbol','position','protection','features'))])
def test_core_chart_strategies_cannot_execute_without_ai(name,args):
    cfg=SimpleNamespace(CORE_AI_STRATEGY_AUTHORITY=True,EXECUTION_MODE='LIVE')
    client=SimpleNamespace() # any exchange access would fail
    values=dict(client=client,symbol=SYMBOL,position={'side':'short','contracts':4},observation={'live_candidate':{'trigger_price':100},'mfe_r':1.},state=TraderState(),raw={},closed={},structures={},protection={},features={})
    out=getattr(trader,name)(cfg,*[values[k] for k in args])
    assert not out or out.get('updated') is False


def test_candidate_chart_only_overrides_old_saved_ai_flags(tmp_path):
    (tmp_path/'.env').write_text('CANDIDATE_C_CHART_ONLY=true\nCANDIDATE_C_GPT_ENTRY_GATE_ENABLED=true\nCANDIDATE_C_AI_EXIT_PLAN_ENABLED=true\n')
    cfg=UserConfig(str(tmp_path))
    assert cfg.CANDIDATE_C_GPT_ENTRY_GATE_ENABLED is False
    assert cfg.CANDIDATE_C_AI_EXIT_PLAN_ENABLED is False


def test_candidate_chart_only_does_not_call_entry_or_price_ai(tmp_path,monkeypatch):
    cfg=SimpleNamespace(user_dir=str(tmp_path),logger=logging.getLogger('v10'),CANDIDATE_C_CHART_ONLY=True,CANDIDATE_C_GPT_ENTRY_GATE_ENABLED=True,CANDIDATE_C_AI_EXIT_PLAN_ENABLED=True,CANDIDATE_C_LIVE_EXECUTE=True)
    intent=SimpleNamespace(reason_code='chart_signal',symbol='DOGE/USDT:USDT',side='short',setup_id='chart')
    monkeypatch.setattr(candidate,'_candidate_c_new_entry_allowed',lambda *a:True)
    monkeypatch.setattr(candidate,'_candidate_manual_close_entry_block',lambda *a:None)
    def forbidden(*a,**k):raise AssertionError('Candidate AI called')
    monkeypatch.setattr(candidate.gga,'verify_candidate_signal',forbidden)
    monkeypatch.setattr(candidate.gga,'review_exit_plan_only',forbidden)
    calls=[]
    monkeypatch.setattr(candidate.gga,'rule_based_entry_without_gpt',lambda *a,**k:calls.append('chart') or {'allowed':False,'gate_result':'blocked_stale','error_reason':None})
    result=candidate._execute_entry(cfg,SimpleNamespace(),intent,{},1000,lambda:False,None)
    assert calls==['chart']
    assert result['executed'] is False


@pytest.mark.parametrize('side',['long','short'])
@pytest.mark.parametrize('gate',['approved','TIMEOUT_BYPASS','blocked_wait','blocked_reject'])
def test_core_entry_uses_gemini_prices_for_approval_and_timeout(tmp_path,monkeypatch,side,gate):
    cfg,r=proposal(tmp_path,side)
    cfg.CORE_AI_STRATEGY_AUTHORITY=True
    cfg.GPT_ENTRY_GATE_ENABLED=True;cfg.OPENAI_API_KEY='test';cfg.MIN_CONFIDENCE=.6
    cfg.REENTRY_COOLDOWN_MINUTES=15
    cfg._core_loss_guards={SYMBOL:SimpleNamespace(allow_new_entry=lambda _:True)}
    cfg.CORE_GPT_ENTRY_TIMEOUT_BYPASS=True
    client=SimpleNamespace(contract_size=lambda:1,fetch_usdt_equity=lambda:1000,fetch_last_price=lambda:100,fetch_position=lambda:None,ensure_markets_loaded=lambda:None,exchange=SimpleNamespace(market=lambda _:dict(contractSize=1,precision={'amount':.01},limits={'amount':{'min':.01}})))
    sign=1 if side=='long' else -1
    plan=dict(stop_loss_price=100-sign*3,take_profit_1_price=100+sign*4.5,take_profit_2_price=100+sign*9)
    decision=dict(action=side,confidence=.8,exit_plan=plan,_approval_started_at=time.time(),_bar_closed_at=time.time())
    result=dict(decision='approve_now' if gate=='approved' else 'TIMEOUT' if gate=='TIMEOUT_BYPASS' else gate[8:],confidence=.8,exit_plan_decision='approve',gate_reason='test')
    monkeypatch.setattr(trader,'_gpt_entry_gate',lambda *a,**k:(gate in ('approved','TIMEOUT_BYPASS'),gate,result))
    orders=[]
    monkeypatch.setattr(trader,'_execute_entry',lambda *a,**k:orders.append((a[7],a[8])) or True)
    monkeypatch.setattr(trader.ai_exit_plan_audit,'enrich_with_exchange_protection',lambda c,r:r)
    trader._handle_new_entry(cfg,TraderState(),client,SYMBOL,side,decision,'v10-entry','entry',['5m'],'summary',None,100,10,r['order_args'][2],r['order_args'][3],adaptive_plan=r['plan'],adaptive_context=r['context'],closed_dfs={})
    assert orders==([(plan['stop_loss_price'],plan['take_profit_2_price'])] if gate in ('approved','TIMEOUT_BYPASS') else [])


def test_ai_core_never_falls_back_to_chart_prices_when_gemini_plan_missing(tmp_path,monkeypatch):
    from test_core_dual_ai_consensus import entry_env,invoke
    env=entry_env.__wrapped__(tmp_path,monkeypatch)
    env[0].CORE_AI_STRATEGY_AUTHORITY=True
    invoke(env,monkeypatch)
    assert env[-1]==[]


def test_core_ai_close_not_downgraded_by_market_rules_or_reduce_cooldown(tmp_path,monkeypatch):
    from test_dual_ai_invalidated_close import Cfg,Client
    cfg=Cfg(tmp_path);cfg.CORE_AI_STRATEGY_AUTHORITY=True
    pos=dict(side='short',contracts=4,entry_price=100,mark_price=99)
    monkeypatch.setattr(trader.gemini_analyzer,'analyze_held_position',lambda *a,**k:dict(assessment='weakening',confidence=.8))
    monkeypatch.setattr(trader.openai_analyzer,'verify_position_management',lambda *a,**k:dict(action='CLOSE_ALL',confidence=.8))
    monkeypatch.setattr(trader,'_position_ai_close_guard',lambda *a,**k:dict(allow_close_all=False,reason='not_enough_loss',threshold_r=.2))
    monkeypatch.setattr(trader,'_post_reduce_close_cooldown',lambda *a,**k:dict(blocked=True,remaining_seconds=800))
    closes=[]
    monkeypatch.setattr(trader,'_execute_close',lambda *a,**k:closes.append(k['reason']) or True)
    monkeypatch.setattr(trader,'_execute_position_ai_reduce_50',lambda *a,**k:pytest.fail('AI close downgraded'))
    trader._handle_position_ai_review(cfg,TraderState(),Client({'sl_price':110}),SYMBOL,['5m'],'summary',pos,{'action':'hold'},{})
    assert closes==['position_ai_close_all']


def test_core_held_timeout_never_fabricates_reduce_approval(tmp_path,monkeypatch):
    from test_dual_ai_invalidated_close import Cfg,Client
    cfg=Cfg(tmp_path);cfg.CORE_AI_STRATEGY_AUTHORITY=True
    monkeypatch.setattr(trader.gemini_analyzer,'analyze_held_position',lambda *a,**k:dict(assessment='weakening',confidence=.9))
    monkeypatch.setattr(trader.openai_analyzer,'verify_position_management',lambda *a,**k:dict(action=None,error_reason='timeout'))
    monkeypatch.setattr(trader,'_execute_position_ai_reduce_50',lambda *a,**k:pytest.fail('unapproved held reduce'))
    trader._handle_position_ai_review(cfg,TraderState(),Client({'sl_price':98}),SYMBOL,['5m'],'summary',dict(side='long',contracts=10,entry_price=100),dict(action='hold'),{},review_path='negative_guard')


def test_gemini_held_prompt_contains_profit_protection_authority(tmp_path,monkeypatch):
    import json
    prompts=[]
    cfg=SimpleNamespace(user_dir=str(tmp_path),logger=logging.getLogger('prompt'),CORE_AI_STRATEGY_AUTHORITY=True,GEMINI_MODEL='test')
    monkeypatch.setattr(trader.gemini_analyzer,'_get_client',lambda _:object())
    monkeypatch.setattr(trader.gemini_analyzer,'_generate_content_observed',lambda *a,**k:prompts.append(k['contents']) or SimpleNamespace(text=json.dumps(dict(assessment='thesis_intact',confidence=.9)),usage_metadata=None))
    trader.gemini_analyzer.analyze_held_position(cfg,SYMBOL,['5m'],'MFE evidence',dict(side='long',contracts=4,unrealized_pnl=20),dict(action='hold'))
    assert 'CORE 보유 전략 권한' in prompts[0]
    assert '부분익절' in prompts[0]


@pytest.mark.parametrize('action',['long','short'])
def test_same_side_primary_decision_still_runs_held_ai(tmp_path,monkeypatch,action):
    from test_core_ai_budget_gate_20261004 import cycle_fixture
    from unittest.mock import Mock
    position=dict(side=action,contracts=1.,entry_price=100.,position_id='p1',entry_timestamp_ms=1)
    cfg,state,client=cycle_fixture(tmp_path,monkeypatch,position)
    cfg.CORE_AI_STRATEGY_AUTHORITY=True;cfg.MIN_CONFIDENCE=.6;cfg.LEVERAGE=5
    cfg.POSITION_AI_REVIEW_COOLDOWN_MINUTES=15
    monkeypatch.setattr(trader,'_position_ai_review_candidate',lambda *a:True)
    monkeypatch.setattr(trader,'_min_hold_elapsed',lambda *a:True)
    monkeypatch.setattr(trader.gemini_analyzer,'analyze',lambda *a,**k:dict(action=action,confidence=.8,reasoning='same thesis'))
    review=Mock();monkeypatch.setattr(trader,'_handle_position_ai_review',review)
    trader.run_cycle(cfg,state,client,'BTC/USDT:USDT',None)
    assert review.call_count==1


@pytest.mark.parametrize('side',['long','short'])
def test_ai_reduce_two_stages_preserves_gemini_stop_and_oco(tmp_path,monkeypatch,side):
    cfg=SimpleNamespace(user_dir=str(tmp_path),logger=logging.getLogger('reduce'),CORE_AI_STRATEGY_AUTHORITY=True,EXECUTION_MODE='LIVE',MIN_CONFIDENCE=.6)
    position=dict(side=side,contracts=10.,entry_price=100.,position_id='p1',entry_timestamp_ms=1)
    sl=98. if side=='long' else 102.;tp=104. if side=='long' else 96.
    class Client:
        symbol=SYMBOL
        def __init__(self):
            self.position=dict(position);self.protection=dict(algo_id='original-oco',side='sell' if side=='long' else 'buy',state='live',sz=10.,sl_price=sl,tp_price=tp);self.orders=[];self.amends=[];self.cfg=cfg;self.exchange=SimpleNamespace(market=lambda _:dict(id='ADA-USDT-SWAP',precision=dict(amount=.1,price=.1)))
        def fetch_position(self):return dict(self.position)
        def fetch_current_protection(self,*a):return dict(self.protection)
        def fetch_protection_order_by_algo_id(self,*a):return dict(self.protection)
        def contract_size(self):return 1.
        def fetch_pending_protection_orders(self):return [dict(instId='ADA-USDT-SWAP',algoId='original-oco',side=self.protection['side'],state='live',reduceOnly='true',sz=self.protection['sz'],slTriggerPx=sl,tpTriggerPx=tp)]
        def fetch_last_price(self):return 102. if side=='long' else 98.
        def fetch_multi_ohlcv(self,*a):return {'5m':object()}
        def reduce_position(self,p,contracts,client_order_id):
            self.orders.append(contracts);self.position['contracts']-=contracts;return dict(id=client_order_id)
        def fetch_order_status_by_client_id(self,cid):return dict(id=cid,status='closed',filled=self.orders[-1])
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
    for stage in (1,2):
        approved=dict(_core_reduce_approval=dict(started_at=time.time(),path='ai_strategy',lifecycle_id=trader.reduce_v2_state.position_identity(position),bar_ts=bar[0],gemini_confidence=.9))
        original=getattr(trader._execute_position_ai_reduce_50_locked,'__wrapped__',trader._execute_position_ai_reduce_50_locked)
        assert original(cfg,state,client,SYMBOL,client.fetch_position(),'review',.9,'thesis_intact',approved)
        assert client.protection['sl_price']==sl
        assert client.protection['tp_price']==tp
        assert client.protection['sz']==10.-stage*2.5
        assert client.protection['algo_id']=='original-oco'
        bar[0]='bar2'
    assert client.orders==[2.5,2.5]
    assert trader.reduce_v2_state.get(cfg.user_dir,SYMBOL)['pending_order'] is None
    assert trader.reduce_v2_state.get(cfg.user_dir,SYMBOL)['cumulative_reduced_ratio']==.5


def test_chart_profit_event_requests_ai_and_respects_review_cooldown(tmp_path,monkeypatch):
    cfg=SimpleNamespace(user_dir=str(tmp_path),logger=logging.getLogger('event'),CORE_AI_STRATEGY_AUTHORITY=True,POSITION_AI_REVIEW_COOLDOWN_MINUTES=15)
    state=TraderState();position=dict(side='long',contracts=4,entry_price=100)
    monkeypatch.setattr(trader,'_position_ai_review_candidate',lambda *a:True)
    monkeypatch.setattr(trader,'_closed_indicator_tail',lambda *a,**k:[dict(ts='bar',macd=1.)])
    monkeypatch.setattr(trader.core_entry_timing,'confirmed_frame',lambda *a:object())
    monkeypatch.setattr(trader,'_observe_mfe_profit_shadow',lambda *a:dict(mfe_r=2.,current_r=1.,giveback_r=1.,live_candidate={'reason':'mfe_giveback'}))
    calls=[]
    def review(*a,**k):
        calls.append(a);state.update_symbol(SYMBOL,position_ai_review_block_until=__import__('datetime').datetime.now()+__import__('datetime').timedelta(minutes=15))
    monkeypatch.setattr(trader,'_handle_position_ai_review',review)
    for _ in range(2):trader._core_fast_profit_observation(cfg,state,object(),SYMBOL,position,{'1m':object(),'5m':object()})
    assert len(calls)==1
    assert 'MFE' in calls[0][5]


@pytest.mark.parametrize('fault',['none','nan','wide_stop','budget_spent'])
def test_ai_add_is_capped_by_actual_gemini_stop(fault):
    from strategy_authority import bounded_add_coin
    cfg=SimpleNamespace(LEVERAGE=5,RISK_PER_TRADE_PCT=5,MAX_DAILY_LOSS_PCT=5)
    pos=dict(side='long',entry_price=100,contracts=10.)
    amount=bounded_add_coin(cfg,pos,equity=1000,price=float('nan') if fault=='nan' else 100,stop=90 if fault=='wide_stop' else 95 if fault=='budget_spent' else 98,proposed=20,contract_size=1)
    if fault=='none':
        assert 0<amount<20
        assert 20+1+amount*2.1<=50+1e-9
    else:assert amount==0


def test_saved_ada_dual_approval_geometry_is_feasible_without_chart_atr_prior():
    from strategy_authority import core_price_policy
    from adaptive_exit_engine import build_ai_price_contract,validate_ai_price_plan_contract
    cfg=SimpleNamespace(CORE_AI_STRATEGY_AUTHORITY=True)
    entry=.2544;atr=.0100/1.4110110311121546
    policy=core_price_policy(cfg,trader.production_adaptive_exit_policy(),entry=entry,atr=atr)
    contract=build_ai_price_contract('short',entry,atr,policy,leverage=5,estimated_roundtrip_cost_rate=.001)
    contract['execution_target']='tp2'
    assert validate_ai_price_plan_contract(dict(stop_loss_price=.2644,take_profit_1_price=.2392,take_profit_2_price=.2239),contract)=='ok'
    assert contract['max_stop_distance_pct']==4


def test_ai_management_review_is_single_flight(tmp_path):
    from strategy_authority import single_review
    import threading
    started=threading.Event();release=threading.Event();calls=[]
    @single_review
    def review(cfg,state,client,symbol):
        calls.append(symbol);started.set();release.wait(2)
    cfg=SimpleNamespace(CORE_AI_STRATEGY_AUTHORITY=True,user_dir=str(tmp_path))
    worker=threading.Thread(target=review,args=(cfg,None,None,SYMBOL));worker.start()
    assert started.wait(1)
    try:review(cfg,None,None,SYMBOL)
    finally:release.set();worker.join()
    assert calls==[SYMBOL]
