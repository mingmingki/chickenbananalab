import importlib.util,math,time,json
from types import SimpleNamespace
import pytest
import trader,gemini_analyzer as gemini,core_ai_context
from test_core_ai_cost_v13 import config,POSITION,PROTECTION,REVIEW,SYMBOL,provider,PAYLOAD
import state as trader_state

def policy():
    assert importlib.util.find_spec('core_management_policy') is not None,'missing Gemini/code management policy'
    import core_management_policy
    return core_management_policy

def cfg(tmp_path):
    c=config(tmp_path);c.CORE_GEMINI_MANAGEMENT_ONLY=True;c.POSITION_AI_REVIEW_ENABLED=True
    return c

def review(action='HOLD',**kw):
    return dict(REVIEW,management_action=action,**kw)

@pytest.mark.parametrize('bad',[{},None,[],{'management_action':[]},{'management_action':'BUY'}])
def test_bad_management_review_does_not_authorize(tmp_path,bad):
    assert not policy().valid_review(cfg(tmp_path),bad)

@pytest.mark.parametrize('action',['HOLD','REDUCE_50','CLOSE_ALL','ADD_POSITION'])
def test_explicit_valid_management_action(tmp_path,action):
    assert policy().valid_review(cfg(tmp_path),review(action))

def test_code_fraction_varies_with_observed_loss_not_confidence(tmp_path):
    p=policy();c=cfg(tmp_path);rows=[dict(close=.2541,atr_14=.002)]
    small=p.decision(c,review('REDUCE_50'),dict(POSITION,mark_price=.254),PROTECTION,rows,{})
    large=p.decision(c,review('REDUCE_50'),dict(POSITION,mark_price=.248),PROTECTION,rows,{})
    assert .05<=small['reduce_fraction']<large['reduce_fraction']<=.50
    assert small['reduce_fraction']==p.decision(c,dict(review('REDUCE_50'),confidence=.99),dict(POSITION,mark_price=.254),PROTECTION,rows,{})['reduce_fraction']

def test_unknown_geometry_is_hold(tmp_path):
    d=policy().decision(cfg(tmp_path),review('REDUCE_50'),POSITION,PROTECTION,[],{})
    assert d['action']=='HOLD' and d['reasoning']=='code_risk_geometry_unavailable'

@pytest.mark.parametrize('action',['HOLD','REDUCE_50','CLOSE_ALL'])
def test_management_does_not_call_gpt(tmp_path,monkeypatch,action):
    c=cfg(tmp_path);state=trader_state.TraderState();calls=[]
    client=SimpleNamespace(fetch_current_protection=lambda _:dict(PROTECTION))
    monkeypatch.setattr(trader,'_position_management_evidence',lambda *a:'')
    monkeypatch.setattr(core_ai_context,'reuse',lambda *a:review(action))
    monkeypatch.setattr(trader,'_closed_indicator_tail',lambda *a,**k:[dict(ts='2026-10-11T00:00:00',close=.2541,atr_14=.002)])
    monkeypatch.setattr(trader.openai_analyzer,'verify_position_management',lambda *a,**k:pytest.fail('paid GPT management called'))
    monkeypatch.setattr(trader,'_execute_close',lambda *a,**k:calls.append('close') or True)
    monkeypatch.setattr(trader,'_execute_position_ai_reduce_50',lambda *a,**k:calls.append('reduce') or True)
    trader._handle_position_ai_review(c,state,client,SYMBOL,['5m'],'facts',dict(POSITION),dict(PAYLOAD),{})
    assert calls==({'HOLD':[],'REDUCE_50':['reduce'],'CLOSE_ALL':['close']}[action])

@pytest.mark.parametrize('verdict',['approve_now','wait','reject','TIMEOUT'])
def test_add_uses_entry_gpt_gate_only(tmp_path,monkeypatch,verdict):
    c=cfg(tmp_path);state=trader_state.TraderState();calls=[];gates=[]
    client=SimpleNamespace(fetch_current_protection=lambda _:dict(PROTECTION),fetch_last_price=lambda:.2541)
    monkeypatch.setattr(trader,'_position_management_evidence',lambda *a:'')
    monkeypatch.setattr(core_ai_context,'reuse',lambda *a:review('ADD_POSITION'))
    monkeypatch.setattr(trader,'_closed_indicator_tail',lambda *a,**k:[])
    monkeypatch.setattr(trader.openai_analyzer,'verify_position_management',lambda *a,**k:pytest.fail('GPT management called'))
    def verify(*args,**kwargs):
        gates.append(kwargs.get('purpose'))
        if verdict=='TIMEOUT':
            return dict(decision='TIMEOUT',error_reason='timeout',error_type='APITimeoutError',timeout_confirmed=True,request_purpose='entry_gate')
        return dict(decision=verdict,confidence=.9,exit_plan_decision='approve')
    c.CORE_GPT_ENTRY_TIMEOUT_BYPASS=True
    monkeypatch.setattr(trader.openai_analyzer,'verify',verify)
    monkeypatch.setattr(trader,'_execute_position_ai_add',lambda *a,**k:calls.append(a[-1]) or True)
    trader._handle_position_ai_review(c,state,client,SYMBOL,['5m'],'facts',dict(POSITION),dict(PAYLOAD),{})
    assert gates==['entry_gate']
    assert bool(calls)==(verdict in ['approve_now','TIMEOUT'])
    if calls:assert calls[0]['_core_add_approval']['entry_gate'] in ['approved','TIMEOUT_BYPASS']

def test_primary_requires_action_and_reuses_it(tmp_path,monkeypatch):
    c=cfg(tmp_path);payload=dict(PAYLOAD,position_review=review('CLOSE_ALL'));req=provider(monkeypatch,payload)
    decision=gemini.analyze(c,SYMBOL,['5m'],'facts',dict(POSITION),protection=dict(PROTECTION))
    schema=req[0]['config'].response_schema
    assert 'management_action' in schema['properties']['position_review']['required']
    assert core_ai_context.reuse(decision,c,SYMBOL,['5m'],'facts',dict(POSITION),dict(PROTECTION))['management_action']=='CLOSE_ALL'

def test_gemini_candidate_does_not_need_openai_key_for_management(tmp_path):
    c=cfg(tmp_path);c.OPENAI_API_KEY='';c.GEMINI_API_KEY='test'
    assert trader._position_ai_review_candidate(c,POSITION)

def test_manual_paid_pattern_review_is_disabled(tmp_path,monkeypatch):
    import web_app
    c=cfg(tmp_path)
    monkeypatch.setattr(web_app,'get_context',lambda _:SimpleNamespace(cfg=c,dir=str(tmp_path)))
    monkeypatch.setattr(web_app,'_queue_manual_strategy_review',lambda *_:pytest.fail('paid pattern AI invoked'))
    with web_app.app.test_client() as client:
        with client.session_transaction() as session:session['username']='chickenbananalab';session['authenticated']=True
        response=client.post('/api/analysis/review-now',json={})
    assert response.status_code==410

def test_risk_schema_required_even_if_legacy_adaptive_flag_off(tmp_path,monkeypatch):
    c=cfg(tmp_path);c.RISK_ADAPTIVE_PARTIAL_ENABLED=False
    req=provider(monkeypatch,dict(PAYLOAD,position_review=review()))
    gemini.analyze(c,SYMBOL,['5m'],'facts',dict(POSITION),protection=dict(PROTECTION))
    assert 'risk_level' in req[0]['config'].response_schema['properties']['position_review']['required']

def test_real_cycle_reuses_one_gemini_and_calls_no_gpt_for_hold(tmp_path,monkeypatch):
    from test_core_ai_budget_gate_20261004 import cycle_fixture
    c,s,client=cycle_fixture(tmp_path,monkeypatch,dict(POSITION))
    c.__dict__.update(cfg(tmp_path).__dict__);c.LEVERAGE=5;c.POSITION_AI_PERIODIC_HOLD_REVIEW_ENABLED=True
    client.fetch_current_protection=lambda _:dict(PROTECTION)
    monkeypatch.setattr(trader,'_position_ai_review_candidate',lambda *a:True)
    monkeypatch.setattr(trader,'_min_hold_elapsed',lambda *a:True)
    req=provider(monkeypatch,dict(PAYLOAD,position_review=review()))
    monkeypatch.setattr(trader.openai_analyzer,'verify_position_management',lambda *a,**k:pytest.fail('GPT held call'))
    monkeypatch.setattr(trader.openai_analyzer,'verify',lambda *a,**k:pytest.fail('GPT entry during hold'))
    trader.run_cycle(c,s,client,SYMBOL,None)
    assert [r['purpose'] for r in req]==['core_primary_decision']

@pytest.mark.parametrize('side',['long','short'])
def test_real_reduce_executor_code_quantity_and_same_oco_with_legacy_flag_off(tmp_path,monkeypatch,side):
    from test_risk_adaptive_partials_v11 import reduce_fixture,approval
    c,client,s,p,b,sl,tp=reduce_fixture(tmp_path,monkeypatch,side)
    c.CORE_GEMINI_MANAGEMENT_ONLY=True;c.RISK_ADAPTIVE_PARTIAL_ENABLED=False
    for i,fraction in enumerate([.1,.15,.35]):
        b[0]=f'v15-bar-{i}'
        raw=approval(p,b[0],fraction)
        raw['_core_reduce_approval'].update(authority='gemini_management',management_action='REDUCE_50',
            code_fraction_policy=policy().POLICY_HASH,gemini_reduce_fraction=0.)
        assert trader._execute_position_ai_reduce_50_locked(c,s,client,client.symbol,client.fetch_position(),
            'v15-executor',.9,'thesis_intact',raw)
        assert client.protection['algo_id']=='original-oco'
        assert (client.protection['sl_price'],client.protection['tp_price'])==(sl,tp)
        assert client.protection['sz']==client.position['contracts']
    assert client.orders==pytest.approx([1.,1.5,2.5])

@pytest.mark.parametrize('verdict',['wait','reject','TIMEOUT','approve_now'])
def test_add_proof_revalidates_entry_verdict(tmp_path,verdict):
    c=cfg(tmp_path);c.CORE_GPT_ENTRY_TIMEOUT_BYPASS=True
    candidate=dict(action='long',confidence=.9,exit_plan=dict(stop_loss_price=.244,
        take_profit_1_price=.28,take_profit_2_price=.28))
    proof=dict(entry_candidate=candidate,entry_result=dict(decision=verdict,confidence=.9,
        exit_plan_decision='approve'),entry_gate='approved',entry_side='long',
        entry_mark_price=.2541,entry_protection=dict(PROTECTION))
    assert policy().valid_add_approval(c,SYMBOL,proof)==(verdict=='approve_now')

@pytest.mark.parametrize('field',['sl_price','tp_price'])
def test_add_proof_prices_must_match_existing_protection(tmp_path,field):
    c=cfg(tmp_path)
    proof=dict(entry_candidate=dict(action='long',confidence=.9,exit_plan=dict(stop_loss_price=.244,
        take_profit_1_price=.28,take_profit_2_price=.28)),entry_result=dict(decision='approve_now',
        confidence=.9,exit_plan_decision='approve'),entry_gate='approved',entry_side='long',
        entry_mark_price=.2541,entry_protection=dict(PROTECTION))
    proof['entry_protection'][field]+=.001
    assert not policy().valid_add_approval(c,SYMBOL,proof)

def test_manual_core_exit_prices_do_not_request_held_management_schema(tmp_path,monkeypatch):
    c=cfg(tmp_path);c.GEMINI_API_KEY='test';calls=[]
    monkeypatch.setattr(gemini,'_get_client',lambda _:object())
    monkeypatch.setattr(gemini,'_generate_content_observed',lambda *a,**k:calls.append(k) or
        SimpleNamespace(text=json.dumps(dict(exit_plan=dict(stop_loss_price=.244,
        take_profit_1_price=.27,take_profit_2_price=.28),confidence=.9,reasoning='structure')),usage_metadata=None))
    result=gemini.propose_entry_exit_plan(c,SYMBOL,'long',['5m'],'facts',.244,.28,manual_core=True)
    assert result['exit_plan']['stop_loss_price']==.244
    assert 'management_action' not in calls[0]['config'].response_schema['properties']

def test_gpt_entry_only_disables_old_paid_shadow_flags(tmp_path,monkeypatch):
    c=cfg(tmp_path);c.CORE_PAID_SHADOW_ENABLED=True
    monkeypatch.setattr(trader.threading,'Thread',lambda *a,**k:pytest.fail('paid shadow thread'))
    trader._fire_shadow_verification_async(c,SYMBOL,['5m'],'facts',POSITION,PAYLOAD,'id','signal_close')
    trader._fire_hold_audit_async(c,trader_state.TraderState(),SYMBOL,['5m'],'facts',PAYLOAD,.2541)

@pytest.mark.parametrize('held_action',['HOLD','REDUCE_50','ADD_POSITION',None])
def test_reversal_execution_never_overrides_explicit_held_action(tmp_path,monkeypatch,held_action):
    import core_entry_events
    c=cfg(tmp_path);s=trader_state.TraderState();d=dict(action='short',confidence=.9,_held_review=review(held_action))
    monkeypatch.setattr(trader.core_unified_service,'owner',lambda *a:'legacy')
    monkeypatch.setattr(core_entry_events,'pending_order',lambda *a:None)
    monkeypatch.setattr(trader,'_execute_close',lambda *a,**k:pytest.fail('contradictory Gemini close'))
    assert not trader._execute_approved_entry_with_optional_reversal(c,s,SimpleNamespace(),SYMBOL,
        'short',10.,.2541,.26,.24,reversal_position=dict(POSITION),decision_id='v15-reversal',decision=d)
    assert d['_entry_outcome']['reason']=='gemini_reversal_close_not_authorized'
