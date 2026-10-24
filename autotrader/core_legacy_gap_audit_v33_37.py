"""v33-37 historical missing financial fields and legacy estimated reduction audit.

Proof boundary: matching time+side+size creates a CANDIDATE, never VERIFIED.
The original journal remains immutable. No order creates, cancels, or changes.
"""
import hashlib,json,os,tempfile
from collections import Counter,defaultdict
from datetime import datetime,timedelta,timezone
from decimal import Decimal,InvalidOperation
from pathlib import Path
import core_full_ledger_audit_v33_36 as full

FILE="core_legacy_gap_audit_v33_37.json"
KST=timezone(timedelta(hours=9))
EPS=Decimal("0.0000001")
LEGACY_ESTIMATED_START="2026-09-11"
LEGACY_ESTIMATED_END="2026-09-14"

def required_rows(user_dir):
    old=full.read_status(user_dir)
    if not old.get("source_valid") or old.get("version")!="v33-36":
        raise ValueError("V33_36_EVIDENCE_STALE")
    raw=(Path(user_dir)/"trades_log.jsonl").read_bytes()
    rows=[json.loads(x) for x in raw.decode().splitlines() if x.strip()]
    incomplete=[e for e in old["events"] if e.get("numeric_invariant")=="INCOMPLETE"
        and e.get("type") in ("close","reduce")]
    unverified=[e for e in old["events"] if e.get("type")=="close"
        and e.get("status")=="UNVERIFIED"]
    for ev in incomplete+unverified:
        i=ev["row_index"]
        if i>=len(rows) or full.digest(rows[i])!=ev["row_sha256"]:
            raise ValueError("SOURCE_JOURNAL_HASH_CHANGED")
    return rows,raw,incomplete,unverified

def gap_reason(row):
    if row.get("type")=="reduce" and row.get("pnl_source")=="estimated":
        return "LEGACY_ESTIMATED_REDUCE_MISSING_NET_AND_ORDER_ID"
    missing=[k for k in ("pnl","fee","okx_net_pnl") if row.get(k) is None]
    if missing==["fee","okx_net_pnl"]:
        return "LEGACY_CLOSE_MISSING_FEE_AND_NET"
    if missing==["okx_net_pnl"]:
        return "LEGACY_CLOSE_MISSING_NET"
    return "OTHER_INCOMPLETE_MONETARY_FIELDS"

def D(value):
    number=Decimal(str(value))
    if not number.is_finite():raise InvalidOperation("NONFINITE")
    return number

def trade_time(trade):
    try:return int(trade["timestamp"])
    except (ValueError,TypeError,KeyError):return None
def candidate_trade(row,trades,tolerance_s=150):
    """Only exact quantity/side and near time; never assert ownership."""
    if row.get("execution_id") or row.get("type")!="reduce":
        return [],"ROW_ALREADY_IDENTIFIED_OR_NOT_REDUCE"
    try:
        when=datetime.fromisoformat(row["time"]).replace(tzinfo=KST)
        stamp=int(when.timestamp()*1000)
        quantity=D(row["amount"])
        if quantity<=0:raise ValueError("INVALID_QUANTITY")
        side="sell" if row["side"]=="long" else "buy"
    except (KeyError,ValueError,TypeError,InvalidOperation):
        return [],"BAD_LOCAL_ROW"
    matches=[]
    for item in trades:
        try:
            ms=trade_time(item)
            if ms is None or abs(ms-stamp)>tolerance_s*1000:continue
            if item.get("side")!=side:continue
            if abs(D(item["amount"])-quantity)>max(EPS,quantity*D("0.0000001")):continue
            orderid=str(item.get("order") or "")
            tradeid=str(item.get("id") or "")
            if not orderid or not tradeid:continue
            matches.append(dict(order_id=orderid,trade_id=tradeid,
                fill_ms=ms,delay_ms=ms-stamp,
                qty=str(item["amount"]),fill_price=str(item.get("price"))))
        except (KeyError,ValueError,TypeError,InvalidOperation):continue
    if len(matches)==1:return matches,"UNIQUE_TIME_QTY_SIDE_CANDIDATE"
    if len(matches)>1:return matches,"AMBIGUOUS_MULTIPLE_CANDIDATES"
    return [],"NO_TIME_QTY_SIDE_CANDIDATE"

def fetch_symbol_day(client,symbol,day):
    start=datetime.fromisoformat(day).replace(tzinfo=KST)
    since=int(start.timestamp()*1000)-180000
    until=since+86400000+360000
    def fetch_window(lo,hi,depth=0):
        raw=client.exchange.fetch_my_trades(symbol,since=lo,limit=100,
                                             params={"until":hi})
        if not isinstance(raw,list):
            raise ValueError("TRADE_HISTORY_UNAVAILABLE")
        if len(raw)>=100:
            if depth>=9 or hi-lo<=1000:
                raise ValueError("HISTORIC_TRADES_WINDOW_SATURATED")
            mid=(lo+hi)//2
            return fetch_window(lo,mid,depth+1)+fetch_window(mid+1,hi,depth+1)
        if any(trade_time(t) is None for t in raw):
            raise ValueError("TRADE_TIMESTAMP_MISSING")
        return [t for t in raw if lo<=trade_time(t)<=hi]
    result=fetch_window(since,until)
    by_id={}
    for fill in result:
        key=str(fill.get("id") or "")
        if not key:
            raise ValueError("TRADE_ID_MISSING")
        if key in by_id and by_id[key]!=fill:
            raise ValueError("TRADE_ID_DUPLICATED_WITH_DIFFERENT_DATA")
        by_id[key]=fill
    return list(by_id.values())

def cohort(rows,incomplete):
    groups=defaultdict(list)
    for item in incomplete:
        i=item["row_index"]
        row=rows[i]
        if row.get("type")=="reduce" and row.get("pnl_source")=="estimated":
            groups[(row["symbol"],row["time"][:10])].append(i)
    return groups
def inspect(user_dir,client_factory):
    folder=Path(user_dir)
    rows,raw,incomplete,unverified=required_rows(folder)
    groups=cohort(rows,incomplete)
    clients={};caches={};errors={}
    for symbol,day in sorted(groups):
        try:
            if symbol not in clients:clients[symbol]=client_factory(symbol)
            caches[(symbol,day)]=fetch_symbol_day(clients[symbol],symbol,day)
        except Exception as ex:
            errors[(symbol,day)]=type(ex).__name__
    matches={};counts=Counter()
    for (symbol,day),indices in sorted(groups.items()):
        trade_rows=caches.get((symbol,day))
        for i in indices:
            if trade_rows is None:
                matches[i]=dict(status="UNAVAILABLE",reason=errors.get((symbol,day),
                     "HISTORIC_TRADE_HISTORY_UNAVAILABLE"),candidates=[])
                continue
            found,why=candidate_trade(rows[i],trade_rows)
            if why=="UNIQUE_TIME_QTY_SIDE_CANDIDATE":
                status="CANDIDATE_ONLY_NOT_PROVEN"
            elif found:status="AMBIGUOUS"
            else:status="NO_CANDIDATE"
            matches[i]=dict(status=status,reason=why,candidates=found)
    # Distinct journal rows must not silently claim the same trade.
    ids=defaultdict(set)
    for i,match in matches.items():
        for t in match["candidates"]:
            ids[(t["order_id"],t["trade_id"])].add(i)
    collisions={key:indices for key,indices in ids.items() if len(indices)>1}
    for (oid,tid),indices in collisions.items():
        for i in indices:
            matches[i]["status"]="AMBIGUOUS"
            matches[i]["reason"]="TRADE_CANDIDATE_REUSED_ACROSS_ROWS"
    # Further validate that a *candidate* is an actual finished exchange
    # reduce-only order. This still cannot establish journal ownership.
    order_checks=Counter()
    for i,match in matches.items():
        if match["status"]!="CANDIDATE_ONLY_NOT_PROVEN":
            continue
        row=rows[i]
        found=match["candidates"][0]
        try:
            exchange_order=clients[row["symbol"]].exchange.fetch_order(
                found["order_id"],row["symbol"])
            raw_order=exchange_order.get("info") or {}
            match_ok=(
                str(raw_order.get("ordId"))==found["order_id"]
                and exchange_order.get("status")=="closed"
                and str(raw_order.get("reduceOnly")).lower() in ("true","1")
                and exchange_order.get("side")==("sell" if row["side"]=="long" else "buy")
                and exchange_order.get("symbol")==row["symbol"]
                and abs(D(exchange_order["filled"])-D(row["amount"]))<=max(
                    EPS,D(row["amount"])*D("0.0000001")))
            match["order_check"]=(
                "REDUCE_ONLY_EXCHANGE_ORDER_CONFIRMED_TEMPORAL_ONLY"
                if match_ok else "EXCHANGE_ORDER_CONFLICT")
        except Exception:
            match["order_check"]="EXCHANGE_ORDER_GET_UNAVAILABLE"
        order_checks[match["order_check"]]+=1
    for i,x in matches.items():counts[x["status"]]+=1
    detail=[]
    for ev in incomplete:
        i=ev["row_index"];r=rows[i]
        info=dict(index=i,time=r["time"],symbol=r["symbol"],
            type=r["type"],row_sha256=full.digest(r),
            defect=gap_reason(r),has_execution_id=bool(r.get("execution_id")),
            estimated_gross=(str(r.get("pnl")) if r.get("pnl") is not None else None),
            candidate=matches.get(i))
        detail.append(info)
    by_defect=dict(Counter(d["defect"] for d in detail))
    close_reasons=dict(Counter(rows[x["row_index"]].get("reason")
        for x in unverified))
    return dict(version="v33-37",mode="HISTORIC_READ_ONLY_UNPROVEN_EXECUTION_PROBE",
        may_execute=False,observed_at=datetime.now(KST).isoformat(),
        source_journal_sha256=hashlib.sha256(raw).hexdigest(),
        total_incomplete=len(detail),
        incomplete_by_defect=by_defect,
        unverified_full_closes=len(unverified),
        unverified_full_closes_by_reason=close_reasons,
        estimated_partial_reductions=len(matches),
        estimated_reduction_candidates=dict(counts),
        collision_trade_keys=len(collisions),
        candidate_order_check_counts=dict(order_checks),
        downloaded_symbol_day_count=len(caches),
        unavailable_symbol_days=[
            dict(symbol=s,day=day,error_type=reason)
            for (s,day),reason in sorted(errors.items())],
        cases=detail,limitations=[
            "A candidate matched by clock/side/quantity is NOT an authoritative journal-to-OKX order link.",
            "Candidate amounts are not written into the original journal or counted as verified PnL.",
            "Missing net PnL and fees remain unverified until independent exchange ownership is established.",
            "Some historical OKX trades may fall outside a paginated/retained API window.",
            "No trade orders or trading policy were modified."])
def save_atomic(path,body):
    p=Path(path);fd,tmp=tempfile.mkstemp(dir=p.parent,prefix=".v33_37_")
    try:
        os.fchmod(fd,0o600)
        with os.fdopen(fd,"w",encoding="utf-8") as file:
            json.dump(body,file,ensure_ascii=False,indent=2,allow_nan=False)
            file.flush();os.fsync(file.fileno())
        os.replace(tmp,p)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)

def read_status(user_dir):
    directory=Path(user_dir)
    try:r=json.loads((directory/FILE).read_text())
    except (OSError,ValueError):return dict(status="NOT_COLLECTED",may_execute=False)
    if r.get("version")!="v33-37":
        return dict(status="INVALID_VERSION",may_execute=False)
    try:
        source=(directory/"trades_log.jsonl").read_bytes()
        current=[json.loads(x) for x in source.decode().splitlines() if x.strip()]
        invalid=sum(1 for x in r["cases"] if
            x["index"]>=len(current) or full.digest(current[x["index"]])!=x["row_sha256"])
        newer=(hashlib.sha256(source).hexdigest()!=r["source_journal_sha256"])
        return dict(r,status="STALE" if invalid or newer else "KNOWN",
            may_execute=False,invalidated_rows=invalid,journal_changed=newer,
            source_valid=(not invalid and not newer))
    except (ValueError,KeyError,TypeError,IndexError,OSError):
        return dict(status="UNAVAILABLE",may_execute=False)

def main():
    import argparse,config,okx_client
    ap=argparse.ArgumentParser();ap.add_argument("--user-dir",required=True)
    args=ap.parse_args()
    conf=config.UserConfig(args.user_dir)
    result=inspect(args.user_dir,lambda s:okx_client.OkxClient(s,conf))
    save_atomic(Path(args.user_dir)/FILE,result)
    print(json.dumps(dict(
        version=result["version"],incomplete=result["total_incomplete"],
        incomplete_by_defect=result["incomplete_by_defect"],
        unverified_closes=result["unverified_full_closes"],
        estimated_reduction_candidates=result["estimated_reduction_candidates"],
        unavailable_days=result["unavailable_symbol_days"],
        collisions=result["collision_trade_keys"]),ensure_ascii=False))
if __name__=="__main__":main()
