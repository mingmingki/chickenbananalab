import pytest
from adaptive_exit_policy import production_adaptive_exit_policy
from adaptive_exit_engine import AdaptiveExitContext, AdaptiveExitEngine, compute_cost_break_even_stop, reduction_allowed

P=production_adaptive_exit_policy(); E=AdaptiveExitEngine(P)
def ctx(**kw):
    b=dict(symbol='SOL/USDT:USDT',side='long',entry_price=100.0,current_quantity=1.0,current_stop=None,
      equity_usdt=3000.0,trade_risk_budget_usdt=30.0,atr=1.0,structural_support=97.0,
      structural_resistance=110.0,configured_margin_usdt=500.0,leverage=5.0,order_cap_notional_usdt=5000.0,
      estimated_roundtrip_cost_rate=0.001,decision_timestamp=1,near_resistance=106.0)
    b.update(kw); return AdaptiveExitContext(**b)

def test_target_ladder_has_tp1_tp2_and_runner():
    p=E.plan(ctx())
    assert p.entry_allowed and p.tp1 and p.tp2
    assert p.tp1.price < p.tp2.price
    assert p.tp1.fraction + p.tp2.fraction + p.runner_fraction == pytest.approx(1.0)

def test_low_post_cost_rr_rejects_new_entry_instead_of_forcing_tp():
    p=E.plan(ctx(near_resistance=101.0, estimated_roundtrip_cost_rate=0.004))
    assert p.entry_allowed is False
    assert p.reason_code == 'post_cost_rr_below_minimum'

def test_cost_break_even_stop_includes_roundtrip_cost():
    assert compute_cost_break_even_stop('long',100.0,0.002) > 100.0
    assert compute_cost_break_even_stop('short',100.0,0.002) < 100.0

def test_reduction_guard_blocks_same_evidence_inside_cooldown():
    assert reduction_allowed(last_evidence_id='x', proposed_evidence_id='x', last_action_ts=100, now_ts=200, cooldown_seconds=900) is False
    assert reduction_allowed(last_evidence_id='x', proposed_evidence_id='y', last_action_ts=100, now_ts=200, cooldown_seconds=900) is True
