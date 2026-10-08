import pytest
from adaptive_exit_policy import production_adaptive_exit_policy
from adaptive_exit_engine import AdaptiveExitContext, AdaptiveExitEngine, compute_structural_stop, solve_risk_capped_size

P = production_adaptive_exit_policy()
E = AdaptiveExitEngine(P)

def ctx(**kw):
    base = dict(symbol='DOGE/USDT:USDT', side='long', entry_price=100.0, current_quantity=0.0,
        current_stop=None, equity_usdt=3000.0, trade_risk_budget_usdt=30.0, atr=2.0,
        structural_support=90.0, structural_resistance=120.0, configured_margin_usdt=500.0,
        leverage=5.0, order_cap_notional_usdt=5000.0, estimated_roundtrip_cost_rate=0.001,
        decision_timestamp=1)
    base.update(kw); return AdaptiveExitContext(**base)

def test_fixed_margin_wide_stop_shrinks_notional_to_risk_budget():
    p = E.plan(ctx())
    assert p.entry_allowed
    assert p.configured_notional == pytest.approx(2500.0)
    assert p.planned_loss_usdt <= 30.0 + 1e-9
    assert p.effective_notional < 2500.0

def test_structural_stop_long_short_symmetry():
    long = compute_structural_stop(ctx(side='long', structural_support=90.0), P)
    short = compute_structural_stop(ctx(side='short', structural_support=None, structural_resistance=110.0), P)
    assert long.stop_price < 100.0 < short.stop_price
    assert abs(100-long.stop_price) == pytest.approx(abs(short.stop_price-100))

def test_missing_structure_or_atr_fails_closed_for_new_entry():
    assert E.plan(ctx(atr=None)).entry_allowed is False
    assert E.plan(ctx(structural_support=None)).entry_allowed is False

def test_exchange_minimum_that_exceeds_risk_budget_is_rejected():
    s = solve_risk_capped_size(ctx(trade_risk_budget_usdt=1.0), 90.0, 2500.0, minimum_notional=50.0)
    assert s.entry_allowed is False
    assert s.reason_code == 'exchange_minimum_exceeds_risk_budget'

def test_estimated_cost_is_included_in_planned_loss():
    p = E.plan(ctx(estimated_roundtrip_cost_rate=0.01))
    distance = abs(100.0-p.stop_price)/100.0
    assert p.planned_loss_usdt == pytest.approx(p.effective_notional*(distance+0.01))
