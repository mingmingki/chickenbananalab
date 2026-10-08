from types import SimpleNamespace

import trader
from adaptive_exit_policy import production_adaptive_exit_policy, policy_sha256


def _cfg(tmp_path):
    p=production_adaptive_exit_policy()
    return SimpleNamespace(
        CORE_ORDER_MODE="FIXED_MARGIN_AUTO_EXIT",CORE_EXIT_MODE="AUTO",
        ADAPTIVE_EXIT_MODE="LIVE_BOUNDED",ADAPTIVE_EXIT_APPROVED_POLICY_HASH=policy_sha256(p),
        LEVERAGE=5,POSITION_SIZE_MODE="FIXED",POSITION_FIXED_USDT=200.0,
        RISK_PER_TRADE_PCT=1.0,MAX_DAILY_LOSS_PCT=5.0,
        user_dir=str(tmp_path),logger=SimpleNamespace(warning=lambda *a,**k:None),
    )


def test_moderately_wide_stop_is_allowed_by_reducing_fixed_margin_notional(tmp_path, monkeypatch):
    monkeypatch.setattr(trader.adaptive_exit_log,"append_plan",lambda *a,**k:True)
    features={
        "atr":1.5,"structural_support":96.0,"structural_resistance":104.0,
        "near_resistance":110.0,"near_support":90.0,
        "continuation_resistance":112.0,"continuation_support":88.0,
        "source_timestamps":(1000,),"input_snapshot_hash":"pi-wide-stop",
    }
    legacy=("short",10.0,102.0,96.0)
    result=trader._core_adaptive_live_entry_decision(
        _cfg(tmp_path),symbol="PI/USDT:USDT",legacy_order_args=legacy,
        entry_price=100.0,equity=1000.0,market_features=features,
    )
    assert result["active"] is True
    assert result["blocked"] is False
    assert result["plan"].stop_price == 105.25
    assert result["plan"].planned_loss_usdt <= 50.0 + 1e-9
    assert result["plan"].effective_notional < 1000.0
    assert result["order_args"][1] < legacy[1]


def test_extremely_wide_stop_stays_blocked_when_post_cost_rr_is_too_low(tmp_path, monkeypatch):
    monkeypatch.setattr(trader.adaptive_exit_log,"append_plan",lambda *a,**k:True)
    features={
        "atr":2.0,"structural_support":80.0,"structural_resistance":120.0,
        "near_resistance":130.0,"near_support":70.0,
        "continuation_resistance":140.0,"continuation_support":60.0,
        "source_timestamps":(1000,),"input_snapshot_hash":"too-wide",
    }
    result=trader._core_adaptive_live_entry_decision(
        _cfg(tmp_path),symbol="PI/USDT:USDT",legacy_order_args=("short",10.0,102.0,96.0),
        entry_price=100.0,equity=1000.0,market_features=features,
    )
    assert result["blocked"] is True
    assert result["reason"] == "post_cost_rr_below_minimum"

def test_bounded_wide_stop_uses_actual_tp2_rr_for_core_order_contract(tmp_path, monkeypatch):
    monkeypatch.setattr(trader.adaptive_exit_log,"append_plan",lambda *a,**k:True)
    features={
        "atr":2.0,"structural_support":92.0,"structural_resistance":107.0,
        "near_resistance":110.0,"near_support":95.0,
        "continuation_resistance":112.0,"continuation_support":80.0,
        "source_timestamps":(1000,),"input_snapshot_hash":"pi-tp2-rescue",
    }
    result=trader._core_adaptive_live_entry_decision(
        _cfg(tmp_path),symbol="PI/USDT:USDT",legacy_order_args=("short",10.0,104.0,94.0),
        entry_price=100.0,equity=1000.0,market_features=features,
    )
    assert result["blocked"] is False
    assert result["reason"] == "adaptive_live_bounded_tp2_rr_rescue"
    # The returned plan is consumed by verified AI price overlays and audit.
    assert result["plan"].entry_allowed is True
    assert result["plan"].reason_code == "adaptive_live_bounded_tp2_rr_rescue"
    assert result["plan"].stop_price == 107.5
    assert result["order_args"][3] == result["plan"].tp2.price
    assert result["plan"].planned_loss_usdt <= 50.0 + 1e-9
    assert result["order_args"][1] < 10.0
