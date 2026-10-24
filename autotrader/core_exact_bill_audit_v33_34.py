"""v33-34 audited transaction fees by exact OKX order+trade IDs.

Read-only. Raw account bills are queried in date-scoped pages and matched to
the entry/partial/exit fills. No price/time guess or ledger modification.
"""
import json,hashlib,os,tempfile
from pathlib import Path
from decimal import Decimal as D
from datetime import datetime,timedelta,timezone
from collections import Counter,defaultdict
import core_position_pnl_audit_v33_34 as monetary
import core_missing_entry_recovery_v33_34 as missing
import core_exit_outcome_join_v33_25 as ledger

FILE="core_exact_trade_bills_v33_34.json"
KST=timezone(timedelta(hours=9))

def fetch_bills(exchange,symbol,begin,end,*,kind="2",max_calls=48):
    """Split saturated 100-row time ranges; fail closed on gaps."""
    inst=symbol.split("/")[0]+"-USDT-SWAP"
    start=datetime.fromtimestamp(begin/1000,KST)
    day=datetime(start.year,start.month,start.day,tzinfo=KST)
    windows=[]
    while day.timestamp()*1000<=end:
        lo=max(begin,int(day.timestamp()*1000))
        hi=min(end,int(day.timestamp()*1000)+86400000-1)
        windows.append((lo,hi,0))
        day+=timedelta(days=1)
    output={};n=0
    while windows:
        lo,hi,depth=windows.pop(0)
        if n+2>max_calls:raise ValueError("BILL_API_QUERY_BUDGET_EXCEEDED")
        data_by_id={}
        for method in (exchange.private_get_account_bills_archive,
                       exchange.private_get_account_bills):
            packet=method(dict(type=kind,instId=inst,
                begin=str(lo),end=str(hi),limit="100")) or {}
            n+=1
            records=packet.get("data") or []
            if len(records)>=100:
                if depth>=12 or hi-lo<1000:
                    raise ValueError("BILL_WINDOW_SATURATED")
                middle=(lo+hi)//2
                windows[0:0]=[(lo,middle,depth+1),(middle+1,hi,depth+1)]
                break
            for raw in records:
                bid=str(raw.get("billId") or "")
                ts=int(raw.get("ts") or 0)
                if not bid or not lo<=ts<=hi:raise ValueError("BILL_ID_OR_TIME_INVALID")
                if bid in data_by_id and data_by_id[bid]!=raw:
                    raise ValueError("BILL_DUPLICATE_DISAGREEMENT")
                data_by_id[bid]=raw
        else:
            for bid,raw in data_by_id.items():
                if bid in output and output[bid]!=raw:
                    raise ValueError("BILL_CROSS_WINDOW_DISAGREEMENT")
                output[bid]=raw
    return output,n
def precise_trades(client,orderid,since_ms,expected_ids=None):
    trades=client.fetch_trades_for_order(str(orderid),max(0,int(since_ms)-60000),limit=100)
    if not isinstance(trades,list) or not trades or len(trades)>=100:
        raise ValueError("FILL_HISTORY_NOT_COMPLETE")
    found={}
    for t in trades:
        raw=t.get("info") or {}
        tid=str(raw.get("tradeId") or t.get("id") or "")
        if (not tid or str(raw.get("ordId") or t.get("order") or "")!=str(orderid)):
            raise ValueError("TRADE_ORDER_ID_CONFLICT")
        if tid in found and found[tid]!=t:
            raise ValueError("FILL_DUPLICATE_CONFLICT")
        found[tid]=t
    if expected_ids is not None and set(found)!=set(map(str,expected_ids)):
        raise ValueError("FILL_IDS_DO_NOT_MATCH_JOURNAL_PROOF")
    return found

def verify_bill_fees(proof,rows,client,bills):
    i=proof["close_index"]
    symbol=proof["symbol"]
    begin=int(proof["created_ms"])
    expected_orders=[]
    expected_orders.append((str(proof["entry_order_id"]),begin,None))
    for ref in proof["trade_rows"][:-1]:
        r=rows[ref["index"]]
        ev=r.get("close_execution_evidence") or {}
        if (ev.get("price_basis")!="verified_order_fills"
            or not ev.get("order_id") or not ev.get("trade_ids")):
            raise ValueError("REDUCE_EXECUTION_EVIDENCE_INCOMPLETE")
        expected_orders.append((str(ev["order_id"]),
            int(ev.get("first_fill_timestamp_ms") or begin),ev["trade_ids"]))
    expected_orders.append((str(proof["close_order_id"]),begin,None))
    all_trades={}
    for orderid,start,ids in expected_orders:
        if not orderid:raise ValueError("ORDER_ID_MISSING")
        found=precise_trades(client,orderid,start,expected_ids=ids)
        for tradeid,t in found.items():
            key=(orderid,tradeid)
            if key in all_trades:raise ValueError("FILL_REUSED_WITHIN_POSITION")
            all_trades[key]=t
    bill_lookup=defaultdict(list)
    for billid,raw in bills.items():
        if (str(raw.get("type"))!="2" or raw.get("ccy")!="USDT"):
            continue
        key=(str(raw.get("ordId") or ""),str(raw.get("tradeId") or ""))
        bill_lookup[key].append((billid,raw))
    all_bill_ids=set()
    total=D(0)
    for key in all_trades:
        candidates=bill_lookup.get(key) or []
        if len(candidates)!=1:
            raise ValueError("FILL_BILL_MISSING_OR_DUPLICATED")
        bid,bill=candidates[0]
        if bid in all_bill_ids:raise ValueError("BILL_REUSED")
        all_bill_ids.add(bid)
        total+=-D(str(bill["fee"]))
    # No additional bills may exist for the same owned orders but differ
    # from the verified trade set, or the joined fee would be incomplete.
    owned={item[0] for item in expected_orders}
    unexplained=[bid for bid,r in bills.items() if str(r.get("type"))=="2"
        and str(r.get("ordId") or "") in owned and bid not in all_bill_ids]
    if unexplained:raise ValueError("UNATTRIBUTED_BILL_FOR_OWNED_ORDER")
    expected=D(proof["exchange_money"]["fee"])
    journal_fee=D(proof["journal_money"]["fee"])
    return dict(status="MATCH" if abs(total-expected)<=D(".0001")
                and abs(total-journal_fee)<=D(".0001") else "DIFFERENCE",
                fee_from_exact_bill_ids=str(total),
                okx_position_fee=str(expected),
                journal_fee=str(journal_fee),
                matched_fill_count=len(all_trades),
                matched_bill_count=len(all_bill_ids),
                matched_bill_ids=sorted(all_bill_ids))
def collect(user_dir,client_factory):
    folder=Path(user_dir)
    raw=(folder/"trades_log.jsonl").read_bytes()
    rows=[json.loads(x) for x in raw.decode().splitlines() if x.strip()]
    parent=missing.read_report(folder)
    if parent.get("status")!="KNOWN" or parent.get("invalidated"):
        raise ValueError("MISSING_ENTRY_PROVENANCE_CHANGED")
    records=[]
    clients={}
    for proof in parent.get("records",[]):
        if proof.get("status")!="MATCH":continue
        i=proof["close_index"]
        if i>=len(rows) or monetary.hash_row(rows[i])!=proof["close_sha256"]:
            raise ValueError("SOURCE_JOURNAL_CHANGED")
        symbol=proof["symbol"]
        if symbol not in clients:clients[symbol]=client_factory(symbol)
        begin=int(proof["created_ms"])-120000
        end=int(ledger.dt_kst(proof["time"]).timestamp()*1000)+120000
        details=dict(close_index=i,symbol=symbol,close_sha256=proof["close_sha256"])
        try:
            fees,queries=fetch_bills(clients[symbol].exchange,symbol,begin,end,kind="2")
            result=verify_bill_fees(proof,rows,clients[symbol],fees)
            details.update(result,read_only_bill_calls=queries)
        except Exception as err:
            details.update(status="UNVERIFIED",reason=type(err).__name__,
                           explanation=str(err)[:130])
        try:
            funding,funding_calls=fetch_bills(
                clients[symbol].exchange,symbol,begin,end,kind="8")
            funded=sum((D(str(b.get("balChg") or 0)) for b in funding.values()
                if str(b.get("type"))=="8" and b.get("ccy")=="USDT"),D(0))
            details["funding_interval_only"]=dict(
                amount_usdt=str(funded),
                okx_position_funding=str(proof["exchange_money"]["funding"]),
                correlation_only=True,
                bill_ids=sorted(funding))
        except Exception as err:
            details["funding_interval_only"]=dict(
                correlation_only=True,status="UNAVAILABLE",error_type=type(err).__name__)
        records.append(details)
    counts=dict(Counter(x["status"] for x in records))
    all_ids=[bid for record in records for bid in record.get("matched_bill_ids",[])]
    if len(set(all_ids))!=len(all_ids):
        raise ValueError("FEE_BILL_CLAIMED_BY_MULTIPLE_POSITIONS")
    return dict(version="v33-34",mode="EXACT_ORDER_TRADE_FEE_BILLS_GET_ONLY",
        may_execute=False,observed_at=datetime.now(KST).isoformat(),
        journal_sha256=hashlib.sha256(raw).hexdigest(),
        counts=counts,unique_fee_bill_ids=len(set(all_ids)),
        records=records,
        caveats=[
            "Fee proof requires exact ordId+tradeId matching of one bill per fill.",
            "Raw funding bills lack a position ID: same-symbol interval evidence is NOT exact ownership proof.",
            "Only recovered ten positions are covered by this bill-level audit.",
            "Original journal and actual live strategy are unchanged."])

def save(path,data):
    p=Path(path);fd,tmp=tempfile.mkstemp(dir=p.parent,prefix=".v33_34_fee_")
    try:
        os.fchmod(fd,0o600)
        with os.fdopen(fd,"w",encoding="utf-8") as fh:
            json.dump(data,fh,ensure_ascii=False,indent=2,allow_nan=False)
            fh.flush();os.fsync(fh.fileno())
        os.replace(tmp,p)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)

def read_status(user_dir):
    try:state=json.loads((Path(user_dir)/FILE).read_text())
    except (OSError,ValueError):
        return dict(status="NOT_COLLECTED",may_execute=False)
    if state.get("version")!="v33-34":
        return dict(status="VERSION_CONFLICT",may_execute=False)
    rows,_=ledger.read_lines(Path(user_dir)/"trades_log.jsonl")
    valid=[]
    for item in state.get("records",[]):
        idx=item.get("close_index")
        if type(idx)!=int or idx<0 or idx>=len(rows):continue
        if monetary.hash_row(rows[idx])!=item.get("close_sha256"):continue
        valid.append(item)
    matched=[x for x in valid if x.get("status")=="MATCH"]
    ids=[bid for x in matched for bid in x.get("matched_bill_ids",[])]
    if len(ids)!=len(set(ids)):
        return dict(status="BILL_DUPLICATE_CONFLICT",may_execute=False)
    return dict(state,status="KNOWN",may_execute=False,
        source_valid=(len(valid)==len(state.get("records",[]))),
        bound_bill_matches=len(matched),
        invalidated=len(state.get("records",[]))-len(valid),
        bound_exact_bill_ids=len(ids))

def main():
    import argparse,config,okx_client
    p=argparse.ArgumentParser();p.add_argument("--user-dir",required=True)
    args=p.parse_args()
    cfg=config.UserConfig(args.user_dir)
    report=collect(args.user_dir,lambda s:okx_client.OkxClient(s,cfg))
    save(Path(args.user_dir)/FILE,report)
    print(json.dumps(dict(counts=report["counts"],
        unique_bill_ids=report["unique_fee_bill_ids"]),ensure_ascii=False))
if __name__=="__main__":main()
