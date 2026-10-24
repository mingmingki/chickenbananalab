import json
import datetime
from pathlib import Path
import core_exit_dual_audit_v33_24 as a

POSITION=dict(side='short',entry_price=100.,mark_price=102.,
              entry_timestamp_ms=123)
RISK=dict(valid=True,side='short',identity='123',closed_5m_ts='2026-10-17T01:00:00',
          signal=True,loss_r=.4)
def profit(mfe=.5,current=.2,give=.3):
    return dict(entry_timestamp_ms=123,side='short',entry_price=100.,
                mfe_r=mfe,current_r=current,giveback_r=give,
                policies={a.POLICY:dict(live_resolved=True)})

def test_profit_priority_no_double_execution():
    result=a.assess(POSITION,RISK,profit())
    assert result['classification']=='PROFIT_GIVEBACK_POLICY_ALREADY_ELIGIBLE'
    assert result['existing_live_policy_resolved'] is True
    assert result['may_execute'] is False

def test_early_profit():
    assert a.assess(POSITION,RISK,profit(.3,.1,.2))['classification']=='EARLY_PROFIT_REVIEW_ELIGIBLE'

def test_profit_to_loss():
    assert a.assess(POSITION,RISK,profit(.3,-.05,.35))['classification']=='PROFIT_TO_LOSS_REVERSION'

def test_adverse_structure_without_profit():
    r=a.assess(POSITION,RISK,profit(.1,-.4,.5))
    assert r['classification']=='ADVERSE_STRUCTURE_RISK'
    assert not r['profit_policy_armed']

def test_mismatched_lifecycle_prevents_profit_collision():
    state=profit();state['entry_timestamp_ms']=999
    r=a.assess(POSITION,RISK,state)
    assert r['classification']=='LOSS_CONTEXT_ONLY'
    assert r['reason']=='mfe_lifecycle_mismatch'

def test_missing_context_is_classified_not_decided():
    assert a.assess(POSITION,RISK,None)['classification']=='LOSS_CONTEXT_ONLY'
    assert a.assess(POSITION,dict(RISK,valid=False),profit())['classification']=='UNKNOWN'

def test_stores_once_never_authorizes_trade(tmp_path):
    r=a.assess(POSITION,RISK,profit())
    assert a.record(tmp_path,'ETH/USDT:USDT',r) is True
    assert a.record(tmp_path,'ETH/USDT:USDT',r) is False
    saved=json.loads((tmp_path/'core_exit_dual_audit_v33_24.jsonl').read_text().strip())
    assert saved['mode']=='SHADOW_ONLY'
    assert saved['may_execute'] is False

def test_hook_preserves_existing_risk_guards():
    t=Path('trader.py').read_text()
    hook=t.split('# v33-24: MFE/profit vs structural-loss lifecycle audit',1)[1].split(
          '    if position is not None and _maybe_negative_guard_review(',1)[0]
    assert 'core_exit_dual_audit_v33_24.assess' in hook
    assert 'core_exit_dual_audit_v33_24.record' in hook
    assert 'exit_shadow_v33_24.assess' in hook
    for forbidden in ('_execute_close(', '_execute_position_ai_reduce_50(', 'cancel_order(', 'create_order('):
        assert forbidden not in hook


def test_elevated_loss_distance_without_structural_confirmation():
    risk=dict(RISK,signal=False,loss_r=.58)
    result=a.assess(POSITION,risk,profit(.15,-.58,.73))
    assert result['classification']=='LOSS_DISTANCE_ELEVATED_UNCONFIRMED_5M'
    assert result['may_execute'] is False
