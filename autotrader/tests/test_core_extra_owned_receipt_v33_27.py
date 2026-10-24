"""v33-27 extra GET-confirmed owned receipt and price recovery tests."""
import json
from datetime import datetime,timedelta,timezone
from pathlib import Path
from types import SimpleNamespace

import core_extra_owned_receipt_v33_27 as ex
import core_legacy_close_proof_v33_27 as base
import core_close_recovery_status_v33_27 as status
import core_close_fill_evidence

KST=timezone(timedelta(hours=9))

def sample():
    when=datetime(2026,10,16,10,30,26,tzinfo=KST)
    start=int((when-timedelta(seconds=1)).timestamp()*1000)
    return dict(type="close",symbol="ETH/USDT:USDT",side="long",
        strategy_group="core",dry_run=False,time=when.isoformat(timespec="seconds"),
        amount=2.25,entry_price=2457.82,close_price=None,execution_id=None,
        okx_net_pnl=-.61,pnl_source="okx_realized",reason="position_ai_close_all",
        close_execution_evidence=None,
        close_order_reference=dict(order_id="oid1",start_ms=start,
             expected_contracts=2.25,side="long",
             basis="owned_market_reduce_only_order_receipt"))

def proof(row):
    start=row["close_order_reference"]["start_ms"]+100
    return dict(order_id="oid1",trade_ids=["t1"],vwap=2432.58,
         filled_contracts=2.25,first_fill_timestamp_ms=start,
         last_fill_timestamp_ms=start,price_basis="verified_order_fills",
         proof_type="OWNED_ORDER_ID_EXCHANGE_FILL_VERIFIED")

def test_eligible_only_owned_correct_geometry():
    row=sample()
    assert ex.eligible(row)
    assert not ex.eligible(dict(row,close_price=2400))
    assert not ex.eligible(dict(row,execution_id="any"))
    bad=dict(row,close_order_reference=dict(row["close_order_reference"],side="short"))
    assert not ex.eligible(bad)

def test_exchange_get_bound_to_owned_order(monkeypatch):
    r=sample()
    p=proof(r);p.pop("proof_type")
    called={}
    def fake(c,pos,receipt,*,start_ms):
        called.update(pos=pos,receipt=receipt,start_ms=start_ms)
        return dict(p)
    monkeypatch.setattr(core_close_fill_evidence,"verified_close_fills",fake)
    res=ex.verified_exchange_proof(object(),r)
    assert res["order_id"]=="oid1"
    assert called["receipt"]["id"]=="oid1"
    assert called["pos"]["contracts"]==2.25
    monkeypatch.setattr(core_close_fill_evidence,"verified_close_fills",
        lambda *args,**kwargs:dict(p,order_id="wrong"))
    assert ex.verified_exchange_proof(object(),r) is None

def test_realistic_verified_sidecar_price_filled_only_in_memory(tmp_path):
    row=sample()
    result=dict(schema_version=1,verified=[
       dict(row_index=0,row_sha256=base.row_hash(row),proof=proof(row),
            symbol=row["symbol"],side=row["side"],
            derived_execution_key="verified-legacy-close:oid1")])
    base.save_atomic(tmp_path/ex.FILE,result)
    patch=ex.load_valid_patch([row],tmp_path)
    assert patch[0]["close_price"]==2432.58
    assert patch[0]["execution_id"]=="verified-legacy-close:oid1"
    new=ex.apply_in_memory([row],patch)
    assert row.get("close_price") is None
    assert new[0]["close_execution_evidence"]["trade_ids"]==["t1"]
    assert new[0]["close_price"]==2432.58

def test_mutated_journal_or_missing_proof_remains_unknown(tmp_path):
    row=sample()
    entry=dict(schema_version=1,verified=[dict(
        row_index=0,row_sha256=base.row_hash(row),
        derived_execution_key="verified-legacy-close:oid1",proof=proof(row))])
    base.save_atomic(tmp_path/ex.FILE,entry)
    changed=dict(row,amount=2.3)
    assert ex.load_valid_patch([changed],tmp_path)=={}
    changed=dict(row,close_order_reference=dict(row["close_order_reference"],order_id="mismatch"))
    assert ex.load_valid_patch([changed],tmp_path)=={}
    assert ex.load_valid_patch([row],tmp_path)=={0:ex.load_valid_patch([row],tmp_path)[0]}

def test_no_trading_authority():
    source=Path("core_extra_owned_receipt_v33_27.py").read_text()
    assert "apply_in_memory" in source
    for forbidden in ("create_order(", "cancel_order(", "amend_protective_stop(",
         "gemini_analyzer.","openai_analyzer."):
        assert forbidden not in source

def test_report_has_two_independent_proof_counts():
    source=Path("core_close_recovery_status_v33_27.py").read_text()
    assert "embedded_fill_verified_rows=len(proven)" in source
    assert "owned_receipt_verified_missing_price_rows=len(extra_verified)" in source
    assert "set(extra_verified).intersection(proven)" in source
