"""Regressions from the 2026-10-07 report; no provider or exchange requests."""
import datetime
import json
import logging
from types import SimpleNamespace

import pytest
import adaptive_exit_engine as ae
import openai_analyzer
import trader
from adaptive_exit_policy import production_adaptive_exit_policy
from state import TraderState


def test_historical_one_r_gpt_revision_is_rejected_by_execution_contract():
    policy = production_adaptive_exit_policy()
    contract = ae.build_ai_price_contract("long", 2714.56, 26.05775129892906, policy, leverage=5)
    proposal = {
        "stop_loss_price": 2675.47,
        "take_profit_1_price": 2753.65,
        "take_profit_2_price": 2792.73,
    }
    verdict, selected, reason = openai_analyzer._apply_exit_contract_to_verdict(
        "revise", None, proposal, contract,
    )
    assert verdict == "reject"
    assert selected is None
    assert reason == "gpt_revision_contract_post_cost_rr_below_minimum"


@pytest.mark.parametrize("side,stop,tp1,tp2", [
    ("long", 96.0, 104.8, 108.0),
    ("short", 104.0, 95.2, 92.0),
])
def test_contract_checks_net_reward_against_cost_included_risk(side, stop, tp1, tp2):
    contract = ae.build_ai_price_contract(side, 100.0, 1.0, production_adaptive_exit_policy())
    contract["estimated_roundtrip_cost_rate"] = 0.004
    proposal = {"stop_loss_price": stop, "take_profit_1_price": tp1, "take_profit_2_price": tp2}
    # Gross 1.20R, but (4.8 - 0.4)/(4.0 + 0.4) = 1.0 net RR.
    assert ae.validate_ai_price_plan_contract(proposal, contract) == "post_cost_rr_below_minimum"


@pytest.mark.parametrize("side,stop,tp1,tp2", [
    ("long", 96.0, 105.25, 108.0),
    ("short", 104.0, 94.75, 92.0),
])
def test_contract_acceptance_agrees_with_overlay_when_costs_are_proven(side, stop, tp1, tp2):
    policy = production_adaptive_exit_policy()
    contract = ae.build_ai_price_contract(side, 100.0, 1.0, policy)
    contract["estimated_roundtrip_cost_rate"] = 0.004
    proposal = {"stop_loss_price": stop, "take_profit_1_price": tp1, "take_profit_2_price": tp2}
    ctx = ae.AdaptiveExitContext(
        symbol="X/USDT:USDT", side=side, entry_price=100.0, current_quantity=10.0,
        current_stop=None, equity_usdt=1000.0, trade_risk_budget_usdt=50.0,
        atr=1.0, structural_support=98.0, structural_resistance=102.0,
        configured_margin_usdt=200.0, leverage=5.0, order_cap_notional=1000.0,
        estimated_roundtrip_cost_rate=0.004, mode="LIVE_BOUNDED",
    )
    base = ae.AdaptiveExitEngine(policy).plan(ctx)
    assert ae.validate_ai_price_plan_contract(proposal, contract) == "ok"
    applied, reason = ae.apply_ai_price_plan(base, ctx, proposal, policy)
    assert reason == "ai_exit_plan_applied"
    assert applied.stop_price == stop
    assert applied.tp1.price == tp1


def _core_gate_attempt(tmp_path, monkeypatch, *, gpt_allows=True):
    real_datetime = datetime.datetime

    class FrozenDateTime(real_datetime):
        @classmethod
        def now(cls, tz=None):
            value = cls(2026, 10, 7, 10, 48, 15)
            return value.replace(tzinfo=tz) if tz is not None else value

    monkeypatch.setattr(trader.datetime, "datetime", FrozenDateTime)
    cfg = SimpleNamespace(
        user_dir=str(tmp_path), logger=logging.getLogger("report_regression"),
        GPT_ENTRY_GATE_ENABLED=True, OPENAI_API_KEY="unit-test-no-network",
    )
    state = TraderState()
    seen = {}
    gpt = {
        "decision": "approve_now" if gpt_allows else "wait",
        "confidence": 0.75, "reasoning": "fixture",
        "exit_plan_decision": "reject", "exit_plan": None, "error_reason": None,
    }

    def provider_gate(*args, **kwargs):
        seen["contract"] = kwargs.get("exit_price_contract")
        return gpt_allows, ("approved" if gpt_allows else "blocked_wait"), gpt

    def order(*args, **kwargs):
        seen["order_amount"] = args[5]
        return True

    monkeypatch.setattr(trader, "_gpt_entry_gate", provider_gate)
    monkeypatch.setattr(trader, "_execute_approved_entry_with_optional_reversal", order)
    # ADA + SHORT is evidence; approval authorizes the configured amount.
    trader._handle_new_entry(
        cfg, state, object(), "ADA/USDT:USDT", "short",
        {"action": "short", "confidence": 0.72}, "fixture-id", "entry",
        ["5m"], "fixture", None, 100.0, 10.0, 104.0, 92.0,
        adaptive_context=SimpleNamespace(
            entry_price=100.0, atr=1.0, leverage=5.0, estimated_roundtrip_cost_rate=0.004,
        ),
    )
    attempt = state.snapshot()["symbols"]["ADA/USDT:USDT"]["last_entry_attempt"]
    audit = json.loads((tmp_path / "gpt_shadow_log.jsonl").read_text().splitlines()[-1])
    assert audit["order_success"] is gpt_allows
    return attempt, audit, seen


def test_gpt_approval_executes_without_historical_risk_veto(tmp_path, monkeypatch):
    attempt, audit, seen = _core_gate_attempt(tmp_path, monkeypatch)
    assert audit["gpt_decision"] == "approve_now"
    assert audit["gate_result"] == "approved"
    assert attempt["status"] == "FILLED"
    assert seen["order_amount"] == 10.0
    assert attempt["gpt_decision"] == "approve_now"
    assert attempt["gpt_confidence"] == 0.75


def test_core_contract_carries_same_cost_as_later_execution_overlay(tmp_path, monkeypatch):
    _, _, seen = _core_gate_attempt(tmp_path, monkeypatch)
    contract = seen["contract"]
    assert contract.get("estimated_roundtrip_cost_rate") == 0.004
    # An identical 1.20R proposal must fail before being called an executable revision.
    proposal = {"stop_loss_price": 104.0, "take_profit_1_price": 95.2, "take_profit_2_price": 92.0}
    assert ae.validate_ai_price_plan_contract(proposal, contract) == "post_cost_rr_below_minimum"


def test_actual_gpt_wait_keeps_its_distinct_status(tmp_path, monkeypatch):
    attempt, audit, _ = _core_gate_attempt(tmp_path, monkeypatch, gpt_allows=False)
    assert attempt["status"] == "GPT_WAIT"
    assert audit["gpt_decision"] == "wait"
    assert audit["gate_result"] == "blocked_wait"
