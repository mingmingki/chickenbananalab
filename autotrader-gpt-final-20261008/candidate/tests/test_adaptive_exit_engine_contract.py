import dataclasses, pytest
from adaptive_exit_policy import production_adaptive_exit_policy
from adaptive_exit_engine import AdaptiveExitContext, AdaptiveExitEngine

def make_ctx():
    return AdaptiveExitContext(symbol='BTC/USDT:USDT', side='long', entry_price=100.0,
        current_quantity=1.0, current_stop=None, equity_usdt=1000.0,
        trade_risk_budget_usdt=10.0, atr=2.0, structural_support=94.0,
        structural_resistance=110.0, configured_margin_usdt=100.0, leverage=5.0,
        order_cap_notional_usdt=1000.0, estimated_roundtrip_cost_rate=0.001,
        decision_timestamp=1234567890)

def test_context_and_plan_are_frozen():
    ctx = make_ctx()
    with pytest.raises(dataclasses.FrozenInstanceError): ctx.entry_price = 101.0
    plan = AdaptiveExitEngine(production_adaptive_exit_policy()).plan(ctx)
    with pytest.raises(dataclasses.FrozenInstanceError): plan.entry_allowed = False

def test_same_context_and_policy_reproduce_same_plan_hash():
    e = AdaptiveExitEngine(production_adaptive_exit_policy()); ctx = make_ctx()
    assert e.plan(ctx).plan_hash == e.plan(ctx).plan_hash
