from types import SimpleNamespace

import candidate_c_decision_engine as dec
import candidate_c_exit_management as cem
import candidate_c_hybrid_live_adapter as live
import candidate_c_reversal_state_machine as rsm
import candidate_c_setup_tracker as st
import candidate_c_strategy_policy


def _snapshot():
    return SimpleNamespace(
        bar_5m={"close_time_ms": 1000, "close": 110.0},
        bar_1h={"close": 105.0},
        bar_10m_current={"open_time_ms": 500},
        bars_10m_prior_20=[{"high": 100.0, "low": 90.0}] * 20,
    )


def _ctx():
    return dec.DecisionContext(
        account_id="acct", symbol="DOGE/USDT:USDT", strategy_id="candidate_c",
        config_version_id="1", config_hash="hash", risk_per_trade_pct=1.0,
        sizing_mode="FIXED_MARGIN",
        strategy_policy=candidate_c_strategy_policy.production_strategy_policy(),
        adaptive_exit_mode="OFF",
    )


def _bars():
    bars4=[{"tf":"4h","confirm":1,"close_time_ms":900,"high":112.0,"low":92.0,"close":101.0}]
    bars1=[{"tf":"1h","confirm":1,"close_time_ms":900,"high":108.0,"low":96.0,"close":105.0}]
    bars5=[{"tf":"5m","confirm":1,"open_time_ms":500,"close_time_ms":1000,"high":111.0,"low":109.0,"close":110.0}]
    return bars4,bars1,bars5


def _indicators_lower_tf_long_4h_none(bars, donchian_n=20):
    tf=bars[0].get("tf") if bars else None
    if tf == "4h":
        return [{"ema_20":100.0,"ema_50":102.0,"close":101.0,"atr_14":2.0}]
    if tf == "1h":
        return [{"ema_20":100.0,"ema_50":99.0,"close":105.0,"atr_14":1.0}]
    return [{"ema_20":105.0,"ema_50":104.0,"close":110.0,"atr_14":1.0}]


def _indicators_lower_tf_mixed_4h_none(bars, donchian_n=20):
    tf=bars[0].get("tf") if bars else None
    if tf == "4h":
        return [{"ema_20":100.0,"ema_50":102.0,"close":101.0,"atr_14":2.0}]
    if tf == "1h":
        return [{"ema_20":100.0,"ema_50":99.0,"close":105.0,"atr_14":1.0}]
    return [{"ema_20":106.0,"ema_50":107.0,"close":105.0,"atr_14":1.0}]


def _decide(monkeypatch, indicator_fn):
    snap=_snapshot()
    monkeypatch.setattr(dec.tfc,"build_as_of_snapshot",lambda *a,**k:snap)
    monkeypatch.setattr(dec.tfc,"donchian_setup_condition",lambda _snap,side: side == "long")
    monkeypatch.setattr(dec,"_candidate_entry_overextension_gate",lambda **k:{"allowed":True})
    bars4,bars1,bars5=_bars()
    return dec.decide(
        _ctx(),as_of_ms=500,
        bars_4h_confirmed_up_to_asof=bars4,bars_1h_confirmed_up_to_asof=bars1,
        bars_1d_confirmed_up_to_asof=[],bars_5m_for_10m_up_to_asof=bars5,
        setup_tracker=st.SetupTracker.in_memory(),epoch_store=cem.PositionEpochStore.in_memory(),
        reversal_machine=rsm.SymbolReversalMachine("DOGE/USDT:USDT"),
        current_position=None,weakening_prev=False,lot_step=0.01,min_size=0.01,
        indicator_fn=indicator_fn,
    )


def test_4h_none_allows_half_size_pilot_only_when_1h_and_5m_align(monkeypatch):
    intent=_decide(monkeypatch,_indicators_lower_tf_long_4h_none)
    assert intent.kind == dec.INTENT_ENTRY
    assert intent.reason_code == "setup_pilot_4h_none_lower_tf_aligned"
    assert intent.entry_size_fraction == 0.5


def test_4h_none_still_blocks_when_lower_timeframes_are_not_aligned(monkeypatch):
    intent=_decide(monkeypatch,_indicators_lower_tf_mixed_4h_none)
    assert intent.kind == dec.INTENT_NO_ACTION
    assert intent.reason_code == "setup_direction_mismatch_or_no_atr"


class _Client:
    def instrument_metadata(self):
        return {"contract_size":1.0,"lot_step":0.01,"min_contracts":0.01}


def test_pilot_fraction_halves_fixed_margin_order_size():
    intent=SimpleNamespace(raw_stop_price=95.0,entry_size_fraction=0.5)
    cfg=SimpleNamespace(
        CANDIDATE_C_ORDER_MODE="FIXED_MARGIN_AUTO_EXIT",
        CANDIDATE_C_SIZING_MODE="FIXED_MARGIN",CANDIDATE_C_LEVERAGE=5.0,
        CANDIDATE_C_MAX_ORDER_NOTIONAL_USDT=5000.0,CANDIDATE_C_FIXED_MARGIN_USDT=150.0,
    )
    result=live._calculate_candidate_c_entry_amount(cfg,_Client(),intent,100.0,2000.0)
    assert result["ok"] is True
    assert result["notional_usdt"] == 375.0
    assert result["entry_size_fraction"] == 0.5
