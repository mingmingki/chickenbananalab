"""v33-33 exact position reconciliation regression coverage."""
import json
from decimal import Decimal
from pathlib import Path
import core_position_pnl_audit_v33_33 as x

def fixture_rows():
    first=dict(type="open",dry_run=False,strategy_group="core",
        symbol="XRP/USDT:USDT",side="short",
        time="2026-10-13T10:02:19",price=1.3264)
    reduce=dict(type="reduce",dry_run=False,strategy_group="core",
        symbol=first["symbol"],side="short",
        time="2026-10-13T10:15:45",amount=4.36,
        pnl=-4.1856,fee=.291248,okx_net_pnl=-4.476848,
        execution_id="reduction-unique-1")
    close=dict(type="close",dry_run=False,strategy_group="core",
        symbol=first["symbol"],side="short",
        time="2026-10-13T10:17:25",amount=6.36,
        pnl=-9.7308,fee=1.137611,funding_fee=0,
        okx_net_pnl=-10.868411,
        execution_id="core-external-close:pos:1791853337461:short")
    history=[dict(info=dict(posId="pos",cTime="1791853337461",
        pnl="-13.9164",fee="-1.428859",fundingFee="0",
        realizedPnl="-15.345259",closeTotalPos="10.72"))]
    return [first,reduce,close],history

def test_partial_plus_final_equals_position_history():
    rows,hist=fixture_rows()
    result=x.audited_position(rows,2,hist)
    assert result["status"]=="MATCH"
    assert result["partial_reductions"]==1
    assert result["journal_values"]["net"]=="-15.345259"
    assert result["okx_position_values"]["net"]=="-15.345259"
    assert all(Decimal(v)==0 for v in result["differences"].values())

def test_missing_open_cannot_be_called_reconciled():
    rows,hist=fixture_rows()
    result=x.audited_position(rows[1:],1,hist)
    assert result["status"]=="EXCLUDED"
    assert result["reason"]=="OPEN_NOT_EXACTLY_UNIQUE"

def test_fee_mismatch_detected():
    rows,hist=fixture_rows()
    rows[1]["fee"]+=.1
    result=x.audited_position(rows,2,hist)
    assert result["status"]=="MISMATCH"
    assert "fee" in result["disputed_fields"]
    assert "journal_net_invariant" in result["disputed_fields"]

def test_wrong_position_history_does_not_match():
    rows,hist=fixture_rows()
    hist[0]["info"]["posId"]="different"
    assert x.audited_position(rows,2,hist)["status"]=="EXCLUDED"

def test_duplicate_execution_id_is_not_assumed_unique():
    rows,_=fixture_rows()
    rows[1]["execution_id"]=rows[2]["execution_id"]
    life,reason=x.journal_lifecycle(rows,2)
    assert life is None and reason=="DUPLICATE_EVENT_EXECUTION_ID"

def test_gross_fee_funding_net_cross_consistency():
    rows,hist=fixture_rows()
    rows[2]["funding_fee"]=.11
    rows[2]["okx_net_pnl"]+=.11
    hist[0]["info"]["fundingFee"]="0.11"
    hist[0]["info"]["realizedPnl"]="-15.235259"
    result=x.audited_position(rows,2,hist)
    assert result["status"]=="MATCH"
    assert result["journal_values"]["funding"]=="0.11"

def test_duplicate_closing_path_fails_closed():
    rows,hist=fixture_rows()
    inserted=dict(rows[2],time="2026-10-13T10:16:00")
    altered=rows[:2]+[inserted,rows[2]]
    result=x.audited_position(altered,3,hist)
    assert result["status"]=="EXCLUDED"

def test_report_is_tamper_sensitive(tmp_path):
    rows,hist=fixture_rows()
    (tmp_path/"trades_log.jsonl").write_text("".join(json.dumps(r)+"\n" for r in rows))
    validated=x.audited_position(rows,2,hist)
    record=dict(close_index=2,close_row_sha256=x.hash_row(rows[2]),**validated)
    x.save_atomic(tmp_path/x.FILE,dict(version="v33-33",details=[record]))
    assert x.read_report(tmp_path)["currently_bound_matching_positions"]==1
    rows[1]["pnl"]=-10
    (tmp_path/"trades_log.jsonl").write_text("".join(json.dumps(r)+"\n" for r in rows))
    assert x.read_report(tmp_path)["currently_bound_matching_positions"]==0

def test_report_bills_do_not_fabricate_allocation(tmp_path):
    result=x.bill_state_metadata(tmp_path)
    assert result["exchange_fee_state.json"]["status"]=="MISSING_OR_UNREADABLE"
    assert result["exchange_funding_state.json"]["status"]=="MISSING_OR_UNREADABLE"

def test_no_live_order_or_strategy_authority():
    script=Path("core_position_pnl_audit_v33_33.py").read_text()
    for forbidden in ("create_order(", "cancel_order(", "amend_protective_stop(",
        "gemini_analyzer.", "openai_analyzer."):
        assert forbidden not in script
    assert "may_execute=False" in script


def test_authenticated_get_only_report_route():
    web=Path("web_app.py").read_text()
    section=web.split("@app.route('/api/ops/position-pnl-audit-v33-33'",1)[1]
    section=section.split("@app.route(",1)[0]
    assert "@login_required" in section
    assert "get_context(session['username'])" in section
    assert "reconciliation.read_report(ctx.dir)" in section
    assert "fetch_positions_history(" not in section
    assert "create_order(" not in section
