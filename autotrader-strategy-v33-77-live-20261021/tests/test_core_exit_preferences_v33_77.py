import copy
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
import core_management_context_v33_77 as policy

def sample(side='long', current=-.2, peak=.1):
    sign=1 if side=='long' else -1
    mark=100+sign*current*2
    rows=[dict(close=mark-sign*x,ema_20=mark-sign*.8,macd=sign*m,atr_14=2,ts='unused') for x,m in [(.4,.2),(.2,.3),(0,.4)]]
    ctx=dict(complete=True,regime='trend_intact',frames={tf:dict(state='aligned',fresh=True,rows=copy.deepcopy(rows)) for tf in policy.TF_SECONDS})
    position=dict(side=side,entry_price=100.,mark_price=100+sign*current*2,contracts=1.)
    evidence=dict(mfe_known=True,initial_r=2.,mfe_r=peak,current_r=current)
    protection=dict(algo_id='existing-stop',sz=1.,sl_price=100-sign*2)
    return ctx,evidence,position,protection

@pytest.mark.parametrize('side',['long','short'])
def test_recovered_upper_trend_defers_loss_churn_and_full_exit(side):
    ctx,evidence,position,protection=sample(side)
    result=policy.exit_preferences(ctx,evidence,position,protection)
    assert result['defer_full_exit'] is True
    assert result['defer_loss_reduction'] is True
    assert result['order_authority'] is False

@pytest.mark.parametrize('side',['long','short'])
def test_recovered_profitable_trend_keeps_partial_profit_available(side):
    ctx,evidence,position,protection=sample(side,current=.3,peak=.35)
    result=policy.exit_preferences(ctx,evidence,position,protection)
    assert result['defer_full_exit'] is True
    assert result['defer_loss_reduction'] is False

@pytest.mark.parametrize('case',['invalidation','conflict','no_recovery','giveback','unknown_peak','incomplete','emergency','shock','missing_protection','wrong_protection_qty'])
@pytest.mark.parametrize('side',['long','short'])
def test_adverse_or_unknown_evidence_retains_independent_exit_review(case,side):
    ctx,evidence,position,protection=sample(side)
    sign=1 if side=='long' else -1
    if case=='invalidation':ctx['frames']['1h']['state']='reversed'
    if case=='conflict':ctx['frames']['15m']['state']='reversed'
    if case=='no_recovery':ctx['frames']['5m']['rows'][-1]['macd']=sign*.1
    if case=='giveback':evidence['mfe_r']=.5
    if case=='unknown_peak':evidence['mfe_known']=False
    if case=='incomplete':ctx['complete']=False
    if case=='emergency':position['mark_price']=100-sign*2*.91
    if case=='missing_protection':protection=None
    if case=='wrong_protection_qty':protection['sz']=.5
    if case=='shock':
        ctx['frames']['5m']['rows'][-1]['close']=ctx['frames']['5m']['rows'][-2]['close']-sign*3
    result=policy.exit_preferences(ctx,evidence,position,protection)
    assert result['defer_full_exit'] is False
    assert result['defer_loss_reduction'] is False

def test_policy_uses_current_quote_not_stale_current_r():
    ctx,evidence,position,protection=sample(current=.3,peak=.35)
    evidence['current_r']=-100
    assert policy.exit_preferences(ctx,evidence,position,protection)['defer_full_exit'] is True

def test_nonfinite_inputs_never_create_a_defer_permission():
    ctx,evidence,position,protection=sample()
    position['mark_price']=float('nan')
    assert policy.exit_preferences(ctx,evidence,position,protection)['defer_full_exit'] is False

def test_explicit_gemini_role_does_not_claim_final_exit_authority():
    import gemini_analyzer
    assert 'Gemini가 최종 판단' not in gemini_analyzer.CORE_MANAGEMENT_ACTION_INSTRUCTIONS
    assert 'GPT' in gemini_analyzer.CORE_MANAGEMENT_ACTION_INSTRUCTIONS

def test_healthy_full_exit_is_deferred_before_paid_verification(monkeypatch,tmp_path):
    import trader
    ctx,evidence,position,protection=sample()
    cfg=SimpleNamespace(user_dir=str(tmp_path),MIN_CONFIDENCE=.6,logger=Mock())
    monkeypatch.setattr(trader,'_position_management_evidence_data',lambda *a:evidence,raising=False)
    monkeypatch.setattr(trader,'_position_management_evidence',lambda *a:'observed evidence')
    import core_entry_economics_v33_77
    monkeypatch.setattr(core_entry_economics_v33_77,'structure',lambda *a,**kw:ctx)
    paid=Mock(return_value=dict(action='CLOSE_ALL',confidence=.9,reasoning='short term weak'))
    monkeypatch.setattr(trader.openai_analyzer,'verify_position_management',paid)
    result=trader._verify_core_exit_proposal(cfg,None,'BTC/USDT:USDT',list(policy.TF_SECONDS),'snapshot',position,{'_candle_asof':'2026-10-20T00:00:00+00:00'}, {'assessment':'weakening'},protection,dict(action='CLOSE_ALL',confidence=.9,reasoning='5m weak'),{},'general')
    assert result['action']=='HOLD'
    assert result.get('gpt_verified') is False
    paid.assert_not_called()

def test_invalidated_full_exit_still_requires_gpt(monkeypatch,tmp_path):
    import trader
    ctx,evidence,position,protection=sample()
    ctx['frames']['1h']['state']='reversed'
    cfg=SimpleNamespace(user_dir=str(tmp_path),MIN_CONFIDENCE=.6,logger=Mock())
    monkeypatch.setattr(trader,'_position_management_evidence_data',lambda *a:evidence,raising=False)
    monkeypatch.setattr(trader,'_position_management_evidence',lambda *a:'observed evidence')
    import core_entry_economics_v33_77
    monkeypatch.setattr(core_entry_economics_v33_77,'structure',lambda *a,**kw:ctx)
    paid=Mock(return_value=dict(action='CLOSE_ALL',confidence=.85,reasoning='upper invalidated'))
    monkeypatch.setattr(trader.openai_analyzer,'verify_position_management',paid)
    result=trader._verify_core_exit_proposal(cfg,None,'BTC/USDT:USDT',list(policy.TF_SECONDS),'snapshot',position,{'_candle_asof':'2026-10-20T00:00:00+00:00'}, {'assessment':'weakening'},protection,dict(action='CLOSE_ALL',confidence=.9,reasoning='upper invalidated'),{},'general')
    assert result['action']=='CLOSE_ALL' and result['gpt_verified'] is True
    paid.assert_called_once()

@pytest.mark.parametrize('assessment',['invalidated',None])
def test_explicit_invalidation_or_unknown_review_cannot_be_vetoed_by_ema_recovery(monkeypatch,tmp_path,assessment):
    import trader
    import core_entry_economics_v33_77
    ctx,evidence,position,protection=sample()
    cfg=SimpleNamespace(user_dir=str(tmp_path),MIN_CONFIDENCE=.6,logger=Mock())
    monkeypatch.setattr(trader,'_position_management_evidence_data',lambda *a:evidence,raising=False)
    monkeypatch.setattr(trader,'_position_management_evidence',lambda *a:'observed evidence')
    monkeypatch.setattr(core_entry_economics_v33_77,'structure',lambda *a,**kw:ctx)
    paid=Mock(return_value=dict(action='CLOSE_ALL',confidence=.85,reasoning='concrete thesis invalidation'))
    monkeypatch.setattr(trader.openai_analyzer,'verify_position_management',paid)
    review={} if assessment is None else dict(assessment=assessment)
    result=trader._verify_core_exit_proposal(cfg,None,'BTC/USDT:USDT',list(policy.TF_SECONDS),'snapshot',position,
        {'_candle_asof':'2026-10-20T00:00:00+00:00'},review,protection,
        dict(action='CLOSE_ALL',confidence=.9,reasoning='independent thesis review'),{},'general')
    assert result['action']=='CLOSE_ALL' and result['gpt_verified'] is True
    paid.assert_called_once()

@pytest.mark.parametrize('side',['long','short'])
def test_current_quote_shock_retains_review_after_a_closed_bar_recovery(side):
    ctx,evidence,position,protection=sample(side,current=.1,peak=.12)
    sign=1 if side=='long' else -1
    evidence['initial_r']=10.
    position['mark_price']=ctx['frames']['5m']['rows'][-1]['close']-sign*2.3
    protection['sl_price']=100-sign*10
    result=policy.exit_preferences(ctx,evidence,position,protection)
    assert -.9<result['current_r']<0
    assert result['defer_full_exit'] is False
    assert result['fast_adverse_move'] is True
