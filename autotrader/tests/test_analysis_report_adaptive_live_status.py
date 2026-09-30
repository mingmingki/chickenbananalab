import datetime as dt

import analysis_report
import learning_shadow
import web_app

KST = dt.timezone(dt.timedelta(hours=9))


def _base_snapshot():
    return {
        "release": "r", "period": "all",
        "accounting_basis": "completed_lifecycle_economic_v1",
        "coverage": {}, "overall": {}, "symbols": [], "sides": [],
        "top_positive": [], "top_negative": [], "tf": [], "confidence": [],
        "self_learning": {"state_counts": {}}, "positions": [],
        "system_issues": {}, "review": {}, "filter_counterfactual": {},
        "exit_reentry": {}, "candidate_c_breakout_shadow": {},
        "settings": {
            "core_order_mode": "AUTO_ALL", "position_fixed_usdt": 400.0,
            "risk_per_trade_pct": 1.0, "leverage": 5,
            "core_exit_mode": "AUTO", "adaptive_exit_mode": "LIVE_BOUNDED",
            "stop_loss_pct": 2.0, "take_profit_pct": 4.0,
            "max_daily_loss_pct": 5.0, "min_confidence": 0.6,
        },
    }


def test_learning_shadow_matches_aware_decision_to_legacy_naive_kst_lifecycle():
    decision = {"timestamp": "2026-09-25T11:09:07+09:00", "symbol": "XRP/USDT:USDT", "side": "long", "baseline_order_executed": True, "upstream_blocked": False}
    life = {"coverage": "complete", "symbol": "XRP/USDT:USDT", "side": "long", "entry_time": "2026-09-25T11:10:00", "exit_time": "2026-09-25T11:20:00", "trade_id": "x"}
    assert learning_shadow._match(decision, [life]) is life


def test_report_auto_mode_labels_adaptive_and_risk_as_authoritative():
    snap = _base_snapshot()
    text = analysis_report.build_report(snap, now=dt.datetime(2026, 9, 26, 20, 0, tzinfo=KST))["text"]
    assert "주문 계산 방식: 전체 자동계산" in text
    assert "거래당 위험: 1.00%" in text
    assert "SL/TP: Adaptive 자동계산 (LIVE_BOUNDED)" in text
    assert "수동 fallback" not in text


def test_report_renders_recent_adaptive_plan_and_live_era_metrics():
    snap = _base_snapshot()
    snap["adaptive_live_performance"] = {
        "since": "2026-09-26T08:31:02+09:00", "count": 3,
        "gross_pnl": 4.0, "fees": -1.2, "net_adjustment": -0.3,
        "net_pnl": 2.5, "win_rate": 66.7, "profit_factor": 1.4,
    }
    snap["recent_adaptive_plans"] = [{
        "symbol": "PI/USDT:USDT", "side": "long", "entry_allowed": True,
        "stop_price": 0.08397, "tp1_price": 0.10295, "tp2_price": 0.11433,
        "effective_notional": 327.83, "planned_loss_usdt": 27.18,
        "reason_code": "ok",
    }]
    text = analysis_report.build_report(snap, now=dt.datetime(2026, 9, 26, 20, 0, tzinfo=KST))["text"]
    assert "Adaptive LIVE 이후 성과" in text
    assert "2026-09-26T08:31:02+09:00" in text
    assert "거래 3건" in text and "Net +2.50" in text
    assert "최근 Adaptive 계산" in text
    assert "PI/USDT:USDT LONG" in text
    assert "planned risk 27.18 USDT" in text


def test_adaptive_live_performance_filters_by_entry_time_and_uses_economic_metrics(monkeypatch):
    rows = [
        {"entry_time": "2026-09-26T08:30:00+09:00", "exit_time": "2026-09-26T09:30:00+09:00", "net_pnl": 100, "gross_pnl": 100, "fee": 0, "coverage": "complete"},
        {"entry_time": "2026-09-26T09:00:00+09:00", "exit_time": "2026-09-26T09:30:00+09:00", "net_pnl": 7, "gross_pnl": 10, "fee": 2, "coverage": "complete"},
        {"entry_time": "2026-09-26T10:00:00+09:00", "exit_time": "2026-09-26T10:30:00+09:00", "net_pnl": -3, "gross_pnl": -2, "fee": 1, "coverage": "complete"},
    ]
    monkeypatch.setattr(web_app.trade_learning_lifecycle, "build_completed_lifecycles", lambda _u: rows)
    out = web_app._adaptive_live_performance("/tmp/u", "2026-09-26T08:31:02+09:00")
    assert out["count"] == 2
    assert out["net_pnl"] == 4
    assert out["gross_pnl"] == 8
    assert out["fees"] == -3


def test_periodic_review_reports_branch_health_and_root_error(monkeypatch):
    rows = [{
        "review_window_id": "20260926-12", "status": "partial",
        "gemini": {"status": "ok", "review": {"observations": ["G"]}},
        "gpt": {"status": "error", "error": "ReadTimeout", "review": None},
        "consensus": {"status": "incomplete"},
    }]
    monkeypatch.setattr(web_app.ai_strategy_review_log, "recent", lambda _u, limit=30: rows)
    out = web_app._analysis_report_review("/tmp/u")
    assert out["kind"] == "scheduled_strategy_review"
    assert out["success_count"] == 1 and out["attempt_count"] == 2
    assert out["gemini_status"] == "ok" and out["gpt_status"] == "error"
    assert out["gpt_error"] == "ReadTimeout"

    rows[:] = [{"review_window_id": "20260926-12", "status": "error", "error": "TypeError"}]
    out = web_app._analysis_report_review("/tmp/u")
    assert out["success_count"] == 0
    assert out["root_error"] == "TypeError"
