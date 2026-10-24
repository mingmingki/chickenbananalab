"""Regression for CORE Gemini->GPT gating with cost-aware bounded SL/TP."""
import math
from dataclasses import replace
from types import SimpleNamespace

import pytest
import core_entry_policy
import trader
from adaptive_exit_engine import AdaptiveExitContext, AdaptiveExitEngine
from adaptive_exit_policy import production_adaptive_exit_policy, policy_sha256
from core_entry_sltp_repair import reconcile_core_plan


def case(side='long', price=0.2531, structural=0.2215167530809656, atr=.00753):
    policy=production_adaptive_exit_policy()
    ctx=AdaptiveExitContext(
        symbol='ADA/USDT:USDT', side=side, entry_price=price, current_quantity=3950,
        current_stop=None, equity_usdt=1770,
        trade_risk_budget_usdt=88.53, atr=atr, configured_margin_usdt=200,
        leverage=5, order_cap_notional=1000,
        estimated_roundtrip_cost_rate=.001,
        structural_support=structural if side=='long' else None,
        structural_resistance=structural if side=='short' else None,
        execution_target='tp2', mode='LIVE_BOUNDED',
        allow_wide_stop_with_risk_sizing=True)
    return policy,ctx,AdaptiveExitEngine(policy).plan(ctx)


@pytest.mark.parametrize('side,price,structure,atr',[
    ('long',.2531,.2215167530809656,.00753),
    ('long',.2516,.2215167530809656,.0075),
    ('short',.2499,.2762654568664815,.0075),
])
def test_ada_gemini_candidate_gets_protected_plan_before_gpt(side,price,structure,atr):
    policy,ctx,base=case(side,price,structure,atr)
    assert not base.entry_allowed and base.reason_code=='post_cost_rr_below_minimum'
    updated,reason=reconcile_core_plan(base,ctx,policy)
    assert updated.entry_allowed and reason.startswith('core_pre_gpt_')
    assert 0 < abs(updated.stop_price-price)/price * ctx.leverage*100 <= 20
    assert updated.effective_notional <= 1000
    assert updated.planned_loss_usdt <= ctx.trade_risk_budget_usdt
    tp2=updated.tp2.price
    rr=(abs(tp2-price)-price*.001)/(abs(updated.stop_price-price)+price*.001)
    assert rr>=policy['min_post_cost_rr']


def test_invalid_or_too_close_target_remains_blocked():
    policy,ctx,base=case()
    tiny=replace(base,tp1=replace(base.tp1,price=ctx.entry_price*1.001),
                       tp2=replace(base.tp2,price=ctx.entry_price*1.002))
    result,reason=reconcile_core_plan(tiny,ctx,policy)
    assert not result.entry_allowed
    assert reason in {'unrepresentable_rr_or_stop_cap','stop_too_tight_for_market'}


def test_gpt_wait_and_reject_still_block_and_typed_timeout_only():
    cfg=SimpleNamespace(MIN_CONFIDENCE=.6, CORE_GPT_ENTRY_TIMEOUT_BYPASS=True)
    decision={'action':'long','confidence':.76}
    for verdict in ('wait','reject'):
        allow,_,_=core_entry_policy.evaluate(cfg,'ADA/USDT:USDT',decision,
                                            {'decision':verdict,'confidence':.8})
        assert allow is False
    allowed,gate,_=core_entry_policy.evaluate(cfg,'ADA/USDT:USDT',decision,
        dict(decision='TIMEOUT',error_reason='timeout',error_type='APITimeoutError',
             timeout_confirmed=True,request_purpose='entry_gate'))
    assert allowed and gate=='TIMEOUT_BYPASS'
    allowed,_,_=core_entry_policy.evaluate(cfg,'ADA/USDT:USDT',decision,
        dict(decision='TIMEOUT',error_reason='timeout',error_type='NetworkError',
             timeout_confirmed=False,request_purpose='entry_gate'))
    assert not allowed


def test_production_wiring_repairs_ada_pre_gpt(tmp_path):
    policy=production_adaptive_exit_policy()
    cfg=SimpleNamespace(
        ADAPTIVE_EXIT_MODE='LIVE_BOUNDED',
        ADAPTIVE_EXIT_APPROVED_POLICY_HASH=policy_sha256(policy),
        RISK_PER_TRADE_PCT=1,LEVERAGE=5, POSITION_SIZE_MODE='FIXED',
        POSITION_FIXED_USDT=200.,CORE_ORDER_MODE='FIXED_MARGIN_AUTO_EXIT',
        MAX_DAILY_LOSS_PCT=5,SPREAD_BPS=0,SLIPPAGE_BPS=0,
        user_dir=str(tmp_path),logger=SimpleNamespace(warning=lambda *a,**k:None))
    features=dict(atr=.00753,structural_support=.2215167530809656,
                  structural_resistance=.276265,source_timestamps=(1000,),
                  input_snapshot_hash='ada-real-replay')
    out=trader._core_adaptive_live_entry_decision(
        cfg,symbol='ADA/USDT:USDT',legacy_order_args=('long',3950,.248,.263),
        entry_price=.2531,equity=1770,market_features=features)
    assert out['active'] and not out['blocked']
    assert out['plan'].entry_allowed and out['context'].execution_target=='tp2'
    assert out['reason']=='core_pre_gpt_stop_bounded'
    _,amount,stop,target=out['order_args']
    assert amount <= 3950 and stop < .2531 < target
