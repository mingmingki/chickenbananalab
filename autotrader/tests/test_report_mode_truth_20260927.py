import analysis_report, trader
from types import SimpleNamespace

def cfg(mode="FIXED_MARGIN_AUTO_EXIT"):
    return SimpleNamespace(CORE_ORDER_MODE=mode, POSITION_FIXED_USDT=400.0, LEVERAGE=5,
        RISK_PER_TRADE_PCT=1.0, CORE_EXIT_MODE="AUTO", ADAPTIVE_EXIT_MODE="LIVE_BOUNDED",
        STOP_LOSS_PCT=2.0, TAKE_PROFIT_PCT=4.0, MAX_DAILY_LOSS_PCT=5.0)

def test_start_log_fixed_auto_reports_fixed_margin_not_risk():
    text=trader._risk_settings_summary(cfg())
    assert "고정 증거금=400.00 USDT" in text
    assert "명목=2000.00 USDT" in text
    assert "RISK_PER_TRADE" not in text
    assert "Adaptive 자동계산" in text

def test_start_log_auto_all_reports_risk():
    text=trader._risk_settings_summary(cfg("AUTO_ALL"))
    assert "거래당 위험=1.0%" in text

def test_analysis_settings_fixed_auto_truth():
    lines=analysis_report._settings_lines({"core_order_mode":"FIXED_MARGIN_AUTO_EXIT","position_fixed_usdt":400.0,
      "leverage":5,"adaptive_exit_mode":"LIVE_BOUNDED","max_daily_loss_pct":5,"min_confidence":.6})
    joined="\n".join(lines)
    assert "고정 증거금: 400.00 USDT" in joined
    assert "예상 명목: 2000.00 USDT" in joined
    assert "risk:" not in joined

def test_analysis_settings_auto_all_truth():
    lines=analysis_report._settings_lines({"core_order_mode":"AUTO_ALL","risk_per_trade_pct":1.0,
      "leverage":5,"adaptive_exit_mode":"LIVE_BOUNDED","max_daily_loss_pct":5,"min_confidence":.6})
    assert any("거래당 위험: 1.00%" in x for x in lines)
