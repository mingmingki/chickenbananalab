import datetime

import pandas as pd
import pytest

import trader


def _frames(*, ema20, atr14, reference=100.0):
    base = pd.Timestamp("2026-09-18T00:00:00")
    one_h = []
    for i in range(26):
        one_h.append({
            "timestamp": base + pd.Timedelta(hours=i),
            "close": reference if i <= 1 else 105.0,
            "ema_20": ema20,
        })
    four_h = pd.DataFrame([{
        "timestamp": base,
        "close": 105.0,
        "atr_14": atr14,
    }])
    return {"1h": pd.DataFrame(one_h), "4h": four_h}


def test_core_extreme_long_is_blocked():
    frames = _frames(ema20=105.0, atr14=2.0, reference=100.0)
    result = trader._core_entry_overextension_gate(frames, "long", 111.0)
    assert result["allowed"] is False
    assert result["reason"] == "entry_overextended_extreme"


def test_core_strong_non_extreme_is_allowed():
    frames = _frames(ema20=105.0, atr14=3.0, reference=100.0)
    result = trader._core_entry_overextension_gate(frames, "long", 110.5)
    assert result["allowed"] is True


def test_core_missing_confirmed_context_fails_closed():
    result = trader._core_entry_overextension_gate({}, "long", 110.0)
    assert result["allowed"] is False
    assert result["reason"] == "entry_overextension_data_unavailable"


def _add_five_minute_history(frames, *, start_price: float, end_price: float):
    base = pd.Timestamp("2026-09-19T00:00:00")
    closes = [start_price] + [end_price] * 6
    frames = dict(frames)
    frames["5m"] = pd.DataFrame([
        {"timestamp": base + pd.Timedelta(minutes=5 * i), "close": close}
        for i, close in enumerate(closes)
    ])
    return frames


def test_core_short_blocks_when_confirmed_30m_drop_is_at_least_half_percent():
    frames = _frames(ema20=99.5, atr14=2.0, reference=100.0)
    frames = _add_five_minute_history(frames, start_price=100.0, end_price=99.4)
    result = trader._core_entry_overextension_gate(frames, "short", 99.4)
    assert result["allowed"] is False
    assert result["reason"] == "short_chase_30m_drop"
    assert result["short_30m_drop_pct"] == pytest.approx(0.6)


def test_core_short_allows_sub_half_percent_30m_drop_when_other_guards_allow():
    frames = _frames(ema20=99.5, atr14=2.0, reference=100.0)
    frames = _add_five_minute_history(frames, start_price=100.0, end_price=99.51)
    result = trader._core_entry_overextension_gate(frames, "short", 99.51)
    assert result["allowed"] is True
    assert result["short_30m_drop_pct"] == pytest.approx(0.49)
