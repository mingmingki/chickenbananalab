"""v33-30 close monitor: exact GET evidence, retry and dedup regression."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import json
import core_close_posttrade_audit_v33_30 as m

KST=timezone(timedelta(hours=9))
NOW=datetime(2026,10,17,14,20,tzinfo=KST)
SYMBOL="BTC/USDT:USDT"

def row(ts="2026-10-17T14:00:00",order="4001"):
    return dict(type="close",dry_run=False,strategy_group="core",
        symbol=SYMBOL,side="long",time=ts,
        amount=1.20,reason="position_ai_close_all",
        execution_id=f"core-close-order:{SYMBOL}:{order}",
        close_order_reference=dict(order_id=order,start_ms=1792213200000),
        close_execution_evidence=dict(order_id=order,trade_ids=["t1"],
            vwap=81000.0,price_basis="verified_order_fills"),
        close_price=81000.,okx_net_pnl=-1.32)

def getproof(*args,**kwargs):
    return dict(order_id="4001",trade_ids=["t1"],vwap=81000.,
                filled_contracts=1.2,price_basis="verified_order_fills")

def test_verified_order_and_fill(monkeypatch):
    monkeypatch.setattr(m.evidence,"verified_close_fills",getproof)
    result=m.audit_row(row(),SimpleNamespace(symbol=SYMBOL),
                       now=NOW,duplicates={})
    assert result["status"]=="VERIFIED"
    assert result["details"]["trade_count"]==1

def test_missing_execution_id_pending_then_alert():
    record=row()
    record.pop("execution_id")
    recent=datetime(2026,10,17,14,2,tzinfo=KST)
    state=m.audit_row(record,None,now=recent,duplicates={})
    assert state["status"]=="PENDING"
    state=m.audit_row(record,None,now=NOW,duplicates={})
    assert state["status"]=="ALERT"
    assert state["reason"]=="JOURNAL_EXECUTION_ID_MISMATCH"

def test_duplicate_id_always_alert():
    rec=row()
    result=m.audit_row(rec,None,now=NOW,duplicates={rec["execution_id"]:2})
    assert result["reason"]=="EXECUTION_ID_DUPLICATED"

def test_conflicting_owned_and_fill_ids_alert():
    rec=row()
    rec["close_execution_evidence"]["order_id"]="OTHER"
    result=m.audit_row(rec,None,now=NOW,duplicates={})
    assert result["reason"]=="OWNED_ORDER_IDS_DISAGREE"

def test_fill_id_conflict_alert(monkeypatch):
    monkeypatch.setattr(m.evidence,"verified_close_fills",getproof)
    rec=row()
    rec["close_execution_evidence"]["trade_ids"]=["wrong"]
    result=m.audit_row(rec,SimpleNamespace(symbol=SYMBOL),
                       now=NOW,duplicates={})
    assert result["reason"]=="JOURNAL_FILL_EVIDENCE_CONFLICT"

def test_missing_exchange_fill_retry_and_alert(monkeypatch):
    monkeypatch.setattr(m.evidence,"verified_close_fills",
                        lambda *a,**k:None)
    before=datetime(2026,10,17,14,1,tzinfo=KST)
    assert m.audit_row(row(),None,now=before,duplicates={})["status"]=="PENDING"
    assert m.audit_row(row(),None,now=NOW,duplicates={})["status"]=="ALERT"

def test_manual_is_not_mistaken_for_raw_exchange_order():
    rec=row()
    rec["reason"]="manual_stop"
    rec["execution_id"]="manual-client-close1"
    assert m.audit_row(rec,None,now=NOW,duplicates={})["status"]=="EXCLUDED"

def test_new_close_baseline_and_cached_verified(monkeypatch):
    monkeypatch.setattr(m.evidence,"verified_close_fills",getproof)
    source=[row()]
    first=m.examine(source,lambda sym:SimpleNamespace(symbol=sym),now=NOW)
    assert first["counts"]=={"VERIFIED":1}
    assert first["new_alerts"]==[]
    def fail(*args):
        raise AssertionError("verified close should not be requested twice")
    second=m.examine(source,fail,previous=first,now=NOW)
    assert second["counts"]=={"VERIFIED":1}
    assert first["may_execute"] is False

def test_duplicate_after_verified_invalidates_cache(monkeypatch):
    monkeypatch.setattr(m.evidence,"verified_close_fills",getproof)
    first=m.examine([row()],lambda sym:SimpleNamespace(symbol=sym),now=NOW)
    second=m.examine([row(),row("2026-10-17T14:01:00")],
        lambda sym:SimpleNamespace(symbol=sym),previous=first,now=NOW)
    assert second["counts"]=={"ALERT":2}

def test_no_new_close_does_not_call_exchange():
    result=m.examine([],lambda sym:1/0,now=NOW)
    assert result["scanned"]==0 and result["counts"]=={}

def test_persists_readonly_snapshot_atomically(tmp_path,monkeypatch):
    (tmp_path/"trades_log.jsonl").write_text(json.dumps(row())+"\n")
    monkeypatch.setattr(m.evidence,"verified_close_fills",getproof)
    state=m.run(tmp_path,lambda sym:SimpleNamespace(symbol=sym))
    assert state["scanned"]==1
    state2=m.read_status(tmp_path)
    assert state2["mode"]=="READ_ONLY"
    assert state2["results"][0]["status"]=="VERIFIED"
    assert json.loads((tmp_path/"trades_log.jsonl").read_text())==row()


def test_exchange_protection_close_uses_separate_audit_scope():
    rec=row()
    rec["execution_id"]="core-external-close:owned-position-1"
    rec["reason"]="stop_loss"
    rec["close_order_reference"]=None
    rec["close_reason_evidence"]={"source":"owned_algo_order_fills","algo_id":"a1"}
    outcome=m.audit_row(rec,None,now=NOW,duplicates={})
    assert outcome["status"]=="EXTERNAL_PATH"
    assert outcome["reason"]=="EXTERNAL_PROVENANCE_NOT_AUDITED_HERE"
    assert outcome["status"]!="VERIFIED"

def test_exchange_protection_close_without_proof_not_claimed_verified():
    rec=row()
    rec["execution_id"]="core-external-close:owned-position-2"
    rec["reason"]="take_profit"
    rec["close_order_reference"]=None
    rec["close_execution_evidence"]=None
    result=m.audit_row(rec,None,now=NOW,duplicates={})
    assert result["status"]=="EXTERNAL_UNVERIFIED"

def test_external_path_does_not_trigger_exchange_order_lookup():
    rec=row()
    rec["execution_id"]="core-external-close:owned-position-3"
    rec["reason"]="stop_loss"
    rec["close_reason_evidence"]={"source":"owned_algo_order_fills","algo_id":"a3"}
    result=m.examine([rec],lambda symbol: 1/0,now=NOW)
    assert result["counts"]=={"EXTERNAL_PATH":1}
    assert result["alerts"]==[]

def test_external_duplicate_detected_before_scope_exclusion():
    a=row()
    a["execution_id"]="core-external-close:duplicate-lifecycle"
    a["close_reason_evidence"]={"source":"owned_algo_order_fills"}
    b=dict(a,time="2026-10-17T14:01:00")
    result=m.examine([a,b],lambda symbol: 1/0,now=NOW)
    assert result["counts"]=={"ALERT":2}
    assert all(r["reason"]=="EXECUTION_ID_DUPLICATED" for r in result["alerts"])

def test_unbound_external_missing_id_is_separate_from_market_close_id():
    rec=row()
    rec.pop("execution_id")
    rec["reason"]="external_close_unknown"
    rec["close_order_reference"]=None
    result=m.examine([rec],lambda symbol: 1/0,now=NOW)
    assert result["counts"]=={"EXTERNAL_UNVERIFIED":1}
