from types import SimpleNamespace
import json

import pytest

import trader
from adaptive_exit_engine import apply_ai_price_plan, select_verified_ai_price_plan
from adaptive_exit_policy import production_adaptive_exit_policy, policy_sha256


@pytest.mark.parametrize("side,stop,tp1,tp2", [
    ("short", 104.0, 94.0, 88.0),
    ("long", 96.0, 106.0, 112.0),
])
def test_tp2_rescue_accepts_valid_verified_ai_exit_through_real_returned_plan(
    tmp_path, side, stop, tp1, tp2
):
    policy = production_adaptive_exit_policy()
    cfg = SimpleNamespace(
        CORE_ORDER_MODE="FIXED_MARGIN_AUTO_EXIT", CORE_EXIT_MODE="AUTO",
        ADAPTIVE_EXIT_MODE="LIVE_BOUNDED",
        ADAPTIVE_EXIT_APPROVED_POLICY_HASH=policy_sha256(policy),
        LEVERAGE=5, POSITION_SIZE_MODE="FIXED", POSITION_FIXED_USDT=200.0,
        RISK_PER_TRADE_PCT=1.0, MAX_DAILY_LOSS_PCT=5.0,
        user_dir=str(tmp_path), logger=SimpleNamespace(warning=lambda *a, **k: None),
    )
    features = dict(
        atr=2.0, structural_support=93.0, structural_resistance=107.0,
        near_resistance=105.0, near_support=95.0,
        continuation_resistance=120.0, continuation_support=80.0,
        source_timestamps=(1000,), input_snapshot_hash="verified-ai-after-tp2-rescue",
    )
    decision = trader._core_adaptive_live_entry_decision(
        cfg, symbol="PI/USDT:USDT",
        legacy_order_args=(side, 10.0, stop, tp1),
        entry_price=100.0, equity=1000.0, market_features=features,
    )
    assert decision["blocked"] is False
    assert decision["reason"] == "adaptive_live_bounded_tp2_rr_rescue"
    selected, selected_reason = select_verified_ai_price_plan(
        {"exit_plan": {
            "stop_loss_price": stop,
            "take_profit_1_price": tp1,
            "take_profit_2_price": tp2,
        }},
        {"exit_plan_decision": "approve"},
    )
    assert selected_reason == "gemini_approved"
    applied, apply_reason = apply_ai_price_plan(
        decision["plan"], decision["context"], selected, policy
    )
    assert apply_reason == "ai_exit_plan_applied"
    assert applied.stop_price == stop
    assert applied.tp1.price == tp1
    assert applied.tp2.price == tp2
    assert applied.entry_allowed is True
    assert applied.planned_loss_usdt <= 50.0 + 1e-9
    records = [
        json.loads(line) for p in tmp_path.glob("*adaptive*jsonl")
        for line in p.read_text().splitlines()
    ]
    assert records[-1]["entry_allowed"] == decision["plan"].entry_allowed
    assert records[-1]["plan_hash"] == decision["plan"].plan_hash
