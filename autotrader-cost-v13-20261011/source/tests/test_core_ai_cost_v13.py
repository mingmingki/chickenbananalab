import copy
import json
import logging
import time
from types import SimpleNamespace

import pytest
import gemini_analyzer as gemini
import trader
import position_management_context as evidence
from state import TraderState

SYMBOL = 'BTC/USDT:USDT'
POSITION = dict(side='long', contracts=10., entry_price=.2541, mark_price=.259,
                unrealized_pnl=2., position_id='p1', entry_timestamp_ms=1)
PROTECTION = dict(algo_id='oco1', sl_price=.244, tp_price=.28, sz=10.)
REVIEW = dict(assessment='thesis_intact', confidence=.85, reasoning='trend; protect giveback',
              risk_level='medium', suggested_reduce_fraction=.15)
PAYLOAD = dict(action='hold', confidence=.85, reasoning='retain thesis', market_regime='bullish',
               regime_confidence=.85, trade_alignment='with_regime', exit_plan=None,
               position_review=REVIEW)


def config(tmp_path):
    return SimpleNamespace(user_dir=str(tmp_path), logger=logging.getLogger('cost-v13'),
        CORE_AI_STRATEGY_AUTHORITY=True, RISK_ADAPTIVE_PARTIAL_ENABLED=True,
        GEMINI_MODEL='test', MIN_CONFIDENCE=.6, OPENAI_API_KEY='test',
        POSITION_AI_REVIEW_COOLDOWN_MINUTES=15, POSITION_AI_LIVE_EXECUTE=True)


def provider(monkeypatch, payload=PAYLOAD):
    requests = []
    monkeypatch.setattr(gemini, '_get_client', lambda _: object())
    def generate(cfg, symbol, purpose, **kwargs):
        requests.append(dict(purpose=purpose, **kwargs))
        body = payload if purpose == 'core_primary_decision' else REVIEW
        return SimpleNamespace(text=json.dumps(body), usage_metadata=None)
    monkeypatch.setattr(gemini, '_generate_content_observed', generate)
    return requests


def test_cycle_calls_gemini_once_and_keeps_gpt_management(tmp_path, monkeypatch):
    from test_core_ai_budget_gate_20261004 import cycle_fixture
    cfg, state, client = cycle_fixture(tmp_path, monkeypatch, dict(POSITION))
    cfg.__dict__.update(config(tmp_path).__dict__)
    cfg.LEVERAGE=5; cfg.POSITION_AI_PERIODIC_HOLD_REVIEW_ENABLED=True
    client.fetch_current_protection=lambda _:dict(PROTECTION)
    monkeypatch.setattr(trader, '_position_ai_review_candidate', lambda *a:True)
    monkeypatch.setattr(trader, '_min_hold_elapsed', lambda *a:True)
    requests=provider(monkeypatch); gpt=[]
    def verify(*a, **k):
        gpt.append((a,k)); return dict(action='HOLD',confidence=.9,reasoning='retain')
    monkeypatch.setattr(trader.openai_analyzer, 'verify_position_management', verify)
    trader.run_cycle(cfg,state,client,SYMBOL,None)
    assert len(requests)==1
    assert len(gpt)==1
    assert gpt[0][0][6]['suggested_reduce_fraction']==.15
    assert set(gpt[0][1]['allowed_actions'])=={'HOLD','REDUCE_50','CLOSE_ALL','ADD_POSITION'}
    assert gpt[0][0][3].count('[POSITION_MANAGEMENT_EVIDENCE]')==1


@pytest.mark.parametrize('fault',['expired','future','position','mark','protection','summary','symbol','timeframes','risk','missing','confidence'])
def test_changed_or_invalid_context_cannot_reuse(tmp_path,monkeypatch,fault):
    cfg=config(tmp_path); provider(monkeypatch)
    decision=gemini.analyze(cfg,SYMBOL,['5m'],'facts',dict(POSITION),protection=dict(PROTECTION))
    import core_ai_context as context
    pos=dict(POSITION);prot=dict(PROTECTION);summary='facts';symbol=SYMBOL;tfs=['5m']
    if fault=='expired':decision['_held_review_snapshot']['created_at']=time.time()-61
    if fault=='future':decision['_held_review_snapshot']['created_at']=time.time()+1
    if fault=='position':pos['position_id']='p2'
    if fault=='mark':pos['mark_price']=.26
    if fault=='protection':prot['sl_price']=.245
    if fault=='summary':summary='changed MFE/reduction or bar'
    if fault=='symbol':symbol='ADA/USDT:USDT'
    if fault=='timeframes':tfs=['1m']
    if fault=='risk':decision['_held_review']['suggested_reduce_fraction']=.01
    if fault=='missing':decision.pop('_held_review_snapshot')
    if fault=='confidence':decision['_held_review']['confidence']=float('nan')
    assert context.reuse(decision,cfg,symbol,tfs,summary,pos,prot) is None


@pytest.mark.parametrize('action',['hold','close','short','long'])
def test_valid_unified_response_reusable_for_all_primary_actions(tmp_path,monkeypatch,action):
    payload=dict(PAYLOAD,action=action);provider(monkeypatch,payload)
    cfg=config(tmp_path)
    decision=gemini.analyze(cfg,SYMBOL,['5m'],'facts',dict(POSITION),protection=dict(PROTECTION))
    import core_ai_context as context
    assert context.reuse(decision,cfg,SYMBOL,['5m'],'facts',dict(POSITION),dict(PROTECTION))==REVIEW


@pytest.mark.parametrize('fault',['stale','protection','evidence','missing_review'])
def test_handler_falls_back_to_fresh_gemini_and_keeps_gpt(tmp_path,monkeypatch,fault):
    cfg=config(tmp_path); requests=provider(monkeypatch)
    summary=evidence.render(dict(mfe_known=False,reduction_known=False))
    monkeypatch.setattr(trader,'_position_management_evidence',lambda *a:summary)
    decision=gemini.analyze(cfg,SYMBOL,['5m'],summary,dict(POSITION),protection=dict(PROTECTION))
    if fault=='stale':decision['_held_review_snapshot']['created_at']=time.time()-61
    if fault=='missing_review':decision.pop('_held_review')
    if fault=='evidence':monkeypatch.setattr(trader,'_position_management_evidence',lambda *a:evidence.render(dict(mfe_known=True,mfe_r=2)))
    prot=dict(PROTECTION,sz=9) if fault=='protection' else dict(PROTECTION)
    client=SimpleNamespace(fetch_current_protection=lambda _:prot)
    gpt=[]
    monkeypatch.setattr(trader.openai_analyzer,'verify_position_management',lambda *a,**k:gpt.append(a) or dict(action='HOLD',confidence=.9))
    trader._handle_position_ai_review(cfg,TraderState(),client,SYMBOL,['5m'],summary,dict(POSITION),decision,{})
    assert [r['purpose'] for r in requests]==['core_primary_decision','position_ai_review']
    assert len(gpt)==1
    assert gpt[0][3].count('[POSITION_MANAGEMENT_EVIDENCE]')==1


def test_evidence_replace_retains_unrelated_facts():
    import core_ai_context as context
    old=evidence.render(dict(mfe_known=False))
    new=evidence.render(dict(mfe_known=True,mfe_r=2))
    result=context.with_evidence('price=.2541'+old+'\nextra bar/protection fact',new)
    assert result.count('[POSITION_MANAGEMENT_EVIDENCE]')==1
    assert 'price=.2541' in result and 'extra bar/protection fact' in result
    assert '"mfe_r":2' in result and '"mfe_known":false' not in result


def test_core_prompt_compact_and_retains_price_risk_authority(tmp_path,monkeypatch):
    cfg=config(tmp_path); requests=provider(monkeypatch)
    gemini.analyze(cfg,SYMBOL,['5m'],'MFE unknown; 5m bar facts',dict(POSITION),protection=dict(PROTECTION))
    prompt=requests[0]['contents'];schema=requests[0]['config'].response_schema
    assert '.2541' in prompt and '.244' in prompt and '.28' in prompt
    assert '부분익절' in prompt and '부분손절' in prompt and '추가' in prompt
    assert 'position_review' in schema['properties']
    assert 'suggested_reduce_fraction' in schema['properties']['position_review']['properties']
    baseline=gemini.PROMPT_TEMPLATE.format(symbol=SYMBOL,timeframes_desc='5분',candle_summary='MFE unknown; 5m bar facts',position_desc='long')
    assert len(prompt)<len(baseline)*.8


def test_flat_core_does_not_request_held_review(tmp_path,monkeypatch):
    requests=provider(monkeypatch)
    result=gemini.analyze(config(tmp_path),SYMBOL,['5m'],'early range break; origin .2541',None)
    assert 'position_review' not in requests[0]['config'].response_schema['properties']
    assert '_held_review' not in result


def test_gpt_held_input_keeps_current_risk_and_removes_new_entry_only_contract(tmp_path,monkeypatch):
    import openai_analyzer as gpt
    cfg=config(tmp_path);cfg.OPENAI_MODEL='test';prompts=[]
    def create(**kwargs):
        prompts.append(kwargs['messages'][0]['content'])
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(dict(action='HOLD',confidence=.9,reasoning='retain'))))],usage=None)
    client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    monkeypatch.setattr(gpt,'_get_client',lambda _:client)
    monkeypatch.setattr(gpt,'_ensure_response_models_warmed_up',lambda:None)
    from adaptive_exit_engine import build_ai_price_contract, format_ai_price_contract
    from adaptive_exit_policy import production_adaptive_exit_policy
    contract=build_ai_price_contract('short',.2541,.002,production_adaptive_exit_policy(),leverage=5,estimated_roundtrip_cost_rate=.001)
    facts='MFE unknown; bar price=.2541; volume=10; pending=unknown'
    out=gpt.verify_position_management(cfg,SYMBOL,['5m'],facts+'\n'+format_ai_price_contract(contract),dict(POSITION),dict(PAYLOAD),dict(REVIEW),protection=dict(PROTECTION))
    assert out['action']=='HOLD'
    assert 'AI_EXIT_EXECUTION_CONTRACT' not in prompts[0]
    for value in ['.2541','.244','MFE unknown','volume=10','pending=unknown','ADD_POSITION','REDUCE_50','CLOSE_ALL','reduce_fraction']:
        assert value in prompts[0]


def test_provider_latency_included_in_reuse_age(tmp_path,monkeypatch):
    cfg=config(tmp_path)
    clock=[1000.]
    monkeypatch.setattr(gemini.time,'time',lambda:clock[0])
    monkeypatch.setattr(gemini,'_get_client',lambda _:object())
    def generate(*a,**k):
        clock[0]+=61
        return SimpleNamespace(text=json.dumps(PAYLOAD),usage_metadata=None)
    monkeypatch.setattr(gemini,'_generate_content_observed',generate)
    decision=gemini.analyze(cfg,SYMBOL,['5m'],'facts',dict(POSITION),protection=dict(PROTECTION))
    import core_ai_context as context
    assert context.reuse(decision,cfg,SYMBOL,['5m'],'facts',dict(POSITION),dict(PROTECTION)) is None
