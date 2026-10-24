"""v33-31: read-only audit of CORE exchange-triggered owned OCO closes.

Only full exact historic order/fill evidence can be labeled VERIFIED.
No write to trades_log, no order functions, and no strategy mutation.
"""
from collections import Counter
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
import hashlib, json, os, tempfile

import core_exit_outcome_join_v33_25 as j


FILE="core_external_close_audit_v33_31.json"
KST=timezone(timedelta(hours=9))
CORE={"BTC/USDT:USDT","ETH/USDT:USDT","XRP/USDT:USDT","ADA/USDT:USDT"}
START=j.dt_kst("2026-01-01T00:00:00")
PROVEN_SOURCE="owned_algo_order_fills"
PROVEN_PRICE="verified_owned_protection_fills"

def positive(x):
    if x is None or isinstance(x,bool):raise ValueError("missing")
    n=Decimal(str(x))
    if not n.is_finite() or n<=0:raise ValueError("nonpositive")
    return n

def digest(row):
    return hashlib.sha256(json.dumps(row,ensure_ascii=False,sort_keys=True,
        separators=(",",":"),allow_nan=False).encode()).hexdigest()

def classification(row,client,duplicates):
    ident=row.get("execution_id")
    if not isinstance(ident,str) or not ident.startswith("core-external-close:"):
        return "NOT_IN_SCOPE","not_external_lifecycle",{}
    if duplicates[ident]>1:
        return "ALERT","duplicate_lifecycle_identity",{}
    reason=row.get("reason")
    cause=row.get("close_reason_evidence")
    proof=row.get("close_execution_evidence")
    if not isinstance(cause,dict) or not isinstance(proof,dict):
        return "UNVERIFIED","owned_oco_evidence_missing",{}
    if cause.get("source")!=PROVEN_SOURCE or proof.get("price_basis")!=PROVEN_PRICE:
        return "UNVERIFIED","owned_oco_source_missing",{}
    oid=cause.get("order_id")
    algo=cause.get("algo_id")
    life=cause.get("lifecycle_id")
    if (not oid or not algo or not life
        or ident!="core-external-close:"+str(life)
        or str(proof.get("order_id"))!=str(oid)
        or str(proof.get("algo_id"))!=str(algo)
        or str(proof.get("lifecycle_id"))!=str(life)
        or cause.get("reason")!=reason
        or reason not in ("stop_loss","take_profit")):
        return "ALERT","stored_ownership_fields_conflict",{}
    if cause.get("trade_ids") is None or not isinstance(cause["trade_ids"],list):
        return "ALERT","stored_trade_ids_missing",{}
    if set(map(str,cause["trade_ids"]))!=set(map(str,proof.get("trade_ids") or [])):
        return "ALERT","stored_trade_ids_disagree",{}
    try:
        amt=positive(row.get("amount"))
        qty=positive(proof.get("filled_contracts"))
        vwap=positive(proof.get("vwap"))
        ref=positive(row.get("close_price"))
        if amt!=qty or abs(ref-vwap)>max(Decimal("0.000000001"),vwap*Decimal("0.00000001")):
            return "ALERT","journal_quantity_or_price_conflict",{}
        if abs(positive(cause.get("exit_price"))-vwap)>max(
            Decimal("0.000000001"),vwap*Decimal("0.00000001")):
            return "ALERT","owned_oco_price_conflict",{}
    except (ValueError,TypeError,InvalidOperation):
        return "ALERT","journal_numeric_proof_invalid",{}
    symbol=row["symbol"]
    try:
        order=client.exchange.fetch_order(str(oid),symbol)
        info=order.get("info") or {}
        expected_side="sell" if row["side"]=="long" else "buy"
        # CCXT maps an OCO child order's unified id to algoId. Raw ordId
        # identifies the exact executed order. Never conflate the two.
        if (str(order.get("id")) not in (str(oid),str(algo))
            or str(info.get("ordId"))!=str(oid)
            or str(info.get("algoId"))!=str(algo)
            or str(info.get("reduceOnly")).lower() not in ("true","1")
            or order.get("status")!="closed"
            or order.get("side")!=expected_side
            or order.get("symbol") not in (None,symbol)
            or positive(order.get("filled"))!=amt
            or Decimal(str(order.get("remaining") or 0))!=0):
            return "ALERT","exchange_order_identity_or_size_conflict",{}
    except Exception:
        return "PENDING","exchange_order_lookup_unavailable",{}
    # Tied to exact owned order ID, never a price/time window match.
    when=j.dt_kst(row["time"])
    earliest=int(when.timestamp()*1000)-24*3600*1000
    try:
        trades=client.fetch_trades_for_order(str(oid),earliest,limit=100)
    except Exception:
        return "PENDING","exchange_fills_query_unavailable",{}
    if not isinstance(trades,list) or not trades or len(trades)>=100:
        return "PENDING","exchange_fills_unavailable_or_incomplete",{}
    unique={}
    for item in trades:
        if not isinstance(item,dict):
            return "PENDING","malformed_exchange_fill",{}
        raw=item.get("info") or {}
        tid=str(raw.get("tradeId") or "")
        if (str(raw.get("ordId"))!=str(oid)
            or str(item.get("order"))!=str(oid)
            or str(item.get("id"))!=tid
            or not tid or item.get("symbol")!=symbol
            or item.get("side")!=expected_side):
            return "ALERT","exchange_fill_order_or_side_conflict",{}
        try:
            q=positive(item.get("amount"));px=positive(item.get("price"))
            fill_tuple=(q,px)
            if tid in unique and unique[tid]!=fill_tuple:
                return "ALERT","duplicate_fill_conflict",{}
            unique[tid]=fill_tuple
        except (ValueError,InvalidOperation):
            return "PENDING","exchange_fill_geometry_unavailable",{}
    total=sum((q for q,px in unique.values()),Decimal(0))
    actual_vwap=sum((q*px for q,px in unique.values()),Decimal(0))/total
    if (total!=amt or set(unique)!=set(map(str,proof["trade_ids"]))
        or abs(actual_vwap-vwap)>max(
            Decimal("0.000000001"),vwap*Decimal("0.00000001"))):
        return "ALERT","exchange_fill_ids_or_vwap_conflict",{}
    return "VERIFIED","owned_algo_record_and_exact_order_fills_match",{
        "order_id":str(oid),"algo_id":str(algo),
        "fills":len(unique),"vwap":float(actual_vwap)}

def eligible(row):
    return (isinstance(row,dict) and row.get("type")=="close"
        and row.get("strategy_group")=="core" and row.get("dry_run") is False
        and row.get("symbol") in CORE and row.get("side") in ("long","short")
        and str(row.get("execution_id") or "").startswith("core-external-close:")
        and (j.dt_kst(row.get("time")) is not None)
        and j.dt_kst(row.get("time"))>=START)
def evaluate(rows,client_factory,previous=None):
    previous=previous if isinstance(previous,dict) else {}
    prior={x.get("sha256"):x for x in previous.get("results",[])
           if isinstance(x,dict) and x.get("sha256")}
    duplicates=Counter(r.get("execution_id") for r in rows
                       if isinstance(r,dict) and r.get("execution_id"))
    clients={}
    results=[]
    for index,row in enumerate(rows):
        if not eligible(row):continue
        sha=digest(row)
        old=prior.get(sha)
        ident=row["execution_id"]
        if old and old.get("status")=="VERIFIED" and duplicates[ident]==1:
            outcome=(old["status"],old["reason"],old["details"])
        else:
            try:
                symbol=row["symbol"]
                local_only=(not isinstance(row.get("close_reason_evidence"),dict)
                    or not isinstance(row.get("close_execution_evidence"),dict))
                if local_only:
                    outcome=classification(row,None,duplicates)
                else:
                    if symbol not in clients:clients[symbol]=client_factory(symbol)
                    outcome=classification(row,clients[symbol],duplicates)
            except Exception:
                outcome=("PENDING","client_lookup_unavailable",{})
        status,why,detail=outcome
        results.append(dict(row_index=index,sha256=sha,
            symbol=row["symbol"],journal_time=row["time"],
            lifecycle_id=ident,status=status,reason=why,details=detail))
    alerts=[r for r in results if r["status"]=="ALERT"]
    old_alerts={a["sha256"]+":"+a["reason"] for a in previous.get("alerts",[])
                if isinstance(a,dict) and a.get("sha256") and a.get("reason")}
    return dict(version="v33-31",mode="READ_ONLY",may_execute=False,
        observed_at=datetime.now(KST).isoformat(),
        scanned=len(results),
        status_counts=dict(Counter(r["status"] for r in results)),
        results=results,alerts=alerts,
        new_alerts=[a for a in alerts if a["sha256"]+":"+a["reason"] not in old_alerts],
        limitations=[
          "Verified means OKX order/fill proof matched locally recorded owned OCO evidence.",
          "Entry-to-OCO ownership is inherited from a previously verified capture, not independently replayed here.",
          "Unverified external closes are excluded from verified totals.",
          "No orders, position writes, or changes to trading behavior."])

def save_atomic(path,body):
    path=Path(path)
    fd,tmp=tempfile.mkstemp(dir=path.parent,prefix=".v33_31_")
    try:
        os.fchmod(fd,0o600)
        with os.fdopen(fd,"w",encoding="utf-8") as f:
            json.dump(body,f,ensure_ascii=False,indent=2,allow_nan=False)
            f.flush();os.fsync(f.fileno())
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)
def read_status(user_dir):
    path=Path(user_dir)/FILE
    try:
        return json.loads(path.read_text()) if path.exists() else {}
    except (ValueError,OSError):
        return {}

def run(user_dir,client_factory):
    directory=Path(user_dir)
    rows,_=j.read_lines(directory/"trades_log.jsonl")
    result=evaluate(rows,client_factory,read_status(directory))
    save_atomic(directory/FILE,result)
    return result

def main():
    import argparse,config,okx_client
    parser=argparse.ArgumentParser()
    parser.add_argument("--user-dir",required=True)
    args=parser.parse_args()
    cfg=config.UserConfig(args.user_dir)
    output=run(args.user_dir,lambda sym:okx_client.OkxClient(sym,cfg))
    print(json.dumps(dict(event="V33_31_EXTERNAL_CLOSE_AUDIT",
        scanned=output["scanned"],statuses=output["status_counts"],
        new_alerts=len(output["new_alerts"])),ensure_ascii=False))
    return 0

if __name__=="__main__":
    raise SystemExit(main())
