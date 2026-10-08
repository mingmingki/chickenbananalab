from pathlib import Path
from types import SimpleNamespace

import web_app


def test_pnl_summary_exposes_cached_okx_actual_fees(monkeypatch, tmp_path):
    state = SimpleNamespace(snapshot=lambda: {"baseline_equity": 1000.0})
    ctx = SimpleNamespace(dir=str(tmp_path), state=state)
    monkeypatch.setattr(web_app, "get_context", lambda _u: ctx)
    empty = {
        "count":0, "gross_pnl":0.0, "fee":0.0, "net_pnl":0.0,
        "win_rate":None, "profit_factor":None,
    }
    summary = {
        "total":dict(empty), "core":dict(empty), "candidate_c":dict(empty),
        "fast":dict(empty), "legacy":dict(empty),
    }
    monkeypatch.setattr(web_app.pnl_reconciliation, "summary", lambda _u: summary)
    monkeypatch.setattr(web_app.pnl_reconciliation, "verify_reconciliation", lambda _u: True)
    monkeypatch.setattr(web_app.pnl_reconciliation, "group_contribution_pct", lambda *_a: {})
    monkeypatch.setattr(web_app.pnl_reconciliation, "period_breakdown_by_group", lambda _u: {})
    expected = {
        "complete":True, "swap_fee_usdt":107.29, "spot_fee_usdt":2.06,
        "start_ms":1, "last_refresh_ms":2,
    }
    monkeypatch.setattr(web_app.exchange_fee_ledger, "cached_summary", lambda _u: expected)

    client = web_app.app.test_client()
    with client.session_transaction() as sess:
        sess["authenticated"] = True
        sess["username"] = "tester"
    res = client.get("/api/pnl_summary")
    assert res.status_code == 200
    assert res.get_json()["exchange_fee_summary"] == expected


def test_dashboard_distinguishes_actual_exchange_fee_from_allocated_trade_fee():
    text = Path("templates/dashboard.html").read_text(encoding="utf-8")
    assert "OKX 실제 선물 수수료" in text
    assert "현물 전환 수수료" in text
    assert "저널 배분 수수료" in text
    assert "진입·청산·부분감축(REDUCE)" in text
    assert 'document.getElementById("okx-fee-line").textContent' in text
    assert 'document.getElementById("okx-fee-hint").textContent' in text
    assert "실현거래 배분 수수료" in text
