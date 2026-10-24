"""v33-35 read-only exact fee bills tests."""
import json
from datetime import datetime,timezone,timedelta
from pathlib import Path
from types import SimpleNamespace
import pytest
import core_exact_fee_bills_v33_35 as a

KST=timezone(timedelta(hours=9))
SYM="BTC/USDT:USDT"

def sample():
    row=dict(type="close",symbol=SYM,side="long",
        execution_id="core-external-close:p1:1792220100000:long",
        time="2026-10-17T20:00:00")
    record=dict(close_index=0,symbol=SYM,journal_close_time=row["time"],
        bound_rows=[dict(index=0,sha256=a.positions.hash_row(row))],
        okx_position_values=dict(fee="0.4"),
        journal_values=dict(fee="0.4"))
    return row,record

def test_report_invalidated_when_original_row_changes(tmp_path):
    row,record=sample()
    (tmp_path/"trades_log.jsonl").write_text(json.dumps(row)+"\n")
    a.atomic_save(tmp_path/a.FILE,dict(version="v33-35",
        results=[dict(close_index=0,bound_rows=record["bound_rows"],
            close_sha256=a.positions.hash_row(row),status="MATCH",
            matched_bill_ids=["b1"])]))
    first=a.read_status(tmp_path)
    assert first["bound_matches"]==1
    assert first["bound_fee_bill_ids"]==1
    row["side"]="short"
    (tmp_path/"trades_log.jsonl").write_text(json.dumps(row)+"\n")
    second=a.read_status(tmp_path)
    assert second["bound_matches"]==0
    assert second["invalidated"]==1

def test_bill_query_cache_by_symbol_calendar_day(monkeypatch):
    calls=[]
    def fetch(exchange,symbol,begin,end,**kw):
        calls.append((symbol,begin,end))
        return {str(begin):dict(billId=str(begin))},2
    monkeypatch.setattr(a.bills,"fetch_bills",fetch)
    monkeypatch.setattr(a.time,"sleep",lambda *_:None)
    day=int(datetime(2026,10,16,tzinfo=KST).timestamp()*1000)
    client=SimpleNamespace(exchange=object())
    cache={}
    first=a.bills_for_span(client,SYM,day+3600000,day+3610000,cache)
    second=a.bills_for_span(client,SYM,day+3602000,day+3604000,cache)
    assert len(first)==len(second)==1
    assert len(calls)==1

def test_corrupt_external_fee_ledger_is_not_accepted(tmp_path):
    (tmp_path/"trades_log.jsonl").write_text("{}\n")
    a.atomic_save(tmp_path/a.FILE,dict(version="wrong",results=[]))
    assert a.read_status(tmp_path)["status"]=="INVALID_VERSION"

def test_stored_owned_algo_mismatch_is_rejected():
    row,record=sample()
    algo=dict(cTime="1792220100000",state="effective",side="sell",
              algoId="actual",ordId="close1",ordIdList=["close1"])
    row["close_execution_evidence"]=dict(
        price_basis="verified_owned_protection_fills",
        algo_id="incorrect",order_id="close1",trade_ids=["t1"])
    with pytest.raises(ValueError,match="ALGO_MISMATCH"):
        a.owned_orders(record,[row],None,[algo],{0:{}},{})

def test_oco_without_unique_child_order_is_rejected():
    row,record=sample()
    algo=dict(cTime="1792220100000",state="effective",side="sell",
              algoId="correct",ordId="close1",ordIdList=["close1","extra"])
    row["close_execution_evidence"]=dict(
        price_basis="verified_owned_protection_fills",
        algo_id="correct",order_id="close1",trade_ids=["t1"])
    with pytest.raises(ValueError,match="CLOSE_CHILD_ORDER_NOT_UNIQUE"):
        a.owned_orders(record,[row],None,[algo],{0:{}},{})

def test_cash_report_duplicate_bill_claims_fail_closed(tmp_path):
    row,record=sample()
    second=dict(row,execution_id="core-external-close:p2:1792220100000:long")
    (tmp_path/"trades_log.jsonl").write_text(
        json.dumps(row)+"\n"+json.dumps(second)+"\n")
    records=[]
    for i,value in enumerate((row,second)):
        sha=a.positions.hash_row(value)
        records.append(dict(close_index=i,close_sha256=sha,
            bound_rows=[dict(index=i,sha256=sha)],
            status="MATCH",matched_bill_ids=["same"]))
    a.atomic_save(tmp_path/a.FILE,dict(version="v33-35",results=records))
    assert a.read_status(tmp_path)["status"]=="DUPLICATE_BILL"

def test_failing_missing_provenance_cannot_be_presented_as_verified(monkeypatch):
    monkeypatch.setattr(a.bills,"read_status",lambda root:dict(status="NOT_COLLECTED"))
    with pytest.raises(ValueError,match="OLDER_FEE"):
        a.known_ten_ids("/tmp")

def test_readonly_service_never_contains_order_commands():
    code=Path("core_exact_fee_bills_v33_35.py").read_text()
    assert "create_order(" not in code
    assert "cancel_order(" not in code
    assert "amend_protective_stop(" not in code
    assert "may_execute=False" in code

def test_authenticated_report_route_reads_cached_snapshot_only():
    source=Path("web_app.py").read_text()
    assert "@app.route('/api/ops/exact-fee-bills-v33-35'" in source
    block=source.split("@app.route('/api/ops/exact-fee-bills-v33-35'",1)[1]
    block=block.split("@app.route(",1)[0]
    assert "@login_required" in block
    assert "get_context(session['username'])" in block
    assert "latest_fee.read_status" in block
    assert "fetch_order(" not in block
    assert "create_order(" not in block
