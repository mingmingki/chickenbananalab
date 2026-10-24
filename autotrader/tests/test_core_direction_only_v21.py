"""CORE v21: GPT direction only, Gemini pricing independently protected."""
import json
import logging
from types import SimpleNamespace
import pytest
import adaptive_exit_engine as ae
import openai_analyzer
import strategy_authority as sa
import trader
from adaptive_exit_policy import production_adaptive_exit_policy


def plan(sl=96.0,tp1=106.0,tp2=112.0):
    return {'stop_loss_price':sl,'take_profit_1_price':tp1,
            'take_profit_2_price':tp2,'confidence':0.7,'reasoning':'Gemini prices'}


@pytest.mark.parametrize('opinion', ['approve','revise','reject','not_applicable',None])
def test_price_opinion_does_not_block_gpt_direction(opinion):
    gemini={'exit_plan':plan()}
    got,source=sa.select_core_prices(
        gemini,{'decision':'approve_now','exit_plan_decision':opinion,'exit_plan':plan(95,108,115)},
        'approved')
    assert got['stop_loss_price']==96.0
    assert source == 'gemini_direction_approved'


@pytest.mark.parametrize('gate', ['blocked_reject','blocked_wait','blocked_error'])
def test_gpt_direction_denial_prevents_entry(gate):
    got,reason=sa.select_core_prices({'exit_plan':plan()}, {'decision':'reject'},gate)
    assert got is None
    assert reason == 'core_gpt_direction_not_approved'


def test_missing_gemini_plan_cannot_be_recovered_from_gpt():
    got,reason=sa.select_core_prices(
        {'exit_plan':None}, {'decision':'approve_now','exit_plan':plan()},
        'approved')
    assert got is None and reason == 'core_gemini_exit_plan_missing_or_invalid'


def test_local_price_contract_still_rejects_invalid_gemini_prices():
    contract=ae.build_ai_price_contract('long',100.0,2.0,production_adaptive_exit_policy(),
                                       leverage=5,estimated_roundtrip_cost_rate=.001)
    assert contract is not None
    assert ae.validate_ai_price_plan_contract(plan(96,106,112),contract)=='ok'
    assert ae.validate_ai_price_plan_contract(plan(101,106,112),contract)!='ok'


def test_direction_prompt_never_transmits_gemini_prices_or_contract(monkeypatch,tmp_path):
    observed={}
    class FakeClient:
        def with_options(self,**kwargs): return self
        chat=None
        def create(self,**kwargs):
            observed['messages']=kwargs.get('messages')
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
                content=json.dumps({'decision':'approve_now','confidence':.88,
                     'exit_plan_decision':'reject','exit_plan':plan()})))],usage=None)
    client=FakeClient()
    client.chat=SimpleNamespace(completions=SimpleNamespace(create=client.create))
    monkeypatch.setattr(openai_analyzer,'_get_client',lambda cfg:client)
    monkeypatch.setattr(openai_analyzer,'_ensure_response_models_warmed_up',lambda:None)
    monkeypatch.setattr(openai_analyzer.gpt_latency_log,'record_call',lambda *args,**kwargs:None)
    cfg=SimpleNamespace(OPENAI_API_KEY='fake',OPENAI_MODEL='fake',user_dir=str(tmp_path),
                        logger=logging.getLogger('direction-v21'))
    result=openai_analyzer.verify(
        cfg,'BTC/USDT:USDT',['5m'],'market evidence',None,
        {'action':'long','confidence':.75,'exit_plan':plan(91,130,150),
         'reasoning':'fresh directional breakout'},
        timeout=10,purpose='entry_gate',max_retries=0,
        exit_price_contract={'entry_price':100.,'secret_sentinel':'UNSENTINELPRICE'})
    assert result['decision']=='approve_now'
    assert result['exit_plan'] is None
    assert result['exit_plan_decision']=='not_applicable'
    assert observed['messages']
    messages=json.dumps(observed['messages'],ensure_ascii=False)
    assert 'UNSENTINELPRICE' not in messages
    assert '150' not in messages
    assert 'stop_loss_price' not in messages
    assert 'exit_plan_decision' not in messages


@pytest.mark.parametrize('mode', ['AUTO_ALL','FIXED_MARGIN_AUTO_EXIT'])
def test_core_risk_context_budget_is_one_percent(mode):
    # Both branches calculate 1% loss budget, independent of margin ceiling.
    import inspect
    src=inspect.getsource(trader._core_adaptive_live_entry_decision)
    assert "float(equity)*float(getattr(cfg,'RISK_PER_TRADE_PCT',1.0))/100.0" in src
    assert "risk_budget=min(configured_notional, daily_cap," in src
