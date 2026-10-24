"""v33-34 legacy, delayed opening and exact bills regression."""
import json
from decimal import Decimal as D
from datetime import datetime,timezone,timedelta
from pathlib import Path
from types import SimpleNamespace
import pytest

import core_position_pnl_audit_v33_34 as pos
import core_missing_entry_recovery_v33_34 as missing
import core_exact_bill_audit_v33_34 as bill

KST=timezone(timedelta(hours=9))
CREATED=int(datetime(2026,10,16,10,0,tzinfo=KST).timestamp()*1000)
SYMBOL="ETH/USDT:USDT"

def fixture():
    opened=dict(type="open",strategy_group="core",dry_run=False,
        symbol=SYMBOL,side="short",amount=.55,price=2450.,
        execution_id="core-entry:ETH/USDT:USDT-202610161000001234",
        time="2026-10-16T10:05:12")
    closed=dict(type="close",strategy_group="core",dry_run=False,
        symbol=SYMBOL,side="short",amount=.55,
        execution_id=f"core-external-close:pos:{CREATED}:short",
        time="2026-10-16T10:18:00",
        pnl=2.2,fee=.2,funding_fee=0,okx_net_pnl=2.0)
    oco=[dict(cTime=str(CREATED),algoId="algo",side="buy",state="effective")]
    history=[dict(info=dict(posId="pos",cTime=str(CREATED),
        pnl="2.2",fee="-0.2",fundingFee="0",
        realizedPnl="2.0",closeTotalPos="0.55"))]
    client=SimpleNamespace(exchange=SimpleNamespace(
        market=lambda sym:dict(contractSize=.1)))
    entry_order=dict(filled=5.5,average=2450.,
        info=dict(ordId="order-entry"))
    return [opened,closed],oco,history,client,entry_order

def test_delayed_open_is_proved_by_embedded_id_contract_size_and_price(monkeypatch):
    rows,oco,hist,client,entry=fixture()
    monkeypatch.setattr(missing.chain,"find_entry",
        lambda *args,**kwargs:(("order-entry",entry),None))
    result=missing.proof_one(rows,dict(close_index=1),
        client,hist,oco)
    assert result["status"]=="MATCH"
    assert result["delayed_open"]["index"]==0

@pytest.mark.parametrize("field,value",[
    ("amount",.60),("price",2600.0),
    ("execution_id","not-an-owned-core-entry")])
def test_delayed_open_false_evidence_is_rejected(monkeypatch,field,value):
    rows,oco,hist,client,entry=fixture()
    rows[0][field]=value
    monkeypatch.setattr(missing.chain,"find_entry",
        lambda *args,**kwargs:(("order-entry",entry),None))
    result=missing.proof_one(rows,dict(close_index=1),client,hist,oco)
    assert result["status"]=="EXCLUDED"

def fake_fee_proof():
    return dict(close_index=1,symbol=SYMBOL,created_ms=CREATED,
        entry_order_id="entry",close_order_id="exit",
        trade_rows=[dict(index=1)],
        exchange_money=dict(fee=".4"),journal_money=dict(fee=".4"))

def bill_row(order,tid,bid):
    return dict(type="2",ordId=order,tradeId=tid,
        billId=bid,fee="-.2",ccy="USDT")

def test_exact_order_trade_fee_bills(monkeypatch):
    proof=fake_fee_proof()
    monkeypatch.setattr(bill,"precise_trades",
        lambda client,orderid,ms,expected_ids=None:{
            "fill-"+orderid:dict(id="fill-"+orderid)})
    receipts={
        "b1":bill_row("entry","fill-entry","b1"),
        "b2":bill_row("exit","fill-exit","b2")}
    result=bill.verify_bill_fees(proof,[{},{}],None,receipts)
    assert result["status"]=="MATCH"
    assert result["matched_bill_count"]==2

def test_missing_bill_fails_closed(monkeypatch):
    monkeypatch.setattr(bill,"precise_trades",
        lambda client,orderid,ms,expected_ids=None:{
            "fill-"+orderid:dict(id="fill-"+orderid)})
    with pytest.raises(ValueError,match="FILL_BILL_MISSING"):
        bill.verify_bill_fees(fake_fee_proof(),[{},{}],None,{
            "b1":bill_row("entry","fill-entry","b1")})

def test_duplicate_bill_for_same_trade_fails_closed(monkeypatch):
    monkeypatch.setattr(bill,"precise_trades",
        lambda client,orderid,ms,expected_ids=None:{
            "fill-"+orderid:dict(id="fill-"+orderid)})
    with pytest.raises(ValueError,match="DUPLICATED"):
        bill.verify_bill_fees(fake_fee_proof(),[{},{}],None,{
            "b1":bill_row("entry","fill-entry","b1"),
            "b2":bill_row("entry","fill-entry","b2"),
            "b3":bill_row("exit","fill-exit","b3")})

def test_fee_bill_scope_is_not_funding_position_ownership():
    source=Path("core_exact_bill_audit_v33_34.py").read_text()
    assert "correlation_only=True" in source
    assert "ordId" in source and "tradeId" in source
    for forbidden in ("create_order(","cancel_order(","amend_protective_stop("):
        assert forbidden not in source

def test_get_only_report_is_authenticated():
    source=Path("web_app.py").read_text()
    route=source.split("@app.route('/api/ops/financial-proof-v33-34'",1)[1]
    route=route.split("@app.route(",1)[0]
    assert "@login_required" in route
    assert "get_context(session['username'])" in route
    assert "fetch_order(" not in route
    assert "create_order(" not in route

def test_v33_34_legacy_group_is_supported_without_modifying_v33_33():
    current=Path("core_position_pnl_audit_v33_34.py").read_text()
    old=Path("core_position_pnl_audit_v33_33.py").read_text()
    assert 'row.get("strategy_group") in (None,"core")' in current
    assert 'row.get("strategy_group")=="core"' in old
