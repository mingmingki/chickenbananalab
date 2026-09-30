import datetime

import pandas as pd

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
