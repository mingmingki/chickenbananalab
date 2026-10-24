import entry_overextension_guard
import pytest
import trader


def _extreme():
    return {"allowed": False, "reason": entry_overextension_guard.BLOCK_REASON}


def test_profit_lock_eligible_after_one_r_profit_and_extreme_move():
    assert trader._profit_lock_eligible("long", 102.0, 100.0, 1.5, _extreme())


def test_profit_lock_is_symmetric_for_short():
    assert trader._profit_lock_eligible("short", 98.0, 100.0, 1.5, _extreme())


def test_profit_lock_eligible_at_three_quarter_r_when_extreme():
    assert trader._profit_lock_eligible("long", 101.125, 100.0, 1.5, _extreme())


def test_profit_lock_rejects_profit_below_three_quarter_r():
    assert not trader._profit_lock_eligible("long", 101.0, 100.0, 1.5, _extreme())


def test_profit_lock_rejects_non_extreme_move():
    ordinary = {"allowed": True, "reason": "ok"}
    assert not trader._profit_lock_eligible("long", 103.0, 100.0, 1.5, ordinary)


def test_profit_lock_opens_fast_reduce_gate_while_position_is_profitable(monkeypatch):
    import datetime

    closed_ts = (datetime.datetime.now(datetime.timezone.utc)
                 - datetime.timedelta(minutes=5)).isoformat()
    rows_5m = [
        {"ts": closed_ts, "close": 106.0, "rsi_14": 55.0, "macd": 3.0,
         "ema_20": 104.0, "ema_50": 102.0, "atr_14": 1.0},
        {"ts": closed_ts, "close": 105.5, "rsi_14": 45.0, "macd": 2.0,
         "ema_20": 104.0, "ema_50": 102.0, "atr_14": 1.0},
        {"ts": closed_ts, "close": 105.0, "rsi_14": 40.0, "macd": 1.0,
         "ema_20": 104.0, "ema_50": 102.0, "atr_14": 1.0},
    ]
    rows_1h = [{"ts": closed_ts, "close": 100.0, "rsi_14": 60.0,
                "macd": 1.0, "ema_20": 100.0, "ema_50": 98.0,
                "atr_14": 2.0} for _ in range(25)]
    rows_1h[-1]["close"] = 106.0
    rows_4h = [{"ts": closed_ts, "close": 106.0, "rsi_14": 60.0,
                "macd": 1.0, "ema_20": 102.0, "ema_50": 99.0,
                "atr_14": 2.0}]

    def fake_tail(_raw, tf, n=2):
        return {"5m": rows_5m, "1h": rows_1h, "4h": rows_4h}[tf][-n:]

    monkeypatch.setattr(trader, "_closed_indicator_tail", fake_tail)
    result = trader._fast_reduce_numeric_diagnostics(
        "long", {}, 106.0, 100.0, stop_loss_pct=1.5)
    assert result["adverse_last_price"] is False
    assert result["profit_lock_eligible"] is True
    assert result["numeric_conditions_met"] is True


def test_half_r_profit_protection_opens_on_confirmed_5m_weakening(monkeypatch):
    import datetime
    closed_ts = (datetime.datetime.now(datetime.timezone.utc)
                 - datetime.timedelta(minutes=5)).isoformat()
    rows_5m = [
        {"ts": closed_ts, "close": 101.0, "rsi_14": 55.0, "macd": 3.0,
         "ema_20": 100.0, "ema_50": 99.0, "atr_14": 1.0},
        {"ts": closed_ts, "close": 100.95, "rsi_14": 45.0, "macd": 2.0,
         "ema_20": 100.0, "ema_50": 99.0, "atr_14": 1.0},
        {"ts": closed_ts, "close": 100.9, "rsi_14": 40.0, "macd": 1.0,
         "ema_20": 100.0, "ema_50": 99.0, "atr_14": 1.0},
    ]
    rows_1h = [{"ts": closed_ts, "close": 100.0, "rsi_14": 55.0,
                "macd": 1.0, "ema_20": 100.0, "ema_50": 99.0,
                "atr_14": 2.0} for _ in range(25)]
    rows_1h[-1]["close"] = 100.9
    rows_4h = [{"ts": closed_ts, "close": 100.9, "rsi_14": 55.0,
                "macd": 1.0, "ema_20": 100.0, "ema_50": 99.0,
                "atr_14": 2.0}]

    def fake_tail(_raw, tf, n=2):
        return {"5m": rows_5m, "1h": rows_1h, "4h": rows_4h}[tf][-n:]

    monkeypatch.setattr(trader, "_closed_indicator_tail", fake_tail)
    result = trader._fast_reduce_numeric_diagnostics(
        "long", {}, 100.9, 100.0, stop_loss_pct=1.5)
    assert result["profit_r"] == pytest.approx(0.6)
    assert result["profit_protect_eligible"] is True
    assert result["adverse_last_price"] is False
    assert result["numeric_conditions_met"] is True


def test_profit_protect_reduce_gate_accepts_thesis_intact_when_both_ai_confident():
    diagnostics = {
        "profit_protect_eligible": True, "closed_5m_fresh": True,
        "two_macd_weakening": True, "two_rsi_adverse": True,
    }
    assert trader._profit_protect_reduce_gate(
        gpt_confidence=0.80, gemini_confidence=0.85, diagnostics=diagnostics) is True
