import pytest
from adaptive_exit_policy import production_adaptive_exit_policy
from adaptive_exit_engine import AdaptiveExitContext, AdaptiveExitEngine, apply_gemini_overlay
from gemini_analyzer import parse_adaptive_exit_assessment

P=production_adaptive_exit_policy()
def ctx():
    return AdaptiveExitContext(symbol='BTC/USDT:USDT',side='long',entry_price=100,current_quantity=1,current_stop=None,
      equity_usdt=3000,trade_risk_budget_usdt=30,atr=1,structural_support=97,structural_resistance=110,
      configured_margin_usdt=500,leverage=5,order_cap_notional_usdt=5000,estimated_roundtrip_cost_rate=.001,
      decision_timestamp=1,near_resistance=106)

def test_gemini_cannot_return_prices_or_order_authority():
    raw={'thesis_state':'weakening','confidence':.9,'trend_persistence':'low','volatility_risk':'high',
         'target_extension':'deny','reasoning':'x','stop_price':88.0}
    assert parse_adaptive_exit_assessment(raw) is None

def test_backward_compatible_assessment_field_maps_to_thesis_state():
    a=parse_adaptive_exit_assessment({'assessment':'weakening','confidence':.8})
    assert a.thesis_state == 'weakening' and a.confidence == pytest.approx(.8)

def test_malformed_or_none_confidence_is_safe():
    assert parse_adaptive_exit_assessment({'assessment':'oops','confidence':.8}) is None
    a=parse_adaptive_exit_assessment({'assessment':'thesis_intact','confidence':None})
    assert a.thesis_state == 'intact' and a.confidence is None

def test_overlay_never_increases_risk_or_notional():
    base=AdaptiveExitEngine(P).plan(ctx())
    a=parse_adaptive_exit_assessment({'thesis_state':'weakening','confidence':.9,'volatility_risk':'high','target_extension':'deny'})
    adjusted=apply_gemini_overlay(base,a,P)
    assert adjusted.trade_risk_budget_usdt <= base.trade_risk_budget_usdt
    assert adjusted.effective_notional <= base.effective_notional
    assert adjusted.tp2 is None

def test_no_assessment_is_byte_equivalent_plan():
    base=AdaptiveExitEngine(P).plan(ctx())
    assert apply_gemini_overlay(base,None,P) == base

def test_valid_json_string_is_parsed_but_price_authority_is_still_rejected():
    import json
    valid=json.dumps({"thesis_state":"intact","confidence":0.7,"trend_persistence":"high","volatility_risk":"low","target_extension":"allow"})
    parsed=parse_adaptive_exit_assessment(valid)
    assert parsed is not None and parsed.thesis_state == "intact"
    forbidden=json.dumps({"thesis_state":"intact","confidence":0.7,"stop_price":88.0})
    assert parse_adaptive_exit_assessment(forbidden) is None
