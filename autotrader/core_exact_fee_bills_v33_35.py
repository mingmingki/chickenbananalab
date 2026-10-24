import json
from pathlib import Path
from collections import Counter
from datetime import datetime,timedelta,timezone
import hashlib,os,tempfile,time
import core_position_pnl_audit_v33_34 as positions
import core_external_close_audit_v33_31 as old_close
import core_external_close_recovery_v33_32 as recovered_close
import core_missing_entry_recovery_v33_34 as recovered_entry
import core_exact_bill_audit_v33_34 as bills
import core_exit_outcome_join_v33_25 as ledger

FILE="core_exact_fee_bills_v33_35.json"
KST=timezone(timedelta(hours=9))
def read_rows(root):
    raw=(Path(root)/"trades_log.jsonl").read_bytes()
    rows=[json.loads(x) for x in raw.decode().splitlines() if x.strip()]
    return rows,hashlib.sha256(raw).hexdigest()

def scope(root,rows):
    positional=positions.read_report(root)
    reconstructed=recovered_entry.read_report(root)
    v31=old_close.read_status(root)
    v32=recovered_close.read_report(root)
    if (positional.get("status")!="KNOWN"
        or positional.get("invalidated_position_proofs")
        or reconstructed.get("status")!="KNOWN"
        or reconstructed.get("invalidated")):
        raise ValueError("POSITION_EVIDENCE_OUTDATED")
    if v31.get("version")!="v33-31" or v32.get("status")!="KNOWN":
        raise ValueError("CLOSE_EVIDENCE_NOT_AVAILABLE")
    matches=[r for r in positional["details"] if r["status"]=="MATCH"]
    excluded={r["close_index"] for r in reconstructed["records"]
              if r["status"]=="MATCH"}
    if len(matches)!=48 or len(excluded)!=10:
        raise ValueError("COHORT_CHANGED_RECHECK_REQUIRED")
    direct={r["row_index"]:r for r in v31["results"]
            if r.get("status")=="VERIFIED"}
    external={r["row_index"]:r for r in v32["results"]
              if r.get("status")=="RECONSTRUCTED_EXCHANGE_PROOF"}
    for r in matches:
        idx=r["close_index"]
        if idx in excluded or (idx in direct)==(idx in external):
            raise ValueError("COHORT_IDENTITY_NOT_UNIQUE")
        refs=r.get("bound_rows") or []
        if not refs or any(positions.hash_row(rows[e["index"]])!=e["sha256"]
                           for e in refs):
            raise ValueError("JOURNAL_FINGERPRINT_CONFLICT")
    return matches,direct,external
def _side(row):
    return "sell" if row["side"]=="long" else "buy"

def owned_orders(item,rows,client,oco_history,direct,external):
    idx=item["close_index"]
    row=rows[idx]
    pid,created,side=row["execution_id"].split(":",1)[1].split(":")
    created=int(created)
    if side!=row["side"]:
        raise ValueError("POSITION_SIDE_DISAGREES")
    algos=[a for a in oco_history
           if abs(int(a.get("cTime") or 0)-created)<=1
           and a.get("state")=="effective" and a.get("side")==_side(row)]
    if len(algos)!=1:
        raise ValueError("OWNED_OCO_NOT_UNIQUE")
    oco=algos[0]
    algo_id=str(oco.get("algoId") or "")
    if not algo_id:raise ValueError("ALGO_ID_MISSING")
    if idx in direct:
        proof=row.get("close_execution_evidence") or {}
        if (proof.get("price_basis")!="verified_owned_protection_fills"
            or str(proof.get("algo_id"))!=algo_id):
            raise ValueError("STORED_OWNED_ALGO_MISMATCH")
        order_id=str(proof.get("order_id") or "")
        trade_ids=proof.get("trade_ids") or []
    else:
        proof=(external[idx].get("proof") or {})
        if (proof.get("chain")!="EXACT_POSITION_ENTRY_ATTACHED_OCO_CHILD_ORDER_AND_FILLS"
            or str(proof.get("algo_id"))!=algo_id):
            raise ValueError("RECONSTRUCTED_ALGO_MISMATCH")
        order_id=str(proof.get("order_id") or "")
        trade_ids=proof.get("trade_ids") or []
    actual_ids=set(map(str,oco.get("ordIdList") or []))
    if oco.get("ordId"):actual_ids.add(str(oco["ordId"]))
    if not order_id or actual_ids!={order_id} or not trade_ids:
        raise ValueError("CLOSE_CHILD_ORDER_NOT_UNIQUE")
    found,error=recovered_close.find_entry(
        client,row["symbol"],created,side,oco)
    if not found:raise ValueError(error or "ENTRY_NOT_OWNED")
    entry_id,entry_order=found
    if (not entry_id or str((entry_order.get("info") or {}).get("ordId"))!=str(entry_id)):
        raise ValueError("ENTRY_ORDER_INCONSISTENT")
    close_raw=client.exchange.fetch_order(order_id,row["symbol"])
    close_info=close_raw.get("info") or {}
    if (str(close_info.get("ordId"))!=order_id
        or str(close_info.get("algoId"))!=algo_id
        or close_raw.get("status")!="closed"
        or close_raw.get("side")!=_side(row)):
        raise ValueError("CLOSE_ORDER_NOT_OWNED")
    refs=item["bound_rows"]
    reduce_rows=refs[1:-1]
    stub=dict(close_index=idx,symbol=row["symbol"],
        created_ms=created,entry_order_id=str(entry_id),
        close_order_id=order_id,
        trade_rows=reduce_rows+[refs[-1]],
        exchange_money=item["okx_position_values"],
        journal_money=item["journal_values"])
    return stub,dict(entry_order_id=str(entry_id),
        algo_id=algo_id,close_order_id=order_id,
        verified_close_trade_ids=sorted(map(str,trade_ids)),
        partial_rows=[ref["index"] for ref in reduce_rows])
def bills_for_span(client,symbol,created,closed,cache):
    start=datetime.fromtimestamp((created-120000)/1000,KST)
    finish=datetime.fromtimestamp((closed+120000)/1000,KST)
    today=datetime(start.year,start.month,start.day,tzinfo=KST)
    final=datetime(finish.year,finish.month,finish.day,tzinfo=KST)
    combined={}
    while today<=final:
        day_ms=int(today.timestamp()*1000)
        key=(symbol,day_ms)
        if key not in cache:
            values,query_count=bills.fetch_bills(client.exchange,
                symbol,day_ms,day_ms+86400000-1,kind="2")
            cache[key]=values
            time.sleep(.06)
        for bill_id,raw in cache[key].items():
            if bill_id in combined and combined[bill_id]!=raw:
                raise ValueError("DAY_CROSSOVER_BILL_COLLISION")
            combined[bill_id]=raw
        today+=timedelta(days=1)
    return combined

def known_ten_ids(user_dir):
    state=bills.read_status(user_dir)
    if (state.get("status")!="KNOWN" or not state.get("source_valid")
        or state.get("bound_bill_matches")!=10):
        raise ValueError("OLDER_FEE_BILL_AUDIT_NOT_CURRENT")
    result=set()
    for r in state["records"]:
        if r.get("status")!="MATCH":continue
        for bill_id in r.get("matched_bill_ids",[]):
            if bill_id in result:
                raise ValueError("PREEXISTING_BILL_DUPLICATE")
            result.add(bill_id)
    if len(result)!=71:
        raise ValueError("PREEXISTING_FEE_BILL_COUNT_CHANGED")
    return result
def collect(user_dir,client_factory):
    rows,journal_sha=read_rows(user_dir)
    cohort,direct,external=scope(user_dir,rows)
    previous_bill_ids=known_ten_ids(user_dir)
    clients={};ocos={};bill_cache={}
    output=[]
    for item in cohort:
        idx=item["close_index"];symbol=item["symbol"]
        row=rows[idx]
        result=dict(close_index=idx,symbol=symbol,
            journal_time=row.get("time"),
            close_sha256=positions.hash_row(row),
            bound_rows=item["bound_rows"])
        try:
            if symbol not in clients:
                clients[symbol]=client_factory(symbol)
                r=clients[symbol].exchange.private_get_trade_orders_algo_history(
                    {"instId":symbol.split("/")[0]+"-USDT-SWAP",
                     "ordType":"oco","state":"effective","limit":"100"})
                ocos[symbol]=r.get("data") or []
            client=clients[symbol]
            evidence,chain=owned_orders(item,rows,client,
                ocos[symbol],direct,external)
            bills.precise_trades(client,chain["close_order_id"],
                evidence["created_ms"],chain["verified_close_trade_ids"])
            close=int(ledger.dt_kst(row["time"]).timestamp()*1000)
            retrieved=bills_for_span(client,symbol,
                evidence["created_ms"],close,bill_cache)
            actual=bills.verify_bill_fees(evidence,rows,client,retrieved)
            result.update(actual,ownership=chain,
                          bill_fetch_days=len([
                              k for k in bill_cache if k[0]==symbol]),
                          actual_fee_delta=str(
                              __import__("decimal").Decimal(actual["fee_from_exact_bill_ids"])-
                              __import__("decimal").Decimal(actual["journal_fee"])))
        except Exception as exc:
            result.update(status="UNVERIFIED",reason=type(exc).__name__,
                          explanation=str(exc)[:140])
        output.append(result)
    verified=[r for r in output if r["status"]=="MATCH"]
    claimed=[bid for r in verified for bid in r["matched_bill_ids"]]
    if len(set(claimed))!=len(claimed):
        raise ValueError("SAME_FEE_BILL_USED_IN_MULTIPLE_OWNED_POSITIONS")
    if set(claimed)&previous_bill_ids:
        raise ValueError("V33_34_AND_V33_35_FEE_BILL_COHORT_OVERLAP")
    return dict(version="v33-35",mode="GET_ONLY_EXACT_TRADE_BILL",
        may_execute=False,observed_at=datetime.now(KST).isoformat(),
        source_journal_sha256=journal_sha,
        target_positions=len(cohort),
        status_counts=dict(Counter(r["status"] for r in output)),
        matched_position_count=len(verified),
        matched_fill_count=sum(r["matched_fill_count"] for r in verified),
        exact_fee_bill_count=len(set(claimed)),
        baseline_ten_fee_bill_count=len(previous_bill_ids),
        combined_exact_fee_bill_count=len(set(claimed))+len(previous_bill_ids),
        queried_symbol_days=len(bill_cache),
        results=output,
        limitations=[
            "Only 48 historically proven CORE OCO position closes in this cohort.",
            "GET-only ordId+tradeId exact fee bill joins; no trading execution changes.",
            "Full funding-to-position bill matching is not proven: funding bills lack posId.",
            "Not a validation of all CORE entries/exits or account profitability."])

def atomic_save(path,payload):
    path=Path(path)
    fd,tmp=tempfile.mkstemp(prefix=".v33_35_",dir=path.parent)
    try:
        os.fchmod(fd,0o600)
        with os.fdopen(fd,"w",encoding="utf-8") as f:
            json.dump(payload,f,ensure_ascii=False,allow_nan=False,indent=2)
            f.flush();os.fsync(f.fileno())
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)
def read_status(user_dir):
    path=Path(user_dir)/FILE
    try: state=json.loads(path.read_text())
    except (OSError,ValueError):
        return dict(status="NOT_COLLECTED",may_execute=False)
    if state.get("version")!="v33-35":
        return dict(status="INVALID_VERSION",may_execute=False)
    rows,_=ledger.read_lines(Path(user_dir)/"trades_log.jsonl")
    valid=[]
    for result in state.get("results",[]):
        idx=result.get("close_index")
        if type(idx)!=int or not 0<=idx<len(rows):continue
        if positions.hash_row(rows[idx])!=result.get("close_sha256"):continue
        refs=result.get("bound_rows") or []
        if not refs or any(type(ref.get("index"))!=int
            or ref["index"]<0 or ref["index"]>=len(rows)
            or positions.hash_row(rows[ref["index"]])!=ref.get("sha256")
            for ref in refs):continue
        valid.append(result)
    matched=[x for x in valid if x.get("status")=="MATCH"]
    ids=[bid for record in matched for bid in record.get("matched_bill_ids",[])]
    if len(ids)!=len(set(ids)):
        return dict(status="DUPLICATE_BILL",may_execute=False)
    return dict(state,status="KNOWN",may_execute=False,
        source_valid=len(valid)==len(state.get("results",[])),
        bound_rows=len(valid),bound_matches=len(matched),
        bound_fee_bill_ids=len(ids),
        invalidated=len(state.get("results",[]))-len(valid))

def main():
    import argparse,config,okx_client
    parser=argparse.ArgumentParser()
    parser.add_argument("--user-dir",required=True)
    opts=parser.parse_args()
    cfg=config.UserConfig(opts.user_dir)
    report=collect(opts.user_dir,
        lambda symbol:okx_client.OkxClient(symbol,cfg))
    atomic_save(Path(opts.user_dir)/FILE,report)
    print(json.dumps(dict(version=report["version"],
        scope=report["target_positions"],
        statuses=report["status_counts"],
        fills=report["matched_fill_count"],
        bills=report["exact_fee_bill_count"],
        combined_bill_count=report["combined_exact_fee_bill_count"]),
        ensure_ascii=False))
    return 0

if __name__=="__main__":
    raise SystemExit(main())
