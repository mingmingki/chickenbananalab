import pytest

import entry_overextension_guard as guard


def test_sol_like_long_is_blocked_as_extreme():
    result = guard.evaluate(
        side="long", entry_price=110.67, ema20_1h=105.05858763163765,
        atr14_4h=1.809272372817885, reference_24h_price=101.56,
    )
    assert result["allowed"] is False
    assert result["reason"] == "entry_overextended_extreme"
    assert result["extension_atr"] > 3.0


def test_combo_blocks_btc_like_chase():
    result = guard.evaluate(
        side="long", entry_price=80246.7, ema20_1h=77962.8215,
        atr14_4h=818.2854, reference_24h_price=76390.0,
    )
    assert result["allowed"] is False
    assert result["reason"] == "entry_overextended_extreme"


def test_strong_but_not_extreme_trend_is_allowed():
    result = guard.evaluate(
        side="long", entry_price=2569.34, ema20_1h=2490.0,
        atr14_4h=40.5, reference_24h_price=2445.0,
    )
    assert 1.5 < result["extension_atr"] < 2.5
    assert result["allowed"] is True
    assert result["reason"] == "ok"


def test_short_is_symmetric():
    # Short entry is 2.6 ATR below EMA20 and market fell >5% in 24h.
    result = guard.evaluate(
        side="short", entry_price=94.0, ema20_1h=99.2,
        atr14_4h=2.0, reference_24h_price=100.0,
    )
    assert result["allowed"] is False
    assert result["extension_atr"] == pytest.approx(2.6)
    assert result["directional_24h_pct"] == pytest.approx(6.0)


def test_missing_inputs_fail_closed():
    result = guard.evaluate(
        side="long", entry_price=100.0, ema20_1h=None,
        atr14_4h=2.0, reference_24h_price=95.0,
    )
    assert result["allowed"] is False
    assert result["reason"] == "entry_overextension_data_unavailable"
