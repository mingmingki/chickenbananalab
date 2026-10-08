import pandas as pd
from pathlib import Path

import core_post_runup_correction as correction
import core_short_level
import trader


def _one_hour(*, weakening=True):
    rows = []
    for i in range(30):
        close = 100.0 + i * 0.25
        rows.append({
            "timestamp": pd.Timestamp("2026-09-20T00:00:00") + pd.Timedelta(hours=i),
            "open": close - 0.2,
            "high": close + 0.5,
            "low": close - 0.5,
            "close": close,
            "ema_20": 104.0,
            "ema_50": 100.0,
            "macd": 4.0,
            "macd_signal": 1.0,
        })
    # 24h reference around 101.25; create a material recent peak.
    rows[-4]["high"] = 111.0
    rows[-3]["high"] = 110.5
    rows[-2]["high"] = 109.5
    rows[-1]["high"] = 109.0
    if weakening:
        rows[-3]["macd"], rows[-3]["macd_signal"] = 4.0, 1.0  # hist 3
        rows[-2]["macd"], rows[-2]["macd_signal"] = 3.0, 1.0  # hist 2
        rows[-1]["macd"], rows[-1]["macd_signal"] = 2.0, 1.0  # hist 1
    else:
        rows[-3]["macd"], rows[-3]["macd_signal"] = 2.0, 1.0  # hist 1
        rows[-2]["macd"], rows[-2]["macd_signal"] = 3.0, 1.0  # hist 2
        rows[-1]["macd"], rows[-1]["macd_signal"] = 4.0, 1.0  # hist 3
    return pd.DataFrame(rows)


def _four_hour():
    return pd.DataFrame([{
        "timestamp": pd.Timestamp("2026-09-21T20:00:00"),
        "open": 106.0, "high": 108.0, "low": 105.0, "close": 107.0,
        "ema_20": 103.0, "ema_50": 100.0,
        "macd": 3.0, "macd_signal": 2.0, "atr_14": 3.0,
    }])


def _five_minute(close=107.0):
    return pd.DataFrame([{
        "timestamp": pd.Timestamp("2026-09-22T00:55:00"),
        "open": close + 0.2, "high": close + 0.4, "low": close - 0.3,
        "close": close, "ema_20": close + 0.5, "ema_50": close + 0.8,
        "macd": -0.5, "macd_signal": -0.2,
    }])


BEARISH = {"high_structure": "LH", "low_structure": "LL"}
BULLISH = {"high_structure": "HH", "low_structure": "HL"}


def test_post_runup_correction_activates_on_runup_retrace_1h_weakness_and_lower_bearish():
    result = correction.evaluate(_one_hour(), _four_hour(), _five_minute(), BEARISH, BEARISH)
    assert result["active"] is True
    assert result["runup_24h_pct"] >= 4.0
    assert result["peak_retracement_pct"] >= 1.0
    assert result["one_h_hist_worsening"] is True
    assert result["three_m_bearish"] is True
    assert result["five_m_bearish"] is True


def test_post_runup_correction_turns_off_when_1h_momentum_recovers():
    result = correction.evaluate(_one_hour(weakening=False), _four_hour(), _five_minute(), BEARISH, BEARISH)
    assert result["active"] is False
    assert result["reason"] == "1h_momentum_not_weakening"


def test_post_runup_correction_turns_off_when_lower_structure_recovers():
    result = correction.evaluate(_one_hour(), _four_hour(), _five_minute(), BULLISH, BEARISH)
    assert result["active"] is False
    assert result["reason"] == "3m_5m_not_both_bearish"


def test_correction_context_explicitly_tells_ai_not_to_equate_1d_bullish_with_long():
    ctx = correction.evaluate(_one_hour(), _four_hour(), _five_minute(), BEARISH, BEARISH)
    text = correction.prompt_context(ctx)
    assert "CORRECTION_ACTIVE=true" in text
    assert "1D 강세만을 이유로 신규 LONG/기존 LONG hold를 정당화하지 마세요" in text
    assert "SHORT 반전" in text


def test_short_level_allows_correction_early_before_4h_full_bearish():
    ctx = correction.evaluate(_one_hour(), _four_hour(), _five_minute(), BEARISH, BEARISH)
    # 4H close > EMA20 and 1H close > EMA20 => old EARLY/TACTICAL path is not enough.
    result = core_short_level.classify(
        gemini_action="short", gemini_confidence=0.78, regime="bullish",
        closed_1d_df=_one_hour(), closed_4h_df=_four_hour(),
        closed_1h_df=_one_hour(), closed_3m_df=_five_minute(), closed_5m_df=_five_minute(),
        structure_3m=BEARISH, structure_5m=BEARISH,
        correction_ctx=ctx,
    )
    assert result["level"] == "EARLY"
    assert result["reasons"]["4h_bearish"] is False
    assert result["reasons"]["post_runup_correction_active"] is True
    assert result["correction_mode"] is True


def test_same_short_remains_none_without_correction_mode():
    result = core_short_level.classify(
        gemini_action="short", gemini_confidence=0.78, regime="bullish",
        closed_1d_df=_one_hour(), closed_4h_df=_four_hour(),
        closed_1h_df=_one_hour(), closed_3m_df=_five_minute(), closed_5m_df=_five_minute(),
        structure_3m=BEARISH, structure_5m=BEARISH,
        correction_ctx={"active": False},
    )
    assert result["level"] == "NONE"


def test_only_confirmed_long_to_short_correction_gets_live_close_exception():
    active = {"active": True}
    inactive = {"active": False}
    assert trader._correction_short_reversal_allowed({"side": "long"}, "short", active) is True
    assert trader._correction_short_reversal_allowed({"side": "long"}, "short", inactive) is False
    assert trader._correction_short_reversal_allowed({"side": "short"}, "long", active) is False
    assert trader._correction_short_reversal_allowed({"side": "long"}, "close", active) is False
    assert trader._correction_short_reversal_allowed(None, "short", active) is False


def test_prompts_and_trader_wire_reversal_and_long_block():
    root = Path(__file__).resolve().parents[1]
    gemini = (root / "gemini_analyzer.py").read_text()
    openai = (root / "openai_analyzer.py").read_text()
    trader = (root / "trader.py").read_text()
    assert "현재 LONG 보유 중이면 LONG 청산 후 SHORT 반전 의사표현" in gemini
    assert "CORRECTION_ACTIVE=true" in gemini
    assert "post-runup correction active" in openai
    assert "BLOCK post_runup_correction_long" in trader
    assert "CORE_CORRECTION_REVERSAL 허용" in trader
    assert 'and correction_ctx.get("active")' in trader
