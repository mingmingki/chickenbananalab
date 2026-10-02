from types import SimpleNamespace

import candidate_c_decision_engine as dec
import candidate_c_exit_management as cem
import candidate_c_reversal_state_machine as rsm
import candidate_c_setup_tracker as st
import candidate_c_strategy_policy


SYMBOL = "DOGE/USDT:USDT"


def _ctx():
    return dec.DecisionContext(
        account_id="acct", symbol=SYMBOL, strategy_id="candidate_c",
        config_version_id="1", config_hash="hash", risk_per_trade_pct=1.0,
        sizing_mode="FIXED_MARGIN",
        strategy_policy=candidate_c_strategy_policy.production_strategy_policy(),
    )


def _indicators(bars, donchian_n=20):
    if bars and bars[0].get("tf") == "4h":
        return [{"ema_20": 100.0, "ema_50": 90.0, "close": 110.0, "atr_14": 2.0}]
    return [{"ema_20": 100.0, "ema_50": 99.0, "close": 105.0, "atr_14": 1.0}]


def _decide(monkeypatch, tracker, bar_open_ms):
    snap = SimpleNamespace(
        bar_5m={"close_time_ms": bar_open_ms + 600_000, "close": 110.0},
        bar_1h={"close": 105.0},
        bar_10m_current={"open_time_ms": bar_open_ms},
        bars_10m_prior_20=[{"high": 111.0, "low": 90.0}],
    )
    monkeypatch.setattr(dec.tfc, "build_as_of_snapshot", lambda *a, **k: snap)
    monkeypatch.setattr(dec.tfc, "donchian_setup_condition", lambda _snap, side: side == "long")
    monkeypatch.setattr(dec, "_candidate_entry_overextension_gate", lambda **k: {"allowed": True})
    bars4 = [{"tf": "4h", "confirm": 1, "close_time_ms": bar_open_ms + 1}]
    bars1 = [{"tf": "1h", "confirm": 1, "close_time_ms": bar_open_ms + 1, "close": 105.0}]
    return dec.decide(
        _ctx(), as_of_ms=bar_open_ms + 2,
        bars_4h_confirmed_up_to_asof=bars4,
        bars_1h_confirmed_up_to_asof=bars1,
        bars_1d_confirmed_up_to_asof=[], bars_5m_for_10m_up_to_asof=[],
        setup_tracker=tracker, epoch_store=cem.PositionEpochStore.in_memory(),
        reversal_machine=rsm.SymbolReversalMachine(SYMBOL), current_position=None,
        weakening_prev=False, lot_step=0.01, min_size=0.01,
        indicator_fn=_indicators,
    )


def test_continued_true_setup_rechecks_on_next_confirmed_10m_bar(monkeypatch):
    tracker = st.SetupTracker.in_memory()
    first = tracker.observe(SYMBOL, "long", 1_000, True)
    tracker.record_attempt_outcome(first, "accepted")

    intent = _decide(monkeypatch, tracker, 1_600)

    assert intent.kind == dec.INTENT_ENTRY
    assert intent.reason_code == "setup_bar_recheck"
    assert intent.setup_id == st.make_setup_id(SYMBOL, "long", 1_600)


def test_continued_true_setup_is_not_reissued_twice_on_same_10m_bar(monkeypatch):
    tracker = st.SetupTracker.in_memory()
    first = tracker.observe(SYMBOL, "long", 1_000, True)
    tracker.record_attempt_outcome(first, "accepted")

    first_recheck = _decide(monkeypatch, tracker, 1_600)
    assert first_recheck.kind == dec.INTENT_ENTRY

    # 실제 cycle처럼 판단 직후 현재 10분봉의 조건을 tracker에 관측한다.
    tracker.observe(SYMBOL, "long", 1_600, True)
    duplicate = _decide(monkeypatch, tracker, 1_600)

    assert duplicate.kind == dec.INTENT_NO_ACTION
    assert duplicate.reason_code == "no_setup"
