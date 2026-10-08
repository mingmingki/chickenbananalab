import pandas as pd

import core_long_confirmation
import trader


def _one_hour(close=105.0, ema20=100.0, hists=(-2.0, -1.0, 0.0)):
    rows=[]
    for i,h in enumerate(hists):
        macd=10.0+h
        rows.append({
            "close": close,
            "ema_20": ema20,
            "macd": macd,
            "macd_signal": 10.0,
        })
    return pd.DataFrame(rows)


def test_bearish_3m_5m_no_longer_veto_when_1h_minimum_is_confirmed():
    allowed, reason = core_long_confirmation.check_long_confirmation(
        _one_hour(close=105.0, ema20=100.0),
        {"high_structure":"LH","low_structure":"LL"},
        {"high_structure":"LH","low_structure":"LL"},
    )
    assert allowed is True
    assert reason == "ok_1h_minimum_confirmed"


def test_weak_1h_still_blocks_even_with_bullish_lower_timeframes():
    # close below EMA20 and MACD histogram deteriorating.
    allowed, reason = core_long_confirmation.check_long_confirmation(
        _one_hour(close=95.0, ema20=100.0, hists=(0.0,-1.0,-2.0)),
        {"high_structure":"HH","low_structure":"HL"},
        {"high_structure":"HH","low_structure":"HL"},
    )
    assert allowed is False
    assert reason == "close_below_ema20 and macd_hist_not_improving"


def test_long_timing_context_contains_3m_5m_structure_for_gpt():
    text=trader._long_entry_timing_context({
        "3m":{"high_structure":"LH","low_structure":"HL"},
        "5m":{"high_structure":"LH","low_structure":"LL"},
    })
    assert "GPT 최종 판단용" in text
    assert "로컬 hard veto 아님" in text
    assert "3m: high_structure=LH, low_structure=HL" in text
    assert "5m: high_structure=LH, low_structure=LL" in text
    assert "bullish_structure=True" in text
    assert "bullish_structure=False" in text


def test_source_passes_advisory_summary_into_entry_handler():
    source=open(trader.__file__,encoding="utf-8").read()
    assert 'entry_candle_summary = (' in source
    assert '_long_entry_timing_context(structures)' in source
    assert 'tf_list, entry_candle_summary, position' in source
