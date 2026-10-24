"""Real entry-gate tests: low volatility exhaustion versus renewed setups."""
import pandas as pd
import pytest

import trader


def frames(side, moves, atr=.2):
    sign = 1 if side == "long" else -1
    start = pd.Timestamp("2026-10-04T10:00:00")
    return {
        "1h": pd.DataFrame([
            {"timestamp": start - pd.Timedelta(hours=24), "close": 100, "ema_20": 100},
            {"timestamp": start, "close": 100, "ema_20": 100},
        ]),
        "4h": pd.DataFrame([{"timestamp": start, "close": 100, "atr_14": 2}]),
        "5m": pd.DataFrame([
            {"timestamp": start + pd.Timedelta(minutes=5*i),
             "close": 100 + sign*move, "atr_14": atr}
            for i, move in enumerate(moves)
        ]),
    }


@pytest.mark.parametrize("side", ["long", "short"])
def test_blocks_125_atr_directional_move_before_fixed_half_percent(side):
    data = frames(side, [0, .03125, .0625, .125, .1875, .21875, .25])
    out = trader._core_entry_overextension_gate(data, side, data["5m"].iloc[-1]["close"])
    assert out["allowed"] is False
    assert out["reason"] == "entry_late_exhaustion_no_pullback"
    assert out["move_30m_atr"] == pytest.approx(1.25)
    assert out["pullback_atr"] == pytest.approx(0)


@pytest.mark.parametrize("side", ["long", "short"])
def test_recent_meaningful_pullback_allows_normal_renewed_setup(side):
    data = frames(side, [0, .10, .18, .30, .14, .20, .25])
    out = trader._core_entry_overextension_gate(data, side, data["5m"].iloc[-1]["close"])
    assert out["allowed"] is True
    assert out["move_30m_atr"] == pytest.approx(1.25)
    assert out["pullback_atr"] == pytest.approx(.8)


@pytest.mark.parametrize("side", ["long", "short"])
def test_old_pullback_does_not_excuse_fresh_exhaustion(side):
    data = frames(side, [0, .30, .10, .18, .21, .24, .30])
    out = trader._core_entry_overextension_gate(data, side, data["5m"].iloc[-1]["close"])
    assert out["allowed"] is False
    assert out["pullback_atr"] == pytest.approx(0)


@pytest.mark.parametrize("side", ["long", "short"])
def test_same_price_move_in_higher_volatility_is_not_late(side):
    data = frames(side, [0, .03125, .0625, .125, .1875, .21875, .25], atr=.8)
    out = trader._core_entry_overextension_gate(data, side, data["5m"].iloc[-1]["close"])
    assert out["allowed"] is True
    assert out["move_30m_atr"] == pytest.approx(.3125)


@pytest.mark.parametrize("side", ["long", "short"])
def test_move_opposite_entry_direction_is_not_exhaustion(side):
    data = frames(side, [0, -.03125, -.0625, -.125, -.1875, -.21875, -.25])
    out = trader._core_entry_overextension_gate(data, side, data["5m"].iloc[-1]["close"])
    assert out["allowed"] is True
    assert out["move_30m_atr"] == pytest.approx(-1.25)


@pytest.mark.parametrize("side", ["long", "short"])
def test_small_pullback_still_blocks_exhaustion(side):
    data = frames(side, [0, .05, .1, .2, .15, .23, .25])
    out = trader._core_entry_overextension_gate(data, side, data["5m"].iloc[-1]["close"])
    assert out["allowed"] is False
    assert out["pullback_atr"] == pytest.approx(.25)


@pytest.mark.parametrize("atr", [0, float("nan"), None])
def test_unavailable_atr_keeps_fixed_chase_guard(atr):
    data = frames("short", [0, .1, .2, .3, .4, .5, .6], atr=atr)
    out = trader._core_entry_overextension_gate(data, "short", 99.4)
    assert out["allowed"] is False
    assert out["reason"] == "short_chase_30m_drop"
    assert out["move_30m_atr"] is None
    assert out["freshness_reason"] == "freshness_data_unavailable"


def test_missing_five_minute_context_preserves_extreme_extension_guard():
    data = frames("long", [0, .1, .2, .3, .4, .5, .6])
    del data["5m"]
    out = trader._core_entry_overextension_gate(data, "long", 106)
    assert out["allowed"] is False
    assert out["reason"] == "entry_overextended_extreme"
    assert out["move_30m_atr"] is None


def test_fixed_chase_still_blocks_even_with_meaningful_pullback():
    data = frames("long", [0, .1, .2, .8, .3, .5, .6])
    out = trader._core_entry_overextension_gate(data, "long", 100.6)
    assert out["allowed"] is False
    assert out["reason"] == "long_chase_30m_rise"
    assert out["pullback_atr"] == pytest.approx(2.5)


@pytest.mark.parametrize("side", ["long", "short"])
def test_exact_half_atr_pullback_allows_renewed_setup_despite_float_rounding(side):
    data = frames(side, [0, .05, .10, .30, .20, .22, .25])
    out = trader._core_entry_overextension_gate(data, side, data["5m"].iloc[-1]["close"])
    assert out["allowed"] is True
    assert out["pullback_atr"] == pytest.approx(.5)
