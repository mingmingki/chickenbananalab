"""v33-39 historical full-close GET-only accounting and candidate guard."""
import json
from pathlib import Path
from types import SimpleNamespace
from decimal import Decimal
import pytest
import core_legacy_full_close_v33_39 as a

def row():
    return dict(type="close",symbol="ETH/USDT:USDT",side="long",
      time="2026-09-11T15:09:03",amount=1.52,pnl=.53504,
      fee=0,okx_net_pnl=None,strategy_group="core",dry_run=False)

def trade(id,order,qty,ts,side="sell"):
    return dict(id=id,order=order,amount=qty,timestamp=ts,side=side)

def test_multi_fill_summed_to_one_order():
    stamp=a.date_ms(row()["time"])
    trades=[trade("t1","order1",.7,stamp),
            trade("t2","order1",.82,stamp+100)]
    matched=a.order_candidates(row(),trades)
    assert len(matched)==1
    assert matched[0]["order_id"]=="order1"
    assert matched[0]["trade_ids"]==["t1","t2"]
    assert matched[0]["n_fills"]==2

def test_one_order_quantity_mismatch_not_promoted():
    stamp=a.date_ms(row()["time"])
    assert not a.order_candidates(row(),[trade("t1","o1",1.50,stamp)])

def test_two_equal_orders_remain_ambiguous():
    stamp=a.date_ms(row()["time"])
    candidates=a.order_candidates(row(),
        [trade("t1","o1",1.52,stamp),trade("t2","o2",1.52,stamp+100)])
    assert len(candidates)==2

def test_order_reduceonly_closed_side_verified():
    raw=dict(ordId="o1",reduceOnly="true")
    order=dict(info=raw,status="closed",symbol=row()["symbol"],
         side="sell",filled=1.52)
    client=SimpleNamespace(exchange=SimpleNamespace(
         fetch_order=lambda oid,sym:order))
    assert a.verify_order(client,row()["symbol"],row(),
        dict(order_id="o1")) is True
    assert a.verify_order(SimpleNamespace(exchange=SimpleNamespace(
         fetch_order=lambda *args:dict(order,info=dict(ordId="o1",reduceOnly="false")))),
         row()["symbol"],row(),dict(order_id="o1")) is False

def test_bill_exact_all_fill_ids():
    candidate=dict(order_id="o1",trade_ids=["t1","t2"])
    r=lambda oid,tid,gross,fee,net:dict(type="2",ccy="USDT",
        ordId=oid,tradeId=tid,pnl=gross,fee=fee,balChg=net)
    bills={
      "b1":r("o1","t1","1","-.1",".9"),
      "b2":r("o1","t2","2","-.2","1.8")
    }
    result=a.settle_order(candidate,bills)
    assert result["bill_ids"]==["b1","b2"]
    assert Decimal(result["exchange_cash_change_usdt"])==Decimal("2.7")

def test_missing_fill_bill_refused():
    with pytest.raises(ValueError,match="MISSING"):
        a.settle_order(dict(order_id="o1",trade_ids=["t1"]),{})

def test_extra_same_order_bill_refused():
    bill=lambda t:dict(type="2",ccy="USDT",ordId="o1",tradeId=t,
        pnl="1",fee="-.1",balChg=".9")
    with pytest.raises(ValueError,match="UNMATCHED_BILLS"):
        a.settle_order(dict(order_id="o1",trade_ids=["t1"]),{
            "b1":bill("t1"),"b2":bill("t2")})

def test_bill_invariant_wrong_net_not_verified():
    candidate=dict(order_id="o1",trade_ids=["t1"])
    with pytest.raises(ValueError,match="INVARIANT"):
        a.settle_order(candidate,{
          "b1":dict(type="2",ccy="USDT",ordId="o1",tradeId="t1",
                    pnl="1",fee="-.1",balChg=".7")})

def test_report_stale_when_original_case_row_edited(tmp_path):
    original=row()
    journal=tmp_path/"trades_log.jsonl"
    journal.write_text(json.dumps(original)+"\n")
    report=dict(version="v33-39",records=[
        dict(index=0,row_sha256=a.full.digest(original))])
    a.save(tmp_path/a.FILE,report)
    assert a.read_status(tmp_path)["source_valid"] is True
    journal.write_text(json.dumps(dict(original,amount=2.5))+"\n")
    assert a.read_status(tmp_path)["source_valid"] is False

def test_journal_append_does_not_invalidate_historic_hashes(tmp_path):
    original=row()
    journal=tmp_path/"trades_log.jsonl"
    journal.write_text(json.dumps(original)+"\n")
    report=dict(version="v33-39",records=[
        dict(index=0,row_sha256=a.full.digest(original))])
    a.save(tmp_path/a.FILE,report)
    journal.write_text(journal.read_text()+json.dumps(dict(original,
         time="2026-10-18T02:15:00"))+"\n")
    assert a.read_status(tmp_path)["source_valid"] is True

def test_proof_flags_no_journal_ownership():
    code=Path("core_legacy_full_close_v33_39.py").read_text()
    assert "journal_row_ownership_proven=False" in code
    assert "may_execute=False" in code
    for forbidden in ("create_order(","cancel_order(","close_position(",
        "amend_protective_stop("):
        assert forbidden not in code


def test_authenticated_readonly_endpoint():
    text=Path("web_app.py").read_text()
    route=text.split("@app.route('/api/ops/core-legacy-full-close-v33-39'",1)[1]
    route=route.split("@app.route(",1)[0]
    assert "@login_required" in route
    assert "get_context(session['username'])" in route
    assert "audit.read_status(ctx.dir)" in route
    assert "request.args.get('details') != '1'" in route
    assert "create_order(" not in route
