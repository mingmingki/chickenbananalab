"""v33-30 standalone GET-only post-close verification; never places orders."""
import hashlib
import json
import os
import tempfile
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
import core_exit_outcome_join_v33_25 as ledger
import core_close_fill_evidence as evidence

START=ledger.dt_kst("2026-10-17T13:29:00")
KST=timezone(timedelta(hours=9))
FILE="core_close_posttrade_audit_v33_30.json"
SYMBOLS={"BTC/USDT:USDT","ETH/USDT:USDT","XRP/USDT:USDT","ADA/USDT:USDT"}

def hashrow(row):
    return hashlib.sha256(json.dumps(row,sort_keys=True,
        separators=(",",":"),ensure_ascii=False).encode()).hexdigest()

def audit_row(row, client, *, now, duplicates):
    age=(now-ledger.dt_kst(row["time"])).total_seconds()
    def result(status,reason,**details):
        return dict(status=status,reason=reason,details=details)
    if row.get("reason")=="manual_stop":
        return result("EXCLUDED","MANUAL_CLIENT_ID_NOT_ORDER_ID")
    identifier=row.get("execution_id")
    if identifier and duplicates.get(identifier,0)>1:
        return result("ALERT","EXECUTION_ID_DUPLICATED")
    # v33-30a: exchange-triggered protection/external closes use a
    # position-lifecycle key, NOT an owned market order ID. This standalone
    # audit does not verify their algo fills; report them as a separate scope.
    # Never promote these rows to VERIFIED without exchange-specific proof.
    if isinstance(identifier,str) and identifier.startswith("core-external-close:"):
        if row.get("close_reason_evidence") or row.get("close_execution_evidence"):
            return result("EXTERNAL_PATH","EXTERNAL_PROVENANCE_NOT_AUDITED_HERE")
        return result("EXTERNAL_UNVERIFIED","EXTERNAL_CLOSE_EVIDENCE_MISSING")
    if (not identifier and not row.get("close_order_reference")
        and row.get("reason") in ("external_close_unknown","stop_loss","take_profit")):
        return result("EXTERNAL_UNVERIFIED","UNBOUND_EXTERNAL_CLOSE_NEEDS_SEPARATE_AUDIT")
    source=row.get("close_order_reference") or {}
    captured=row.get("close_execution_evidence") or {}
    if not isinstance(source,dict) or not isinstance(captured,dict):
        return result("ALERT","INVALID_RECORD_EVIDENCE")
    oid=source.get("order_id") or captured.get("order_id")
    if source.get("order_id") and captured.get("order_id"):
        if str(source["order_id"])!=str(captured["order_id"]):
            return result("ALERT","OWNED_ORDER_IDS_DISAGREE")
    if not oid:
        return result("ALERT" if age>900 else "PENDING","OWNED_ORDER_ID_MISSING")
    oid=str(oid)
    expected=f"core-close-order:{row['symbol']}:{oid}"
    if identifier!=expected:
        return result("ALERT" if age>900 else "PENDING",
                      "JOURNAL_EXECUTION_ID_MISMATCH",order_id=oid)
    start=source.get("start_ms") or captured.get("first_fill_timestamp_ms")
    if start is None:
        return result("ALERT" if age>900 else "PENDING","OWNED_START_TIME_MISSING")
    try:
        proof=evidence.verified_close_fills(
            client,{"side":row["side"],"contracts":row["amount"]},
            {"id":oid,"symbol":row["symbol"]},start_ms=int(start))
    except Exception:
        return result("PENDING","EXCHANGE_READ_UNAVAILABLE")
    if not proof:
        return result("ALERT" if age>900 else "PENDING",
                      "EXCHANGE_FILLS_UNVERIFIED",order_id=oid)
    if str(proof["order_id"])!=oid:
        return result("ALERT","EXCHANGE_ORDER_ID_CONFLICT")
    if captured:
        if (str(captured.get("order_id"))!=oid
            or captured.get("price_basis")!="verified_order_fills"
            or set(map(str,captured.get("trade_ids") or []))!=set(proof["trade_ids"])
            or captured.get("vwap") is None):
            return result("ALERT","JOURNAL_FILL_EVIDENCE_CONFLICT")
        if abs(float(captured["vwap"])-float(proof["vwap"]))>max(
            1e-9,abs(float(proof["vwap"]))*1e-8):
            return result("ALERT","JOURNAL_FILL_VWAP_MISMATCH")
    close_price=ledger.finite(row.get("close_price"))
    if close_price is not None and abs(close_price-float(proof["vwap"]))>max(
            1e-9,abs(float(proof["vwap"]))*1e-8):
        return result("ALERT","JOURNAL_CLOSE_PRICE_MISMATCH")
    if ledger.finite(row.get("okx_net_pnl")) is None:
        return result("ALERT","JOURNAL_NET_PNL_MISSING")
    return result("VERIFIED","EXACT_ORDER_AND_FILLS",
        order_id=oid,trade_count=len(proof["trade_ids"]),
        vwap=proof["vwap"],filled_contracts=proof["filled_contracts"])
def examine(rows,client_factory, *, previous=None, now=None):
    now=now or datetime.now(KST)
    previous=previous or {}
    prior={x.get("sha256"):x for x in previous.get("results",[])
           if isinstance(x,dict) and x.get("sha256")}
    duplicates=Counter(x.get("execution_id") for x in rows
                       if x.get("execution_id"))
    candidates=[]
    for i,row in enumerate(rows):
        timestamp=ledger.dt_kst(row.get("time"))
        if (row.get("type")=="close" and row.get("strategy_group")=="core"
            and row.get("dry_run") is False and row.get("symbol") in SYMBOLS
            and timestamp is not None and timestamp>=START):
            candidates.append((i,row))
    clients={}
    results=[]
    for i,row in candidates[-250:]:
        digest=hashrow(row)
        old=prior.get(digest)
        if (old and old.get("status")=="VERIFIED"
            and duplicates[row.get("execution_id")]==1):
            outcome={key:old[key] for key in ("status","reason","details")}
        else:
            sym=row["symbol"]
            try:
                local_only=(row.get("reason")=="manual_stop"
                    or str(row.get("execution_id") or "").startswith("core-external-close:")
                    or (not row.get("execution_id")
                        and not row.get("close_order_reference")
                        and row.get("reason") in ("external_close_unknown","stop_loss","take_profit")))
                if not local_only and sym not in clients:
                    clients[sym]=client_factory(sym)
                outcome=audit_row(row,None if local_only else clients[sym],
                                  now=now,duplicates=duplicates)
            except Exception:
                outcome=dict(status="PENDING",reason="CLIENT_READ_UNAVAILABLE",
                             details={})
        results.append(dict(outcome,index=i,sha256=digest,
                            symbol=row["symbol"],time=row["time"]))
    active=[x for x in results if x["status"]=="ALERT"]
    for row in active:
        row["alert_key"]=row["sha256"]+":"+row["reason"]
    old_alerts={x.get("alert_key") for x in previous.get("alerts",[])
                if isinstance(x,dict)}
    changes=[x for x in active if x["alert_key"] not in old_alerts]
    return dict(schema_version=1,mode="READ_ONLY",may_execute=False,
        last_check=now.isoformat(),start=START.isoformat(),
        scanned=len(results),counts=dict(Counter(x["status"] for x in results)),
        results=results,alerts=active,
        new_alerts=[dict(x) for x in changes])
def store_atomic(path,payload):
    path=Path(path)
    fd,tmp=tempfile.mkstemp(prefix=".v33_30_",dir=str(path.parent))
    try:
        os.fchmod(fd,0o600)
        with os.fdopen(fd,"w",encoding="utf-8") as out:
            json.dump(payload,out,ensure_ascii=False,indent=2)
            out.flush()
            os.fsync(out.fileno())
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
def read_status(user_dir):
    path=Path(user_dir)/FILE
    try:
        return json.loads(path.read_text()) if path.exists() else {}
    except (OSError,ValueError):
        return {}

def run(user_dir,client_factory):
    folder=Path(user_dir)
    rows,invalid=ledger.read_lines(folder/"trades_log.jsonl")
    state=examine(rows,client_factory,previous=read_status(folder))
    state["invalid_journal_rows"]=invalid
    store_atomic(folder/FILE,state)
    return state
def main():
    import argparse
    import config
    import okx_client
    parser=argparse.ArgumentParser()
    parser.add_argument("--user-dir",required=True)
    args=parser.parse_args()
    cfg=config.UserConfig(args.user_dir)
    state=run(args.user_dir,lambda sym:okx_client.OkxClient(sym,cfg))
    brief={"event":"V33_30_CLOSE_AUDIT","scanned":state["scanned"],
           "counts":state["counts"],
           "new_alerts":[{"symbol":x["symbol"],"reason":x["reason"]}
                         for x in state["new_alerts"]]}
    # v33-31: run a separate read-only audit for owned exchange OCO
    # child orders. Failure cannot interrupt the v33-30 close monitor.
    try:
        import core_external_close_audit_v33_31 as external
        external_result=external.run(args.user_dir,
            lambda sym:okx_client.OkxClient(sym,cfg))
        brief["external_protection_audit"]=dict(
            scanned=external_result["scanned"],
            counts=external_result["status_counts"],
            new_alerts=len(external_result["new_alerts"]))
    except Exception as exc:
        brief["external_protection_audit"]=dict(
            status="UNAVAILABLE",error_type=type(exc).__name__)
    print(json.dumps(brief,ensure_ascii=False))
    return 0

if __name__=="__main__":
    raise SystemExit(main())
