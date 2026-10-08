from adaptive_exit_engine import AdaptiveExitContext, AdaptiveExitEngine, LearningExitSuggestion
from adaptive_exit_learning import suggest_overlay
from adaptive_exit_policy import production_adaptive_exit_policy


def fixture_ctx(learning):
    return AdaptiveExitContext(
        symbol="BTC/USDT:USDT", side="long", entry_price=100.0,
        current_quantity=1.0, current_stop=None, equity_usdt=1000.0,
        trade_risk_budget_usdt=20.0, atr=2.0, structural_support=95.0,
        near_resistance=112.0, continuation_resistance=120.0,
        order_cap_notional=1000.0, leverage=5.0, learning=learning,
    )


def test_shadow_learning_cannot_change_live_plan():
    suggestion = LearningExitSuggestion(authority="SHADOW", initial_atr_delta=-0.4, evidence_ids=("e1",))
    plan = AdaptiveExitEngine(production_adaptive_exit_policy()).plan(fixture_ctx(suggestion))
    assert plan.live_parameters == plan.base_parameters
    assert plan.shadow_parameters != plan.base_parameters


def test_validated_still_does_not_auto_become_live_bounded():
    suggestion = LearningExitSuggestion(authority="VALIDATED", initial_atr_delta=-0.3, evidence_ids=("e1",))
    plan = AdaptiveExitEngine(production_adaptive_exit_policy()).plan(fixture_ctx(suggestion))
    assert plan.live_parameters == plan.base_parameters
    assert plan.shadow_parameters != plan.base_parameters


def test_suggestion_requires_sample_oos_and_adjacent_stability_for_validation():
    prior = production_adaptive_exit_policy()
    few = [{"mae_r":0.5,"mfe_r":2.0,"validation":{"oos_improvement":True,"adjacent_stable":True}} for _ in range(5)]
    assert suggest_overlay(few, prior, min_samples=10).authority == "SHADOW"

    enough_but_unstable = [{"mae_r":0.5,"mfe_r":2.0,"validation":{"oos_improvement":True,"adjacent_stable":False}} for _ in range(12)]
    assert suggest_overlay(enough_but_unstable, prior, min_samples=10).authority == "SHADOW"

    stable = [{"mae_r":0.5,"mfe_r":2.0,"validation":{"oos_improvement":True,"adjacent_stable":True}} for _ in range(12)]
    suggestion = suggest_overlay(stable, prior, min_samples=10)
    assert suggestion.authority == "VALIDATED"
    assert suggestion.authority != "LIVE_BOUNDED"
