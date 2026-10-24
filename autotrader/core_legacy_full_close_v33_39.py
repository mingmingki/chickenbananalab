"""v33-39: independent OKX GET-only review of 207 legacy CORE full closes.

Never promotes time+quantity matches to journal ownership. Uses exact exchange
order/fill/bill IDs only to establish exchange settlement for temporal candidate.
"""
from pathlib import Path
from collections import Counter,defaultdict
from datetime import datetime,timedelta,timezone
from decimal import Decimal as D, InvalidOperation
import hashlib,json,tempfile,os
import core_legacy_gap_audit_v33_37 as parent
import core_legacy_bill_correlation_v33_38 as bill80
import core_exact_bill_audit_v33_34 as bills
import core_full_ledger_audit_v33_36 as full

FILE="core_legacy_full_close_v33_39.json"
KST=timezone(timedelta(hours=9))
TOL=D("0.0000001")

def decimal(n):
    v=D(str(n))
    if not v.is_finite():raise ValueError("NONFINITE")
    return v

def cohort(folder,rows):
    src=json.loads((Path(folder)/parent.FILE).read_text())
    if src.get("version")!="v33-37":raise ValueError("UPSTREAM_VERSION_INVALID")
    cases=[x for x in src.get("cases",[]) if x.get("type")=="close"
        and x.get("defect") in ("LEGACY_CLOSE_MISSING_FEE_AND_NET",
             "LEGACY_CLOSE_MISSING_NET")]
    if len(cases)!=207:raise ValueError("LEGACY_CLOSE_COHORT_SIZE_CHANGED")
    for x in cases:
        i=x["index"]
        if type(i)!=int or i>=len(rows) or full.digest(rows[i])!=x.get("row_sha256"):
            raise ValueError("LEGACY_CLOSE_SOURCE_CHANGED")
    return cases

def date_ms(text):
    return int(datetime.fromisoformat(text).replace(tzinfo=KST).timestamp()*1000)

def order_candidates(row,trades,*,seconds=150):
    when=date_ms(row["time"])
    need_side="buy" if row.get("side")=="short" else "sell"
    qty=decimal(row["amount"])
    orders=defaultdict(list)
    for t in trades:
        try:
            ms=int(t["timestamp"])
            if abs(ms-when)>seconds*1000 or t.get("side")!=need_side:continue
            oid=str(t.get("order") or "")
            tid=str(t.get("id") or "")
            amount=decimal(t["amount"])
            if not oid or not tid or amount<=0:continue
            orders[oid].append(t)
        except (ValueError,TypeError,InvalidOperation,KeyError):
            continue
    found=[]
    for oid,parts in orders.items():
        IDs=[str(t["id"]) for t in parts]
        if len(IDs)!=len(set(IDs)):continue
        total=sum((decimal(t["amount"]) for t in parts),D(0))
        if abs(total-qty)>max(TOL,qty*D("0.0000001")):continue
        found.append(dict(order_id=oid,
            trade_ids=sorted(IDs),n_fills=len(IDs),qty=str(total),
            first_ms=min(int(t["timestamp"]) for t in parts),
            last_ms=max(int(t["timestamp"]) for t in parts)))
    return found

def verify_order(client,symbol,row,candidate):
    order=client.exchange.fetch_order(candidate["order_id"],symbol)
    raw=order.get("info") or {}
    qty=decimal(row["amount"])
    valid=(str(raw.get("ordId"))==candidate["order_id"]
        and order.get("status")=="closed"
        and str(raw.get("reduceOnly")).lower() in ("true","1")
        and order.get("symbol")==symbol
        and order.get("side")==("buy" if row["side"]=="short" else "sell")
        and abs(decimal(order["filled"])-qty)<=max(TOL,qty*D("0.0000001")))
    return valid
def settle_order(candidate,exchange_bills):
    amounts=dict(gross=D(0),fee=D(0),cash_change=D(0))
    found=[]
    for trade_id in candidate["trade_ids"]:
        linked=[(bid,b) for bid,b in exchange_bills.items()
           if str(b.get("type"))=="2" and b.get("ccy")=="USDT"
           and str(b.get("ordId") or "")==candidate["order_id"]
           and str(b.get("tradeId") or "")==trade_id]
        if len(linked)!=1:raise ValueError("BILL_MISSING_OR_DUPLICATED")
        bid,b=linked[0]
        amounts["gross"]+=decimal(b["pnl"])
        amounts["fee"]+=-decimal(b["fee"])
        amounts["cash_change"]+=decimal(b["balChg"])
        found.append(str(bid))
    if len(found)!=len(set(found)):raise ValueError("BILL_ID_REUSED")
    if abs(amounts["gross"]-amounts["fee"]-amounts["cash_change"])>D(".0001"):
        raise ValueError("BILL_NET_INVARIANT_FAILED")
    unexpected=[bid for bid,b in exchange_bills.items()
        if str(b.get("type"))=="2"
        and str(b.get("ordId") or "")==candidate["order_id"]
        and str(bid) not in found]
    if unexpected:raise ValueError("OWNED_ORDER_HAS_UNMATCHED_BILLS")
    return dict(bill_ids=sorted(found),
        exchange_gross_usdt=str(amounts["gross"]),
        exchange_fee_usdt=str(amounts["fee"]),
        exchange_cash_change_usdt=str(amounts["cash_change"]))

def audit(user_dir,client_factory):
    root=Path(user_dir)
    original=(root/"trades_log.jsonl").read_bytes()
    rows=[json.loads(x) for x in original.decode().splitlines() if x.strip()]
    source=cohort(root,rows)
    groups=defaultdict(list)
    for case in source:
        groups[(case["symbol"],case["time"][:10])].append(case)
    clients={};trades_by_group={};bills_by_group={};errors={}
    for key in sorted(groups):
        symbol,day=key
        try:
            if symbol not in clients:clients[symbol]=client_factory(symbol)
            # Same strict interval subdivision as v33-37; saturated days
            # are never called complete.
            trades_by_group[key]=parent.fetch_symbol_day(clients[symbol],symbol,day)
            start=date_ms(day)
            bills_by_group[key],_ = bills.fetch_bills(
                clients[symbol].exchange,symbol,start,start+86400000-1,
                kind="2",max_calls=100)
        except Exception as exc:
            errors[key]=type(exc).__name__
    records=[]
    for case in source:
        idx=case["index"];row=rows[idx]
        symbol,day=case["symbol"],case["time"][:10]
        key=(symbol,day)
        basic=dict(index=idx,row_sha256=full.digest(row),
            symbol=symbol,time=case["time"],reason=row.get("reason"),
            journal_gross_usdt=(str(row.get("pnl")) if row.get("pnl") is not None else None))
        if key in errors:
            records.append(dict(basic,status="UNAVAILABLE",reason_code=errors[key]))
            continue
        options=order_candidates(row,trades_by_group[key])
        if len(options)!=1:
            records.append(dict(basic,
                status="AMBIGUOUS" if options else "NO_CANDIDATE",
                reason_code=("MULTIPLE_ORDER_CANDIDATES" if options
                             else "NO_EXACT_SIDE_QTY_ORDER"),
                order_candidate_count=len(options)))
            continue
        candidate=options[0]
        try:
            if not verify_order(clients[symbol],symbol,row,candidate):
                raise ValueError("CANDIDATE_ORDER_NOT_CLOSED_REDUCE_ONLY")
            proof=settle_order(candidate,bills_by_group[key])
            original_gross=decimal(row["pnl"])
            gap=decimal(proof["exchange_gross_usdt"])-original_gross
            records.append(dict(basic,status="EXCHANGE_SETTLEMENT_TEMPORAL_CANDIDATE",
                order_candidate_count=1,
                candidate_order=candidate,settlement=proof,
                conditional_gross_difference_usdt=str(gap),
                journal_row_ownership_proven=False))
        except Exception as exc:
            records.append(dict(basic,status="UNVERIFIED",
                reason_code=str(exc)[:100]))
    seen_bills=defaultdict(list)
    seen_orders=defaultdict(list)
    for record in records:
        if record["status"]!="EXCHANGE_SETTLEMENT_TEMPORAL_CANDIDATE":
            continue
        oid=(record["symbol"],record["candidate_order"]["order_id"])
        seen_orders[oid].append(record["index"])
        for bid in record["settlement"]["bill_ids"]:
            seen_bills[bid].append(record["index"])
    conflicts=set()
    for r in [*seen_bills.values(),*seen_orders.values()]:
        if len(r)>1: conflicts.update(r)
    for record in records:
        if record["index"] in conflicts:
            record["status"]="AMBIGUOUS"
            record["reason_code"]="CANDIDATE_ORDER_OR_BILL_REUSED"
            record["journal_row_ownership_proven"]=False
    counts=Counter(r["status"] for r in records)
    proved=[r for r in records
            if r["status"]=="EXCHANGE_SETTLEMENT_TEMPORAL_CANDIDATE"]
    sum_net=sum((decimal(r["settlement"]["exchange_cash_change_usdt"])
                 for r in proved),D(0))
    sum_fee=sum((decimal(r["settlement"]["exchange_fee_usdt"])
                 for r in proved),D(0))
    diff=sum((decimal(r["conditional_gross_difference_usdt"])
              for r in proved),D(0))
    return dict(version="v33-39",mode="EXCHANGE_CLOSE_AND_BILL_TEMPORAL_CANDIDATES",
        may_execute=False,observed_at=datetime.now(KST).isoformat(),
        journal_sha256=hashlib.sha256(original).hexdigest(),
        total_close_rows=len(records),counts=dict(counts),
        exchange_order_bill_ids_found=sum(
            len(r["settlement"]["bill_ids"]) for r in proved),
        candidate_total_exchange_cash_change_usdt=str(sum_net),
        candidate_total_exchange_fee_usdt=str(sum_fee),
        hypothetical_gross_difference_usdt=str(diff),
        unavailable_symbol_days=[
            dict(symbol=s,day=day,error_type=error)
            for (s,day),error in sorted(errors.items())],
        records=records,
        caveats=[
          "Time+side+quantity matching does not establish journal row ownership.",
          "All monetary deltas are hypothetical correlations; original PnL remains unchanged.",
          "An OKX order is confirmed closed/reduceOnly and every fill has one settlement bill.",
          "Unmatched or ambiguous historic closes are NOT treated as zero-PnL or error.",
          "No execution or funding policy was modified."])

def save(path,body):
    path=Path(path);fd,tmp=tempfile.mkstemp(dir=path.parent,prefix=".v33_39_")
    try:
        os.fchmod(fd,0o600)
        with os.fdopen(fd,"w",encoding="utf-8") as f:
            json.dump(body,f,ensure_ascii=False,indent=2,allow_nan=False)
            f.flush();os.fsync(f.fileno())
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)

def read_status(user_dir):
    directory=Path(user_dir)
    try:r=json.loads((directory/FILE).read_text())
    except (OSError,ValueError):return dict(status="NOT_COLLECTED",may_execute=False)
    if r.get("version")!="v33-39":
        return dict(status="INVALID_VERSION",may_execute=False)
    try:
        rows=[json.loads(x) for x in (directory/"trades_log.jsonl").read_text().splitlines() if x.strip()]
        invalid=sum(type(x.get("index"))!=int
            or x["index"]>=len(rows)
            or full.digest(rows[x["index"]])!=x.get("row_sha256")
            for x in r["records"])
        return dict(r,status="STALE" if invalid else "KNOWN",
            may_execute=False,source_valid=(not invalid),
            invalidated_cases=invalid)
    except (ValueError,KeyError,TypeError,IndexError,OSError):
        return dict(status="UNAVAILABLE",may_execute=False)

def main():
    import argparse,config,okx_client
    p=argparse.ArgumentParser();p.add_argument("--user-dir",required=True)
    args=p.parse_args()
    conf=config.UserConfig(args.user_dir)
    report=audit(args.user_dir,lambda s:okx_client.OkxClient(s,conf))
    save(Path(args.user_dir)/FILE,report)
    print(json.dumps(dict(version=report["version"],
        total=report["total_close_rows"],counts=report["counts"],
        bill_ids=report["exchange_order_bill_ids_found"]),ensure_ascii=False))
if __name__=="__main__":raise SystemExit(main())
