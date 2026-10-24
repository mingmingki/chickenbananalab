"""v33-44 CORE reduce immutable request/order/fill/ledger + exact bill chain."""
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
import json
import pytest
import core_reduce_settlement_v33_44 as m


def sample():
    return dict(type="reduce",strategy_group="core",symbol="ETH/USDT:USDT",
        time="2026-10-18T14:00:00",execution_id="cr123",
        pnl_source="order_fills",amount=0.09,pnl=2.1,fee=0.1,
        close_price=102.5,okx_net_pnl=2.0,
        close_execution_evidence=dict(order_id="ORDER123",trade_ids=["FILL1"],
            filled_contracts=.09,vwap=102.5,requested_contracts=.12,
            exchange_order_contracts=.12,
            first_fill_timestamp_ms=1792300000000,
            last_fill_timestamp_ms=1792300000000,
            price_basis="verified_order_fills"))


def test_request_exchange_fill_journal_chain():
    out=m.evidence_row(sample(),3)
    assert out["quantity_chain"]=="REQUEST_EXCHANGE_FILL_JOURNAL_PARITY"


def test_original_request_absence_not_guessed():
    row=sample()
    del row["close_execution_evidence"]["requested_contracts"]
    assert m.evidence_row(row,3)["quantity_chain"]=="REQUEST_SIZE_NOT_RECORDED"


@pytest.mark.parametrize("field,value",[
    ("requested_contracts",0.05),("exchange_order_contracts",0.11)])
def test_size_inconsistency_explicit(field,value):
    row=sample()
    row["close_execution_evidence"][field]=value
    assert m.evidence_row(row,3)["quantity_chain"]=="QUANTITY_MISMATCH"


def test_invalid_receipt_never_upgraded():
    row=sample()
    row["amount"]=0.12
    assert m.evidence_row(row,3)["quantity_chain"]=="PRIOR_RECEIPT_UNVERIFIED"


class StubExchange:
    def __init__(self):
        self.order=dict(id="ORDER123",info=dict(ordId="ORDER123"),status="closed",
            amount=0.12,filled=0.09)
    def fetch_order(self,oid,sym):
        assert oid=="ORDER123" and sym=="ETH/USDT:USDT"
        return self.order


def mock_trade():
    return {"id":"FILL1","amount":0.09,"price":102.5,
            "fee":{"currency":"USDT","cost":0.1},"info":{"ordId":"ORDER123"}}


def mock_bill():
    return {"type":"2","ccy":"USDT","instId":"ETH-USDT-SWAP",
            "ordId":"ORDER123","tradeId":"FILL1","sz":"0.09","fee":"-0.1"}


def install_stubs(monkeypatch,bill=None):
    monkeypatch.setattr(m,"precise_trades",
        lambda *a,**k: {"FILL1":mock_trade()})
    monkeypatch.setattr(m,"fetch_bills",
        lambda *a,**k: ({"BILL1":mock_bill() if bill is None else bill},2))


def test_independent_order_fill_and_exact_fee_bill(monkeypatch):
    install_stubs(monkeypatch)
    row=sample()
    out=m.verify_settlement(row,m.evidence_row(row,0),
        SimpleNamespace(exchange=StubExchange()))
    assert out["settlement"]=="ORDER_FILLS_AND_EXACT_FEE_BILLS_VERIFIED"
    assert out["verified_fill_contracts"]=="0.09"
    assert out["verified_bill_ids"]==["BILL1"]
    assert out["verified_bill_fee_usdt"]=="0.1"


def test_other_trade_id_bill_does_not_count(monkeypatch):
    bad=mock_bill()
    bad["tradeId"]="OTHER"
    install_stubs(monkeypatch,bad)
    row=sample()
    out=m.verify_settlement(row,m.evidence_row(row,0),
        SimpleNamespace(exchange=StubExchange()))
    assert out["settlement"]=="INDEPENDENT_PROOF_UNCONFIRMED"
    assert out["settlement_reason"]=="FILL_BILL_MISSING_OR_DUPLICATED"


def test_bill_quantity_disagreement_not_accepted(monkeypatch):
    bad=mock_bill()
    bad["sz"]="0.08"
    install_stubs(monkeypatch,bad)
    row=sample()
    out=m.verify_settlement(row,m.evidence_row(row,0),
        SimpleNamespace(exchange=StubExchange()))
    assert out["settlement_reason"]=="BILL_FILL_QUANTITY_MISMATCH"


def test_fee_mismatch_not_accepted(monkeypatch):
    bad=mock_bill()
    bad["fee"]="-0.2"
    install_stubs(monkeypatch,bad)
    row=sample()
    out=m.verify_settlement(row,m.evidence_row(row,0),
        SimpleNamespace(exchange=StubExchange()))
    assert out["settlement_reason"]=="BILL_JOURNAL_FEE_MISMATCH"


def test_exchange_order_amount_mismatch_not_accepted(monkeypatch):
    install_stubs(monkeypatch)
    e=StubExchange()
    e.order["amount"]=.11
    row=sample()
    out=m.verify_settlement(row,m.evidence_row(row,0),
        SimpleNamespace(exchange=e))
    assert out["settlement_reason"]=="ORIGINAL_REQUEST_ORDER_MISMATCH"


def test_offline_audit_never_calls_exchange(tmp_path):
    path=tmp_path/"trades_log.jsonl"
    path.write_text(json.dumps(sample())+"\n")
    def forbidden(_): raise AssertionError("GET must not run")
    report=m.audit(tmp_path,forbidden,max_online=0)
    assert report["online_checked"]==0
    assert report["total_core_reduce_rows"]==1
    assert report["settlement_status_counts"]=={"NOT_CHECKED":1}
    m.save(tmp_path/m.FILE,report)
    assert m.read_status(tmp_path)["status"]=="KNOWN"
    path.write_text(path.read_text()+json.dumps(sample())+"\n")
    assert m.read_status(tmp_path)["status"]=="STALE"


def test_no_execution_or_ledger_writes_in_auditor():
    src=Path(m.__file__).read_text()
    for forbidden in ("create_order(", "record_reduce(", "record_close(",
                      "systemctl restart", "create_market"):
        assert forbidden not in src
    trader=Path("trader.py").read_text()
    assert trader.count("requested_contracts=reduce_contracts")==1
    assert trader.count('requested_contracts=pending["contracts"]')==1
    assert trader.count("def _reduce_exact_evidence(")==1
