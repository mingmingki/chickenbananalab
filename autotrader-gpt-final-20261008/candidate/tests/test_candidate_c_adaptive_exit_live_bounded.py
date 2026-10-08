from types import SimpleNamespace

import candidate_c_decision_engine as dec
import candidate_c_hybrid_live_adapter as live
from adaptive_exit_policy import policy_sha256, production_adaptive_exit_policy


def legacy_intent():
    return dec.Intent(
        kind=dec.INTENT_ENTRY, account_id="acct", symbol="DOGE/USDT:USDT",
        strategy_id="candidate_c", setup_id="setup", position_epoch=None,
        config_version_id="1", config_hash="cfg", decision_timestamp=1000,
        source_candle_close_timestamp=1000, side="long", idempotency_key="idem",
        reason_code="new_entry", input_snapshot_hash="snap", raw_stop_price=93.0,
        requested_risk_pct=1.0,
    )


def ctx(mode="LIVE_BOUNDED", approved=""):
    policy=production_adaptive_exit_policy()
    return dec.DecisionContext(
        account_id="acct",symbol="DOGE/USDT:USDT",strategy_id="candidate_c",
        config_version_id="1",config_hash="cfg",risk_per_trade_pct=1.0,
        sizing_mode="FIXED_MARGIN",strategy_policy=None,
        adaptive_exit_mode=mode,adaptive_exit_policy=policy,
        adaptive_approved_policy_hash=approved,
    )


def test_live_bounded_requires_exact_approved_policy_hash():
    policy=production_adaptive_exit_policy()
    legacy=legacy_intent()
    unapproved=dec._apply_live_bounded_entry_geometry(
        ctx(approved="wrong"),legacy,entry_price=100.0,atr=2.0,
        structural_support=90.0,structural_resistance=None)
    assert unapproved == legacy

    approved=dec._apply_live_bounded_entry_geometry(
        ctx(approved=policy_sha256(policy)),legacy,entry_price=100.0,atr=2.0,
        structural_support=90.0,structural_resistance=None)
    assert approved != legacy
    assert approved.raw_stop_price < 90.0
    assert approved.adaptive_policy_hash == policy_sha256(policy)
    assert approved.adaptive_mode == "LIVE_BOUNDED"


def test_shadow_and_advisory_never_change_intent_even_with_matching_hash():
    policy=production_adaptive_exit_policy(); h=policy_sha256(policy); legacy=legacy_intent()
    for mode in ("OFF","SHADOW","ADVISORY"):
        out=dec._apply_live_bounded_entry_geometry(
            ctx(mode=mode,approved=h),legacy,entry_price=100.0,atr=2.0,
            structural_support=90.0,structural_resistance=None)
        assert out == legacy


class Client:
    def instrument_metadata(self):
        return {"contract_size":1.0,"lot_step":0.01,"min_contracts":0.01}


def cfg(approved=True):
    policy=production_adaptive_exit_policy()
    return SimpleNamespace(
        CANDIDATE_C_SIZING_MODE="FIXED_MARGIN", CANDIDATE_C_LEVERAGE=5,
        CANDIDATE_C_MAX_ORDER_NOTIONAL_USDT=5000.0,
        CANDIDATE_C_FIXED_MARGIN_USDT=500.0,CANDIDATE_C_RISK_PER_TRADE_PCT=1.0,
        ADAPTIVE_EXIT_MODE="LIVE_BOUNDED",
        ADAPTIVE_EXIT_APPROVED_POLICY_HASH=policy_sha256(policy) if approved else "wrong",
    )


def test_approved_fixed_margin_keeps_500_margin_and_uses_adaptive_geometry():
    policy=production_adaptive_exit_policy(); intent=legacy_intent()
    intent=dec._apply_live_bounded_entry_geometry(
        ctx(approved=policy_sha256(policy)),intent,entry_price=100.0,atr=2.0,
        structural_support=90.0,structural_resistance=None)
    result=live._calculate_candidate_c_entry_amount(cfg(True),Client(),intent,100.0,3000.0)
    assert result["ok"] is True
    assert result["notional_usdt"] == 2500.0
    assert result.get("adaptive_risk_capped") is not True


def test_unapproved_live_bounded_keeps_legacy_fixed_margin_sizing():
    result=live._calculate_candidate_c_entry_amount(cfg(False),Client(),legacy_intent(),100.0,3000.0)
    assert result["ok"] is True
    assert result["notional_usdt"] == 2500.0
    assert result.get("adaptive_risk_capped") is not True
