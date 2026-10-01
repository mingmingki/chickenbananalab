import pandas as pd

import indicators


def test_compact_summary_includes_absolute_price_context_for_ai_exit_plan():
    df = pd.DataFrame([
        {"close": 100.0, "ema_20": 99.0, "ema_50": 98.0, "rsi_14": 55.0,
         "macd": 1.0, "macd_signal": 0.5, "atr_14": 2.0, "high": 102.0, "low": 97.0},
        {"close": 123.456, "ema_20": 120.0, "ema_50": 118.0, "rsi_14": 60.0,
         "macd": 1.2, "macd_signal": 0.8, "atr_14": 3.25, "high": 126.0, "low": 96.0},
    ])

    summary = indicators.summarize_compact(df, "5분", lookback=20)

    assert "현재가 123.456" in summary
    assert "EMA20 120" in summary
    assert "EMA50 118" in summary
    assert "ATR 3.25" in summary
