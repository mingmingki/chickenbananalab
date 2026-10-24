"""v33-43 CORE partial-close exact fill receipt parity."""
from pathlib import Path
import core_reduce_receipts_v33_43 as r

def row():
    return dict(type="reduce",strategy_group="core",symbol="ETH/USDT:USDT",
        time="2026-10-18T12:00:00",execution_id="cr123",
        pnl_source="order_fills",amount=0.09,pnl=2.1,fee=0.1,
        close_price=102.5,okx_net_pnl=2.0,
        close_execution_evidence=dict(order_id="ORD1",trade_ids=["FIL1","FIL2"],
            filled_contracts=0.09,vwap=102.5))

def test_parity_validates_order_id_fills_qty_fee_net():
    got=r.evaluate(row())
    assert got["status"]=="EXACT_EXIT_FILL_RECEIPT_PARITY"
    assert got["order_id"]=="ORD1" and got["fill_count"]==2

def test_parity_rejects_planned_qty_mismatch():
    x=row();x["amount"]=0.1
    assert r.evaluate(x)["reason_code"]=="FILLED_JOURNAL_QTY_MISMATCH"

def test_parity_rejects_duplicate_trades():
    x=row();x["close_execution_evidence"]["trade_ids"]=["FIL1","FIL1"]
    assert r.evaluate(x)["reason_code"]=="DUPLICATED_FILL_IDS"

def test_parity_rejects_gross_fee_net_mismatch():
    x=row();x["okx_net_pnl"]=2.15
    assert r.evaluate(x)["reason_code"]=="NET_PNL_INVARIANT_MISMATCH"

def test_legacy_rows_not_treated_as_correct():
    x=row();x.pop("close_execution_evidence")
    assert r.evaluate(x)["status"]=="LEGACY_OR_UNVERIFIED_RECEIPT"

def test_readonly_authenticated_route():
    text=Path("web_app.py").read_text()
    anchor="@app.route('/api/ops/core-reduce-receipts-v33-43', methods=['GET'])"
    assert text.count(anchor)==1
    block=text.split(anchor,1)[1].split("@app.route(",1)[0]
    assert "@login_required" in block
    assert "get_context(session['username'])" in block
    assert "r.read_status(ctx.dir)" in block
    assert "request.args.get('details')" in block
    assert "create_order(" not in block
