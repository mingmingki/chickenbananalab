import datetime as dt

import analysis_report
import web_app

KST = dt.timezone(dt.timedelta(hours=9))


def test_adaptive_live_performance_uses_entry_time_not_exit_time(monkeypatch):
    rows = [
        {"entry_time":"2026-09-26T07:00:00+09:00","exit_time":"2026-09-26T09:00:00+09:00","net_pnl":-20.0,"gross_pnl":-18.0,"fee":2.0,"final_close_reason":"legacy_close"},
        {"entry_time":"2026-09-26T09:00:00+09:00","exit_time":"2026-09-26T10:00:00+09:00","net_pnl":-3.0,"gross_pnl":-2.0,"fee":1.0,"final_close_reason":"position_ai_close_all"},
        {"entry_time":"2026-09-26T10:30:00+09:00","exit_time":"2026-09-26T11:00:00+09:00","net_pnl":5.0,"gross_pnl":6.0,"fee":1.0,"final_close_reason":"adaptive_tp"},
    ]
    monkeypatch.setattr(web_app.trade_learning_lifecycle, "build_completed_lifecycles", lambda _u: rows)
    out = web_app._adaptive_live_performance("/tmp/u", "2026-09-26T08:31:02+09:00")
    assert out["count"] == 2
    assert out["net_pnl"] == 2.0
    assert {r["reason"]: (r["count"], r["net_pnl"]) for r in out["exit_reasons"]} == {
        "position_ai_close_all": (1, -3.0),
        "adaptive_tp": (1, 5.0),
    }


def test_adaptive_live_report_renders_entry_basis_and_exit_reason_breakdown():
    snap = {
        "adaptive_live_performance": {
            "since":"2026-09-26T08:31:02+09:00","basis":"entry_time",
            "count":2,"gross_pnl":4.0,"fees":-2.0,"net_adjustment":0.0,
            "net_pnl":2.0,"win_rate":50.0,"profit_factor":1.67,
            "exit_reasons":[
                {"reason":"position_ai_close_all","count":1,"net_pnl":-3.0},
                {"reason":"adaptive_tp","count":1,"net_pnl":5.0},
            ],
        },
        "coverage":{},"overall":{},"symbols":[],"sides":[],"top_positive":[],"top_negative":[],"tf":[],"confidence":[],
        "self_learning":{},"review":{},"settings":{},"positions":[],"system_issues":{},"filter_counterfactual":{},"exit_reentry":{},"candidate_c_breakout_shadow":{},
    }
    text = analysis_report.build_report(snap, now=dt.datetime(2026,9,27,0,45,tzinfo=KST))["text"]
    assert "진입시각 기준" in text
    assert "청산사유별:" in text
    assert "position_ai_close_all | 1건 | Net -3.00" in text
    assert "adaptive_tp | 1건 | Net +5.00" in text
