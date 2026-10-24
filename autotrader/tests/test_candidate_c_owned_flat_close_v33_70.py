"""v33-70 exact owned reduce-only close attestation; all tests are offline."""
import copy
from types import SimpleNamespace
import pytest
import candidate_c_owned_flat_close_v33_70 as proof


def case():
    entry = SimpleNamespace(kind="EntryIntent", state="terminal", intent_id="ENTRY",
        requested_side="long", filled_quantity=0.7)
    closing = SimpleNamespace(kind="ReversalIntent", state="terminal", intent_id="CLOSE",
        requested_side="long", requested_quantity=0.7, exchange_order_id="4025646808464871424",
        position_epoch="ENTRY:epoch123")
    records = [entry, closing]
    ledger = SimpleNamespace(records=lambda:records)
    receipt = dict(type="close",symbol="SOL/USDT:USDT",strategy_group="candidate_c",
        execution_id="CLOSE",reason="MANUAL_OR_EXTERNAL_CLOSE",pnl_source="okx_realized",
        amount=0.7,side="long",time="2026-10-20T15:03:28")
    order = dict(id="4025646808464871424",status="closed",side="sell",
        filled=0.7,amount=0.7,remaining=0,info={"reduceOnly":"true"})
    trades = [
        dict(id="TRADE_A",order="4025646808464871424",side="sell",amount=0.56),
        dict(id="TRADE_B",order="4025646808464871424",side="sell",amount=0.14)]
    exchange = SimpleNamespace(fetch_order=lambda oid,symbol:order)
    client = SimpleNamespace(exchange=exchange,
        fetch_trades_for_order=lambda oid,since_ms,limit:trades)
    return entry, closing, records, receipt, order, trades, client, ledger


def run(case):
    e,c,recs,receipt,order,trades,client,ledger = case
    return proof.verify(client,"SOL/USDT:USDT",ledger,e,[receipt])


def test_exact_two_fills_full_reduce_only():
    facts,reason=run(case())
    assert reason=="VERIFIED_OWNED_CLOSE"
    assert facts["external_close_qty"]=="0.7"
    assert facts["trade_ids"]==["TRADE_A","TRADE_B"]
    assert facts["receipt_type"]=="OWNED_REDUCE_ONLY_CLOSE_V33_70"


@pytest.mark.parametrize("field,value",[
    ("status","open"),("side","buy"),("filled",0.69),("amount",0.71),
    ("remaining",0.01),("id","bad")])
def test_never_accept_bad_exchange_order(field,value):
    x=case()
    x[4][field]=value
    assert run(x)[0] is None


@pytest.mark.parametrize("field,value",[
    ("amount",0.69),("reason","unrelated"),("pnl_source","estimated"),
    ("execution_id","WRONG"),("side","short"),("symbol","DOGE/USDT:USDT")])
def test_never_accept_bad_close_receipt(field,value):
    x=case()
    x[3][field]=value
    assert run(x)[0] is None


@pytest.mark.parametrize("field,value",[
    ("order","DIFFERENT_ORDER"),("side","buy"),("amount",0),
    ("id","TRADE_A")])
def test_never_accept_bad_fill_identity(field,value):
    x=case()
    x[5][1][field]=value
    assert run(x)[0] is None


@pytest.mark.parametrize("field,value",[
    ("state","open"),("position_epoch","wrong"),("requested_quantity",0.72),
    ("requested_side","short"),("exchange_order_id","")])
def test_never_accept_invalid_owned_reversal(field,value):
    x=case()
    setattr(x[1],field,value)
    assert run(x)[0] is None


def test_not_last_intent_does_not_attest():
    x=case()
    x[2].append(SimpleNamespace(kind="OtherIntent"))
    assert run(x)[0] is None


def test_duplicate_trade_ids_not_allowed():
    x=case()
    x[5][1]["id"]="TRADE_A"
    assert run(x)[0] is None


def test_duplicate_close_receipts_not_allowed():
    e,c,records,receipt,order,trades,client,ledger=case()
    assert proof.verify(client,"SOL/USDT:USDT",ledger,e,[receipt,copy.copy(receipt)])[0] is None


def test_api_failure_fails_closed():
    e,c,records,receipt,order,trades,client,ledger=case()
    def unavailable(*args):
        raise TimeoutError()
    client.exchange.fetch_order=unavailable
    assert proof.verify(client,"SOL/USDT:USDT",ledger,e,[receipt])[0] is None


def test_fetch_fill_limit_treats_100_as_truncated():
    x=case()
    x[5].extend(dict(id=str(i),order="4025646808464871424",
           side="sell",amount=.001) for i in range(98))
    assert run(x)[0] is None


def test_not_a_general_reconciliation_bypass():
    from pathlib import Path
    source=Path(proof.__file__).read_text()
    assert "reduceOnly" in source
    for dangerous in ("create_order(", "cancel_order(", "mark_terminal(",
                      "mark_protected(", "set_entry_pause(", "save_json_atomic("):
        assert dangerous not in source
