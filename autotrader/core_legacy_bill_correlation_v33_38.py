"""v33-38: forensic correlation of OKX settlement bills to 80 historic close candidates.

An exact (ordId, tradeId, ts) exchange bill proves the bill belongs to the
candidate fill; it NEVER proves that fill belongs to an old journal row without
a recorded authoritative execution identifier. Only classify as CANDIDATE.
No writes to trading engine, position or journal.
"""
import hashlib,json,os,tempfile
from collections import Counter,defaultdict
from datetime import datetime,timedelta,timezone
from decimal import Decimal,InvalidOperation
from pathlib import Path
import core_legacy_gap_audit_v33_37 as previous
import core_exact_bill_audit_v33_34 as bill_api
import core_full_ledger_audit_v33_36 as whole

FILE="core_legacy_bill_correlation_v33_38.json"
KST=timezone(timedelta(hours=9))
TOL=Decimal("0.00000001")

def D(v):
    if v is None or isinstance(v,bool):raise ValueError("MISSING_NUMBER")
    n=Decimal(str(v))
    if not n.is_finite():raise ValueError("NONFINITE_NUMBER")
    return n

def candidate_rows(root,rows):
    # Append-only new trades do not invalidate old immutable case links.
    # Read stored report but bind *all* historic missing-financial row hashes.
    file=Path(root)/previous.FILE
    old=json.loads(file.read_text())
    if old.get("version")!="v33-37":
        raise ValueError("V33_37_SOURCE_REPORT_INVALID")
    source_cases=old.get("cases") or []
    if len(source_cases)!=347 or any(
        type(x.get("index"))!=int or x["index"]>=len(rows)
        or whole.digest(rows[x["index"]])!=x.get("row_sha256")
        for x in source_cases):
        raise ValueError("V33_37_HISTORIC_ROW_PROVENANCE_CHANGED")
    cases=[x for x in old["cases"] if isinstance(x.get("candidate"),dict)
        and x["candidate"].get("status")=="CANDIDATE_ONLY_NOT_PROVEN"
        and x["candidate"].get("order_check")==
            "REDUCE_ONLY_EXCHANGE_ORDER_CONFIRMED_TEMPORAL_ONLY"]
    if len(cases)!=80:raise ValueError("UNEXPECTED_CANDIDATE_COHORT")
    seen=set()
    for x in cases:
        idx=x["index"]
        if idx in seen or idx>=len(rows) or whole.digest(rows[idx])!=x["row_sha256"]:
            raise ValueError("DUPLICATE_OR_TAMPERED_ROW")
        seen.add(idx)
        if len(x["candidate"].get("candidates",[]))!=1:
            raise ValueError("AMBIGUOUS_CANDIDATE")
    return cases,old
def settle_candidate(row,case,fee_bills):
    link=case["candidate"]["candidates"][0]
    oid,tid=link["order_id"],link["trade_id"]
    eligible=[]
    for billid,b in fee_bills.items():
        if (str(b.get("type"))=="2" and b.get("ccy")=="USDT"
            and str(b.get("ordId"))==oid and str(b.get("tradeId"))==tid):
            eligible.append((billid,b))
    if len(eligible)!=1:
        return dict(status="UNVERIFIED",reason=(
            "FEE_BILL_MISSING" if not eligible else "FEE_BILL_DUPLICATED"))
    billid,b=eligible[0]
    try:
        if abs(int(b["ts"])-int(link["fill_ms"]))>1000:
            raise ValueError("BILL_TIME_CONFLICT")
        gross=D(b["pnl"]);signed_fee=D(b["fee"]);net=D(b["balChg"])
        if (gross+signed_fee-net).copy_abs()>TOL:
            raise ValueError("BILL_GROSS_FEE_NET_INVARIANT_FAILED")
        estimated=D(row["pnl"])
        previous_fee=D(row.get("fee") or 0)
        estimated_net=estimated-previous_fee
        return dict(status="BILL_VERIFIED_FOR_FILL_ONLY",
            reason="EXACT_OKX_ORDER_TRADE_AND_BILL_ID_MATCH",
            exchange_order_id=oid,exchange_trade_id=tid,
            exchange_bill_id=str(billid),
            exchange_fill_ms=int(link["fill_ms"]),
            journal_estimated_gross_usdt=str(estimated),
            journal_recorded_fee_usdt=str(previous_fee),
            journal_implied_net_usdt=str(estimated_net),
            exchange_bill_gross_usdt=str(gross),
            exchange_bill_fee_usdt=str(-signed_fee),
            exchange_bill_cash_change_usdt=str(net),
            hypothetical_gross_delta_usdt=str(gross-estimated),
            hypothetical_net_delta_usdt=str(net-estimated_net),
            row_to_order_ownership_proven=False)
    except (ValueError,TypeError,InvalidOperation,KeyError) as ex:
        return dict(status="UNVERIFIED",reason=str(ex)[:80])

def ai_log_index(root):
    path=Path(root)/"position_ai_log.jsonl"
    raw=path.read_bytes()
    indexed=defaultdict(list)
    for line in raw.splitlines():
        if not line.strip():continue
        if b'"position_ai_reduce_50"' not in line:continue
        event=json.loads(line)
        if event.get("event_type")=="position_ai_reduce_50":
            indexed[event.get("symbol")].append(event)
    return indexed,hashlib.sha256(raw).hexdigest()

def match_ai_local_action(case,row,events):
    stamp=datetime.fromisoformat(case["time"])
    matches=[]
    for ev in events:
        try:
            t=datetime.fromisoformat(ev["time"])
            if abs((t-stamp).total_seconds())>6:continue
            qty=D(ev["reduced_contracts"])
            if abs(qty-D(row["amount"]))>max(TOL,qty*D("0.0000001")):
                continue
            matches.append(ev)
        except (ValueError,TypeError,InvalidOperation,KeyError):
            continue
    if len(matches)!=1:return dict(
        status="AMBIGUOUS_OR_MISSING_LOCAL_AI_REDUCTION",
        matches=len(matches),order_id_stored=False)
    return dict(status="LOCAL_AI_REDUCTION_TIME_QTY_MATCH",
        review_id=matches[0].get("review_id"),
        logged_at=matches[0].get("time"),order_id_stored=False)

def audit(user_dir,client_factory):
    root=Path(user_dir)
    original=(root/"trades_log.jsonl").read_bytes()
    rows=[json.loads(line) for line in original.decode().splitlines() if line.strip()]
    cohort,baseline=candidate_rows(root,rows)
    ai_events,ai_sha256=ai_log_index(root)
    groups=defaultdict(list)
    for case in cohort:
        groups[(case["symbol"],case["time"][:10])].append(case)
    cache={};clients={};errors={}
    for (symbol,day),cases in sorted(groups.items()):
        if symbol not in clients:clients[symbol]=client_factory(symbol)
        begin=int(datetime.fromisoformat(day).replace(tzinfo=KST).timestamp()*1000)
        try:
            cache[(symbol,day)],requests=bill_api.fetch_bills(
                clients[symbol].exchange,symbol,begin,begin+86400000-1,
                kind="2",max_calls=100)
        except Exception as ex:
            errors[(symbol,day)]=type(ex).__name__
    records=[]
    for case in cohort:
        index=case["index"];key=(case["symbol"],case["time"][:10])
        if key not in cache:
            result=dict(status="UNVERIFIED",
                reason="BILL_QUERY_UNAVAILABLE_"+errors.get(key,"UNKNOWN"))
        else:
            result=settle_candidate(rows[index],case,cache[key])
        records.append(dict(row_index=index,row_sha256=whole.digest(rows[index]),
            symbol=case["symbol"],time=case["time"],
            ai_action_correlation=match_ai_local_action(
                case,rows[index],ai_events.get(case["symbol"],[])),**result))
    statuses=Counter(r["status"] for r in records)
    proven=[r for r in records if r["status"]=="BILL_VERIFIED_FOR_FILL_ONLY"]
    billids=[r["exchange_bill_id"] for r in proven]
    if len(billids)!=len(set(billids)):
        raise ValueError("BILL_ID_REUSED_ACROSS_CANDIDATES")
    money_keys=("journal_estimated_gross_usdt","journal_recorded_fee_usdt",
        "journal_implied_net_usdt","exchange_bill_gross_usdt",
        "exchange_bill_fee_usdt","exchange_bill_cash_change_usdt",
        "hypothetical_gross_delta_usdt","hypothetical_net_delta_usdt")
    sums={key:str(sum((D(x[key]) for x in proven),Decimal(0))) for key in money_keys}
    return dict(version="v33-38",mode="HISTORICAL_CANDIDATE_BILL_CORRELATION",
        may_execute=False,observed_at=datetime.now(KST).isoformat(),
        journal_sha256=hashlib.sha256(original).hexdigest(),
        position_ai_log_sha256=ai_sha256,
        ai_action_match_counts=dict(Counter(
            x["ai_action_correlation"]["status"] for x in records)),
        candidate_cohort=len(cohort),status_counts=dict(statuses),
        exact_candidate_bill_count=len(proven),
        unique_bills=len(billids),
        comparison_totals_usdt=sums,
        billing_unavailable_days=[
            dict(symbol=s,day=day,error_type=why)
            for (s,day),why in sorted(errors.items())],
        cases=records,
        limitations=[
            "EXACT order/trade/bill ID links fee to the candidate fill, NOT fill ownership to historic journal row.",
            "AI REDUCE_50 logs independently establish local decision execution by time and size, but record no exchange order ID.",
            "Hypothetical money deltas are NOT journal errors, corrections, verified PnL, or account performance.",
            "No updated PnL is written into trades_log; ambiguous and unmatched historic closes remain unverified.",
            "Funding may occur separately and is not included in exchange fee-bill cash changes.",
            "This audit does not validate every historic exit, only the 80 temporal close-fill candidates."])

def save_atomic(path,data):
    p=Path(path)
    fd,temporary=tempfile.mkstemp(dir=p.parent,prefix=".v33_38_")
    try:
        os.fchmod(fd,0o600)
        with os.fdopen(fd,"w",encoding="utf-8") as f:
            json.dump(data,f,ensure_ascii=False,indent=2,allow_nan=False)
            f.flush();os.fsync(f.fileno())
        os.replace(temporary,p)
    finally:
        if os.path.exists(temporary):os.unlink(temporary)

def read_status(user_dir):
    directory=Path(user_dir)
    try: report=json.loads((directory/FILE).read_text())
    except (OSError,ValueError):
        return dict(status="NOT_COLLECTED",may_execute=False)
    if report.get("version")!="v33-38":
        return dict(status="INVALID_VERSION",may_execute=False)
    try:
        raw=(directory/"trades_log.jsonl").read_bytes()
        rows=[json.loads(x) for x in raw.decode().splitlines() if x.strip()]
        invalid=sum(
            1 for x in report.get("cases",[])
            if x["row_index"]>=len(rows) or
            whole.digest(rows[x["row_index"]])!=x["row_sha256"])
        changed=hashlib.sha256(raw).hexdigest()!=report.get("journal_sha256")
        ai,ai_current=ai_log_index(directory)
        ai_invalid=0
        for evidence in report.get("cases",[]):
            local=evidence.get("ai_action_correlation") or {}
            if local.get("status")!="LOCAL_AI_REDUCTION_TIME_QTY_MATCH":
                continue
            index=evidence["row_index"]
            if index>=len(rows):
                ai_invalid+=1;continue
            current=match_ai_local_action(
                evidence,rows[index],ai.get(evidence["symbol"],[]))
            if (current.get("status")!=local.get("status")
                or current.get("review_id")!=local.get("review_id")
                or current.get("logged_at")!=local.get("logged_at")):
                ai_invalid+=1
        invalid+=ai_invalid
        return dict(report,status="STALE" if invalid else "KNOWN",
            may_execute=False,source_valid=(not invalid),
            invalidated_cases=invalid,journal_changed=changed,
            ai_log_appended_since_audit=(ai_current!=report.get("position_ai_log_sha256")),
            append_only_new_rows_do_not_invalidate_historical_case_proof=True)
    except (ValueError,TypeError,KeyError,IndexError):
        return dict(status="UNAVAILABLE",may_execute=False)

def main():
    import argparse,config,okx_client
    parser=argparse.ArgumentParser()
    parser.add_argument("--user-dir",required=True)
    opts=parser.parse_args()
    config_obj=config.UserConfig(opts.user_dir)
    report=audit(opts.user_dir,
        lambda sym:okx_client.OkxClient(sym,config_obj))
    save_atomic(Path(opts.user_dir)/FILE,report)
    print(json.dumps(dict(version=report["version"],
        status_counts=report["status_counts"],
        unique_bills=report["unique_bills"],
        hypothetical_net_delta=report["comparison_totals_usdt"]["hypothetical_net_delta_usdt"]),
        ensure_ascii=False))
    return 0
if __name__=="__main__":raise SystemExit(main())
