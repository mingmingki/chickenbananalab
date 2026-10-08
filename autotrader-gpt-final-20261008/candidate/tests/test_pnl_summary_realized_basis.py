from pathlib import Path
from types import SimpleNamespace
import web_app


def _agg(net=0.0):
    return {"count":0,"completed_count":0,"realized_event_count":0,"gross_pnl":net,
            "fee":0.0,"net_adjustment":0.0,"net_pnl":net,"win_rate":None,"profit_factor":None}


def test_pnl_summary_defaults_to_reduce_inclusive_realized_basis(monkeypatch,tmp_path):
    state=SimpleNamespace(snapshot=lambda:{"baseline_equity":1000.0,"capital_flow_summary":None,"symbols":{}})
    cfg=SimpleNamespace(CANDIDATE_C_SYMBOLS=[])
    ctx=SimpleNamespace(dir=str(tmp_path),state=state,cfg=cfg)
    monkeypatch.setattr(web_app,"get_context",lambda _u:ctx)
    close={"total":_agg(5),"core":_agg(5),"candidate_c":_agg(),"fast":_agg(),"legacy":_agg()}
    realized={"total":_agg(-15),"core":_agg(-15),"candidate_c":_agg(),"fast":_agg(),"legacy":_agg()}
    monkeypatch.setattr(web_app.pnl_reconciliation,"summary",lambda _u:close)
    monkeypatch.setattr(web_app.account_reconciliation_bridge,"realized_economic_summary",lambda _u:realized)
    monkeypatch.setattr(web_app.pnl_reconciliation,"verify_reconciliation",lambda _u:True)
    monkeypatch.setattr(web_app.pnl_reconciliation,"group_contribution_pct",lambda *_a:{})
    monkeypatch.setattr(web_app.pnl_reconciliation,"period_breakdown_by_group",lambda _u:{})
    monkeypatch.setattr(web_app.exchange_fee_ledger,"cached_summary",lambda _u:{"complete":True,"events":[]})
    monkeypatch.setattr(web_app.exchange_fee_ledger,"load_state",lambda _u:{"events":[]})
    monkeypatch.setattr(web_app.exchange_funding_ledger,"cached_summary",lambda *_a,**_k:{"complete":True,"funding_pnl_usdt":-1.25})
    monkeypatch.setattr(web_app.account_reconciliation_bridge,"build",lambda *_a,**_k:{"complete":False})
    client=web_app.app.test_client()
    with client.session_transaction() as sess:
        sess["authenticated"]=True; sess["username"]="tester"
    data=client.get("/api/pnl_summary").get_json()
    assert data["summary"]["total"]["net_pnl"] == -15
    assert data["close_only_summary"]["total"]["net_pnl"] == 5
    assert data["accounting_basis"] == "realized_events_close_plus_reduce_v1"
    assert data["funding_summary"]["funding_pnl_usdt"] == -1.25


def test_dashboard_labels_realized_basis_and_unreconciled_gap():
    text=Path("templates/dashboard.html").read_text(encoding="utf-8")
    assert "실현 Net (REDUCE 포함)" in text
    assert "완료승률" in text
    assert "계좌↔봇 저널 미대사 차이" in text
    assert "실제 funding" in text
    assert "수수료 원장-저널 차이" in text
    assert "설명되지 않은 잔차" not in text

