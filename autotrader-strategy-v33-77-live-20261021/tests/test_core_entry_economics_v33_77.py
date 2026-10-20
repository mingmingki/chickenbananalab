import copy, math
from types import SimpleNamespace
from unittest.mock import Mock
import pandas as pd
import pytest
import core_entry_economics_v33_77 as e

def inputs(side='long'):
    sign=1 if side=='long' else -1
    d=dict(action=side,confidence=.8,exit_plan=dict(stop_loss_price=100-sign*2,take_profit_1_price=100+sign*3,take_profit_2_price=100+sign*5))
    return d,dict(entry_price=100.,estimated_roundtrip_cost_rate=.001)
@pytest.mark.parametrize('side',['long','short'])
def test_net_payoff_is_symmetric_and_includes_both_sides_cost(side):
    d,c=inputs(side); x=e.economics(d,c)
    assert x['status']=='known'
    assert x['base']['cost_r']==pytest.approx(.05)
    assert x['base']['tp1_net_r']==pytest.approx(1.45)
    assert x['base']['tp2_reward_to_loss']==pytest.approx(4.9/2.1)
    assert x['stress_15bps']['tp2_reward_to_loss']<x['base']['tp2_reward_to_loss']
@pytest.mark.parametrize('entry',[None,float('nan'),True,-1])
def test_missing_or_invalid_quote_is_unknown(entry):
    d,c=inputs();c['entry_price']=entry
    assert e.economics(d,c)['status']=='unknown'
def test_wrong_side_stop_is_unknown_and_has_no_order_authority():
    d,c=inputs();d['exit_plan']['stop_loss_price']=101
    x=e.economics(d,c)
    assert x['status']=='unknown' and x['order_authority'] is False
def test_prompt_preserves_price_plan_and_reviews_economics_without_revising():
    d,c=inputs();before=copy.deepcopy(d)
    p=e.prompt(symbol='BTC',frames=['5m','15m','1h','4h'],position='none',decision=d,summary='CLOSED_BARS',short_context='',contract=c)
    assert 'CLOSED_BARS' in p and 'tp2_reward_to_loss' in p
    assert 'confidence' in p and 'approve_now|wait|reject' in p
    assert 'SL/TP 가격이나 수량을 변경하지' in p
    assert d==before
def test_15m_aggregation_uses_only_three_contiguous_closed_5m_bars():
    t=pd.date_range('2026-10-01',periods=111,freq='5min')
    f=pd.DataFrame(dict(timestamp=t,open=100.,high=102.,low=99.,close=101.,volume=1.))
    now=t[-1]+pd.Timedelta(minutes=2)
    x=e.structure('long',{'5m':f},now=now.to_pydatetime())
    assert x['frames']['15m']['fresh'] is True
    assert x['frames']['15m']['rows'][-1]['ts']=='2026-10-01T08:45:00+00:00'
    changed=f.copy();changed.loc[110,'close']=1e6
    assert e.structure('long',{'5m':changed},now=now.to_pydatetime())==x
def test_core_gate_forwards_economic_contract_once(monkeypatch):
    import trader
    cfg=SimpleNamespace(logger=Mock(),CORE_AI_STRATEGY_AUTHORITY=True)
    d,c=inputs();seen=[]
    def verify(*a,**k):
        seen.append(k);return dict(decision='approve_now',confidence=.8)
    monkeypatch.setattr(trader.openai_analyzer,'verify',verify)
    allowed,_,_=trader._gpt_entry_gate(cfg,'BTC/USDT:USDT',['5m'],'',None,d,exit_price_contract=c)
    assert allowed and len(seen)==1
    assert seen[0]['exit_price_contract']==c

def test_entry_structure_replaces_previous_evidence_to_avoid_duplicate_tokens():
    a=e.with_structure('raw',{'side':'long'})
    b=e.with_structure(a,{'side':'short'})
    assert b.count('[ENTRY_STRUCTURE_V33_77]')==1
    assert 'long' not in b and 'short' in b

def test_entry_structure_rejects_duplicate_5m_source_timestamps():
    t=pd.date_range('2026-10-01',periods=111,freq='5min')
    f=pd.DataFrame(dict(timestamp=t,open=100.,high=102.,low=99.,close=101.,volume=1.))
    f.loc[109,'timestamp']=f.loc[108,'timestamp']
    x=e.structure('long',{'5m':f},now=(t[-1]+pd.Timedelta(minutes=2)).to_pydatetime())
    assert x['frames']['15m']['fresh'] is False

def test_confirmed_market_summary_ignores_unfinished_upper_bar():
    t=pd.date_range('2026-10-01',periods=70,freq='1h')
    f=pd.DataFrame(dict(timestamp=t,open=100.,high=102.,low=99.,close=101.,volume=1.))
    now=(t[-1]+pd.Timedelta(minutes=20)).to_pydatetime()
    before=e.market_summary({'1h':f},now=now)
    f.loc[69,['high','close']]=[1000000.,1000000.]
    assert e.market_summary({'1h':f},now=now)==before
    assert 'CONFIRMED' in before

def test_replay_decision_cutoff_precedes_fill_and_has_explicit_timezone():
    from replay_entry_profitability_v33_77 import decision_at
    assert decision_at('BTC/USDT:USDT-20261009023148493184') == int(pd.Timestamp('2026-10-08T17:31:48.493184Z').timestamp()*1000)
    assert decision_at('legacy-entry-without-timestamp') is None

def test_low_latency_reasoning_is_scoped_to_supported_models():
    assert e.request_options('gpt-5-mini')=={'reasoning_effort':'low'}
    assert e.request_options('gpt-4.1-mini')=={}

def test_confirmed_price_levels_are_available_to_both_analysts():
    t=pd.date_range('2026-10-01',periods=200,freq='1h')
    values=[100+math.sin(i/3) for i in range(200)]
    f=pd.DataFrame(dict(timestamp=t,open=values,high=[x+.2 for x in values],low=[x-.2 for x in values],close=values,volume=1.))
    text=e.market_summary({'1h':f},now=(t[-1]+pd.Timedelta(minutes=20)).to_pydatetime())
    assert '[CONFIRMED_PRICE_STRUCTURE]' in text
    assert 'swing_high' in text and 'swing_low' in text
