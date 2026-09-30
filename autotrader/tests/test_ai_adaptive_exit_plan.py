from pathlib import Path

import adaptive_exit_engine as ae
from adaptive_exit_policy import production_adaptive_exit_policy
import gemini_analyzer
import openai_analyzer


def _ctx(side="long", risk_budget=20.0):
    kw = dict(
        symbol="X/USDT:USDT", side=side, entry_price=100.0, current_quantity=10.0,
        current_stop=None, equity_usdt=1000.0, trade_risk_budget_usdt=risk_budget,
        atr=2.0, configured_margin_usdt=200.0, leverage=5.0,
        order_cap_notional=1000.0, mode="LIVE_BOUNDED",
    )
    if side == "long":
        kw.update(structural_support=92.0, near_resistance=110.0)
    else:
        kw.update(structural_resistance=108.0, near_support=90.0)
    return ae.AdaptiveExitContext(**kw)


def test_normalize_ai_price_plan_accepts_valid_prices():
    plan = ae.normalize_ai_price_plan({
        "stop_loss_price": 96, "take_profit_1_price": 106,
        "take_profit_2_price": 112, "confidence": 0.82,
        "reasoning": "structure invalidates below 96",
    })
    assert plan["stop_loss_price"] == 96.0
    assert plan["take_profit_1_price"] == 106.0
    assert plan["take_profit_2_price"] == 112.0
    assert plan["confidence"] == 0.82


def test_verified_ai_price_plan_approve_uses_gemini_and_revise_uses_gpt():
    gemini = {"exit_plan": {
        "stop_loss_price": 96, "take_profit_1_price": 106,
        "take_profit_2_price": 112, "confidence": 0.8, "reasoning": "gemini",
    }}
    selected, source = ae.select_verified_ai_price_plan(
        gemini, {"exit_plan_decision": "approve"})
    assert source == "gemini_approved"
    assert selected["stop_loss_price"] == 96.0
    revised = {
        "exit_plan_decision": "revise",
        "exit_plan": {"stop_loss_price": 95, "take_profit_1_price": 107,
                      "take_profit_2_price": 114, "confidence": 0.86,
                      "reasoning": "gpt revision"},
    }
    selected, source = ae.select_verified_ai_price_plan(gemini, revised)
    assert source == "gpt_revised"
    assert selected["stop_loss_price"] == 95.0


def test_rejected_or_malformed_ai_price_plan_falls_back():
    gemini = {"exit_plan": {"stop_loss_price": 96,
              "take_profit_1_price": 106, "take_profit_2_price": 112}}
    assert ae.select_verified_ai_price_plan(
        gemini, {"exit_plan_decision": "reject"}) == (None, "gpt_rejected")
    assert ae.normalize_ai_price_plan(
        {"stop_loss_price": "nan", "take_profit_1_price": 106}) is None


def test_apply_ai_price_plan_long_replaces_prices_and_recomputes_risk_size():
    policy = production_adaptive_exit_policy()
    ctx = _ctx("long", risk_budget=20.0)
    base = ae.AdaptiveExitEngine(policy).plan(ctx)
    plan, reason = ae.apply_ai_price_plan(base, ctx, {
        "stop_loss_price": 96, "take_profit_1_price": 106,
        "take_profit_2_price": 112, "confidence": 0.8,
        "reasoning": "AI target",
    }, policy)
    assert reason == "ai_exit_plan_applied"
    assert plan.reason_code == "ai_exit_plan"
    assert plan.stop_price == 96.0
    assert plan.tp1.price == 106.0
    assert plan.tp2.price == 112.0
    assert 499.0 < plan.effective_notional < 501.0
    assert plan.plan_hash != base.plan_hash


def test_apply_ai_price_plan_rejects_unsafe_geometry_and_keeps_baseline():
    policy = production_adaptive_exit_policy()
    ctx = _ctx("long")
    base = ae.AdaptiveExitEngine(policy).plan(ctx)
    plan, reason = ae.apply_ai_price_plan(base, ctx, {
        "stop_loss_price": 98.5, "take_profit_1_price": 103,
        "take_profit_2_price": 106,
    }, policy)
    assert reason == "ai_stop_below_atr_min"
    assert plan == base


def test_apply_ai_price_plan_supports_short_direction():
    policy = production_adaptive_exit_policy()
    ctx = _ctx("short", risk_budget=20.0)
    base = ae.AdaptiveExitEngine(policy).plan(ctx)
    plan, reason = ae.apply_ai_price_plan(base, ctx, {
        "stop_loss_price": 104, "take_profit_1_price": 94,
        "take_profit_2_price": 88,
    }, policy)
    assert reason == "ai_exit_plan_applied"
    assert plan.stop_price == 104.0
    assert plan.tp1.price == 94.0
    assert plan.tp2.price == 88.0


def test_gemini_and_gpt_prompts_include_ai_exit_price_contract():
    assert '"exit_plan"' in gemini_analyzer.PROMPT_TEMPLATE
    assert "stop_loss_price" in gemini_analyzer.PROMPT_TEMPLATE
    assert "take_profit_1_price" in gemini_analyzer.PROMPT_TEMPLATE
    assert "exit_plan_decision" in openai_analyzer.PROMPT_TEMPLATE
    assert '"revise"' in openai_analyzer.PROMPT_TEMPLATE


def test_trader_applies_verified_ai_exit_plan_only_after_gpt_gate():
    src = Path(__import__("trader").__file__).read_text()
    body = src.split("def _handle_new_entry(", 1)[1].split("def run_cycle(", 1)[0]
    gate_at = body.index("allowed, gate_result, gpt_result = _gpt_entry_gate(")
    select_at = body.index("select_verified_ai_price_plan")
    apply_at = body.index("apply_ai_price_plan")
    execute_at = body.index("_execute_approved_entry_with_optional_reversal", gate_at)
    assert gate_at < select_at < apply_at < execute_at


def test_ai_exit_plan_json_braces_are_safe_for_python_format_templates():
    assert '"exit_plan": {{' in gemini_analyzer.PROMPT_TEMPLATE
    assert '"exit_plan": {{' in openai_analyzer.PROMPT_TEMPLATE
