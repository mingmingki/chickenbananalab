from types import SimpleNamespace

import candidate_c_decision_engine as dec
import candidate_c_hybrid_cycle as cycle
import candidate_c_setup_tracker as st


def _bars_1h(ema20=105.0):
    rows = []
    hour = 60 * 60 * 1000
    for i in range(30):
        rows.append({
            "open_time_ms": i * hour,
            "close_time_ms": (i + 1) * hour - 1,
            "close": 100.0 if i < 5 else 106.0,
            "confirm": 1,
            "ema_for_test": ema20,
        })
    return rows


def _indicator_fn(bars, *, donchian_n):
    return [{**b, "ema_20": b.get("ema_for_test", 105.0)} for b in bars]


def test_candidate_c_sol_like_entry_is_blocked():
    bars = _bars_1h(ema20=105.0)
    result = dec._candidate_entry_overextension_gate(
        side="long", entry_price=111.2, bars_1h=bars, atr14_4h=2.0,
        as_of_ms=bars[-1]["close_time_ms"], indicator_fn=_indicator_fn,
    )
    assert result["allowed"] is False
    assert result["reason"] == "entry_overextended_extreme"


def test_candidate_c_non_extreme_entry_is_allowed():
    bars = _bars_1h(ema20=105.0)
    result = dec._candidate_entry_overextension_gate(
        side="long", entry_price=109.0, bars_1h=bars, atr14_4h=2.0,
        as_of_ms=bars[-1]["close_time_ms"], indicator_fn=_indicator_fn,
    )
    assert result["allowed"] is True


def test_live_overextension_rejection_consumes_setup(tmp_path):
    tracker = st.SetupTracker.load(str(tmp_path / "setup.jsonl"))
    setup_id = tracker.observe("SOL/USDT:USDT", "long", 123000, True)
    intent = SimpleNamespace(
        symbol="SOL/USDT:USDT", side="long", setup_id=setup_id,
        reason_code="entry_overextended_extreme",
    )
    cfg = SimpleNamespace(logger=None)

    consumed = cycle._consume_overextension_no_action(
        cfg, "SOL/USDT:USDT", tracker, intent, 123000,
    )

    assert consumed is True
    assert tracker.pending_setup_ids() == []
    assert tracker.is_attempted(setup_id) is True


def test_current_bar_direction_mismatch_consumes_setup(tmp_path):
    tracker = st.SetupTracker.load(str(tmp_path / "setup.jsonl"))
    setup_id = tracker.observe("DOGE/USDT:USDT", "long", 456000, True)
    intent = SimpleNamespace(
        symbol="DOGE/USDT:USDT", side="long", setup_id=setup_id,
        reason_code="setup_direction_mismatch_or_no_atr",
    )
    cfg = SimpleNamespace(logger=None)

    consumed = cycle._consume_overextension_no_action(
        cfg, "DOGE/USDT:USDT", tracker, intent, 456000,
    )

    assert consumed is True
    assert tracker.pending_setup_ids() == []
    assert tracker.is_attempted(setup_id) is True
