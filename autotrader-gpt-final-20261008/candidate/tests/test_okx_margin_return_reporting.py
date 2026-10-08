from pathlib import Path
from types import SimpleNamespace

import web_app


def _empty_period_row(period="2026-09-19"):
    return {
        "period": period, "count": 1, "total_pnl": 10.0,
        "total_fee": 0.5, "net_pnl": 9.5, "win_rate": 100.0,
    }


def test_periodic_api_uses_historical_fixed_margin_return(monkeypatch, tmp_path):
    state = SimpleNamespace(snapshot=lambda: {"baseline_equity": 1293.65})
    ctx = SimpleNamespace(dir=str(tmp_path), state=state)
    monkeypatch.setattr(web_app, "get_context", lambda _u: ctx)
    monkeypatch.setattr(
        web_app.trade_log, "period_breakdown",
        lambda *_a, **_k: {
            "daily": [_empty_period_row()],
            "monthly": [_empty_period_row("2026-09")],
            "yearly": [_empty_period_row("2026")],
        },
    )
    margin = {
        "complete": True, "last_error": None, "last_refresh_ms": 123,
        "order_count": 20, "anomaly_count": 0,
        "periods": {"daily": {}, "monthly": {}, "yearly": {}},
    }
    monkeypatch.setattr(web_app.okx_margin_return, "cached_summary", lambda _u: margin)
    close_rows = [{
        "time": "2026-09-19T10:00:00", "pnl": 10.0,
        "fixed_margin_usdt": 100.0, "_group": "core",
    }]
    monkeypatch.setattr(web_app.pnl_reconciliation, "load_all_records", lambda _u: close_rows)
    monkeypatch.setattr(
        web_app.okx_margin_return, "enrich_trade_records",
        lambda _u, rows: (rows, {"matched_count": 1, "record_count": 1}),
    )

    client = web_app.app.test_client()
    with client.session_transaction() as sess:
        sess["authenticated"] = True
        sess["username"] = "tester"
    res = client.get("/api/stats/periodic")
    assert res.status_code == 200
    day = res.get_json()["periods"]["daily"][0]
    assert day["fixed_return_pct"] == 10.0
    assert day["fixed_margin_match_count"] == 1


def test_dashboard_uses_fixed_margin_not_turnover_margin():
    text = Path("templates/dashboard.html").read_text(encoding="utf-8")
    assert "고정금 대비 손익률(%)" in text
    assert "각 거래의 화면 손익 ÷ 그 거래의 OKX 실제 진입 증거금" in text
    render = text[text.index("function renderPeriodTable"):text.index("async function refreshPeriodicStats")]
    assert "r.fixed_return_pct" in render
    assert "grandOkxNet / grandMargin * 100" not in render
    assert "netPnl / baseline * 100" not in render


def test_filtered_api_exposes_fixed_margin_periods_for_any_filter(monkeypatch, tmp_path):
    state = SimpleNamespace(snapshot=lambda: {"baseline_equity": 1293.65})
    ctx = SimpleNamespace(dir=str(tmp_path), state=state)
    monkeypatch.setattr(web_app, "get_context", lambda _u: ctx)
    rows = [{
        "time": "2026-09-19T10:00:00", "pnl": 10.0,
        "fixed_margin_usdt": 100.0, "_group": "core",
    }]
    monkeypatch.setattr(web_app.pnl_reconciliation, "load_all_records_with_reduces", lambda _u: rows)
    monkeypatch.setattr(web_app.pnl_reconciliation, "load_all_records", lambda _u: rows)
    monkeypatch.setattr(web_app.pnl_reconciliation, "filter_records", lambda records, **_k: records)
    monkeypatch.setattr(web_app.pnl_reconciliation, "period_breakdown_full", lambda *_a, **_k: {
        "daily": [], "monthly": [], "yearly": []
    })
    match = {"complete": True, "record_count": 1, "matched_count": 1, "unmatched_count": 0}
    monkeypatch.setattr(web_app.okx_margin_return, "enrich_trade_records", lambda _u, records: (records, match))
    expected = {
        "complete": True, "last_error": None, "last_refresh_ms": 1,
        "order_count": 2, "anomaly_count": 0,
        "periods": {"daily": {}, "monthly": {}, "yearly": {}},
    }
    monkeypatch.setattr(web_app.okx_margin_return, "cached_summary", lambda _u: expected)

    client = web_app.app.test_client()
    with client.session_transaction() as sess:
        sess["authenticated"] = True
        sess["username"] = "tester"
    res = client.get("/api/trades_filtered?group=core&symbol=all&variant=all")
    assert res.status_code == 200
    data = res.get_json()
    assert data["fixed_margin_periods"]["daily"]["2026-09-19"]["fixed_return_pct"] == 10.0
    assert data["fixed_margin_total"]["fixed_return_pct"] == 10.0
