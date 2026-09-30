import datetime as dt
from types import SimpleNamespace

import core_reentry_thesis
import trader

SYMBOL = "XRP/USDT:USDT"

class State:
    def __init__(self): self.data = {}
    def update_symbol(self, symbol, **kwargs): self.data.setdefault(symbol, {}).update(kwargs)


def test_opposite_side_thesis_is_reported_as_allowed_not_blocked(tmp_path):
    now = dt.datetime(2026, 9, 27, 0, 57, 0)
    core_reentry_thesis.record_ai_close(str(tmp_path), SYMBOL, "short", now-dt.timedelta(hours=7), "thesis_intact", 30)
    state = State()
    cfg = SimpleNamespace(user_dir=str(tmp_path), logger=SimpleNamespace(error=lambda *a, **k: None))
    result = trader._evaluate_ai_close_thesis_entry_gate(cfg, state, SYMBOL, "long", {}, now=now)
    assert result["blocked"] is False
    diag = state.data[SYMBOL]
    assert diag["reentry_thesis_status"] == "opposite_side_allowed"
    assert diag["reentry_thesis_blocked_side"] == "short"


def test_auto_risk_settings_summary_does_not_claim_manual_sl_tp():
    cfg = SimpleNamespace(CORE_EXIT_MODE="AUTO", ADAPTIVE_EXIT_MODE="LIVE_BOUNDED", RISK_PER_TRADE_PCT=1.0,
                          STOP_LOSS_PCT=2.0, TAKE_PROFIT_PCT=4.0, MAX_DAILY_LOSS_PCT=5.0, LEVERAGE=5)
    text = trader._risk_settings_summary(cfg)
    assert "SL/TP=Adaptive 자동계산(LIVE_BOUNDED)" in text
    assert "SL=2.0%" not in text and "TP=4.0%" not in text


def test_manual_risk_settings_summary_keeps_manual_sl_tp():
    cfg = SimpleNamespace(CORE_EXIT_MODE="MANUAL", ADAPTIVE_EXIT_MODE="LIVE_BOUNDED", RISK_PER_TRADE_PCT=1.0,
                          STOP_LOSS_PCT=2.0, TAKE_PROFIT_PCT=4.0, MAX_DAILY_LOSS_PCT=5.0, LEVERAGE=5)
    text = trader._risk_settings_summary(cfg)
    assert "SL=2.0%" in text and "TP=4.0%" in text


def test_dashboard_distinguishes_opposite_side_allowed():
    from pathlib import Path
    text = (Path(__file__).resolve().parents[1] / "templates" / "dashboard.html").read_text()
    assert "opposite_side_allowed" in text
    assert "reentry_thesis_blocked_side" in text
    assert "재진입만 차단" in text
