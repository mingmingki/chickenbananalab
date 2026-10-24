"""v33-32 historical external close reconciliation (GET-only)."""
import json, hashlib, os, tempfile
from collections import Counter, defaultdict
from pathlib import Path
from datetime import datetime, timezone, timedelta
import core_exit_outcome_join_v33_25 as ledger
import core_external_close_audit_v33_31 as prior_audit
from okx_client import match_owned_protection_close

FILE="core_external_recovery_v33_32.json"
KST=timezone(timedelta(hours=9))

def row_hash(row):
    return hashlib.sha256(json.dumps(row, sort_keys=True,
        separators=(",",":"),ensure_ascii=False).encode()).hexdigest()
def unresolved(user_dir, rows):
    state=prior_audit.read_status(user_dir)
    if state.get("version")!="v33-31":
        raise ValueError("V33_31_PROVENANCE_MISSING")
    result=[]
    for item in state.get("results",[]):
        if item.get("status")!="UNVERIFIED":continue
        idx=item.get("row_index")
        if type(idx)!=int or not 0<=idx<len(rows):continue
        row=rows[idx]
        if not prior_audit.eligible(row):continue
        if row_hash(row)!=item.get("sha256"):continue
        result.append((idx,row))
    return result

def identity(row):
    try:
        position_id, created, side=row["execution_id"].split(":",1)[1].split(":")
        created=int(created)
        if not position_id or created<=0 or side!=row["side"]:
            raise ValueError("BAD_LIFECYCLE")
        return position_id, created, side
    except (KeyError,TypeError,ValueError):
        raise ValueError("BAD_LIFECYCLE")
def find_entry(client,symbol,created,side,algo):
    """Exact attached client algo ID links entry to the OCO."""
    start=created-90000
    finish=created+30000
    fills=client.exchange.fetch_my_trades(
        symbol,since=start,limit=100,params={"until":finish})
    if not isinstance(fills,list) or len(fills)>=100:
        return None,"ENTRY_FILL_WINDOW_INCOMPLETE"
    wanted_side="buy" if side=="long" else "sell"
    candidates={str(t.get("order")) for t in fills
                if t.get("side")==wanted_side and t.get("order")
                and t.get("timestamp") is not None
                and abs(int(t["timestamp"])-created)<=2000}
    matches=[]
    for oid in candidates:
        order=client.exchange.fetch_order(oid,symbol)
        raw=order.get("info") or {}
        attached=raw.get("attachAlgoOrds") or []
        ids={z.get("attachAlgoClOrdId") for z in attached
             if isinstance(z,dict)}
        if (raw.get("ordId")==oid and raw.get("state")=="filled"
            and str(raw.get("reduceOnly")).lower()=="false"
            and algo.get("algoClOrdId") in ids):
            matches.append((oid,order))
    if len(matches)!=1:return None,"ENTRY_ATTACHMENT_NOT_UNIQUE"
    return matches[0],None
def prove_one(row, client, position_history, oco_history):
    pid,created,side=identity(row)
    positions=[p for p in position_history
        if str((p.get("info") or {}).get("posId"))==pid
        and str((p.get("info") or {}).get("cTime"))==str(created)]
    if len(positions)!=1:return None,"POSITION_ID_AND_CTIME_NOT_UNIQUE"
    position=positions[0].get("info") or {}
    close_side="sell" if side=="long" else "buy"
    algos=[a for a in oco_history
        if abs(int(a.get("cTime") or 0)-created)<=1
        and a.get("side")==close_side and a.get("state")=="effective"
        and a.get("actualSide") in ("sl","tp")]
    if len(algos)!=1:return None,"OCO_NOT_UNIQUE"
    algo=algos[0]
    if abs(int(position.get("uTime") or 0)-int(algo.get("triggerTime") or 0))>5000:
        return None,"POSITION_END_AND_OCO_TRIGGER_DISAGREE"
    seen_at=ledger.dt_kst(row.get("time"))
    if seen_at is None:return None,"JOURNAL_TIME_MISSING"
    delay=seen_at.timestamp()*1000-int(algo["triggerTime"])
    if not 0<=delay<=90*60000:return None,"JOURNAL_CLOSE_OUTSIDE_TRIGGER_WINDOW"
    pair,error=find_entry(client,row["symbol"],created,side,algo)
    if pair is None:return None,error
    entry_order_id,entry_order=pair
    order_ids=set(algo.get("ordIdList") or [])
    if algo.get("ordId"):order_ids.add(algo["ordId"])
    if len(order_ids)!=1:return None,"TRIGGERED_ORDER_ID_NOT_UNIQUE"
    exit_order_id=next(iter(order_ids))
    exit_order=client.exchange.fetch_order(exit_order_id,row["symbol"])
    fills=client.fetch_trades_for_order(exit_order_id,created-30000,limit=100)
    if not fills or len(fills)>=100:return None,"EXIT_FILLS_NOT_COMPLETE"
    bound=f"{pid}:{created}:{side}"
    position_arg=dict(position_id=pid,entry_timestamp_ms=created,
        side=side,contracts=row["amount"],symbol=row["symbol"])
    verified=match_owned_protection_close(
        position_arg,entry_order,algo,exit_order,fills,
        bound_lifecycle=bound,entry_order_id=entry_order_id,
        entry_client_order_id=(entry_order.get("info") or {}).get("clOrdId"),
        owned_algo_ids=(algo["algoId"],),
        attach_algo_cl_ord_id=algo.get("algoClOrdId"))
    if verified is None:return None,"OWNED_FULL_CHAIN_VERIFICATION_FAILED"
    price=row.get("close_price")
    price_agrees=(price is None or abs(float(price)-verified["exit_price"])
        <=max(1e-9,abs(verified["exit_price"])*1e-8))
    return dict(entry_order_id=entry_order_id,
        entry_client_order_id=(entry_order.get("info") or {}).get("clOrdId"),
        algo_id=verified["algo_id"],order_id=verified["order_id"],
        trade_ids=verified["trade_ids"],exit_price=verified["exit_price"],
        recovered_exit_cause=verified["reason"],lifecycle_id=bound,
        journal_price_agrees=price_agrees,
        journal_reason_agrees=(row.get("reason")==verified["reason"]),
        trigger_to_journal_seconds=round(delay/1000,3),
        chain="EXACT_POSITION_ENTRY_ATTACHED_OCO_CHILD_ORDER_AND_FILLS"),None
def collect(user_dir,client_factory):
    folder=Path(user_dir)
    raw=(folder/"trades_log.jsonl").read_bytes()
    rows=[json.loads(s) for s in raw.decode().splitlines() if s.strip()]
    targets=unresolved(folder,rows)
    clients={};history={};algos={}
    results=[]
    for index,row in targets:
        symbol=row["symbol"]
        try:
            if symbol not in clients:
                clients[symbol]=client_factory(symbol)
                history[symbol]=clients[symbol].exchange.fetch_positions_history(
                    [symbol],limit=100)
                response=clients[symbol].exchange.private_get_trade_orders_algo_history({
                    "instId":symbol.split("/")[0]+"-USDT-SWAP",
                    "ordType":"oco","state":"effective","limit":"100"})
                algos[symbol]=response.get("data") or []
            proof,reason=prove_one(row,clients[symbol],
                history[symbol],algos[symbol])
            status="RECONSTRUCTED_EXCHANGE_PROOF" if proof else "NOT_PROVEN"
        except Exception as exc:
            status="GET_UNAVAILABLE";reason=type(exc).__name__;proof=None
        results.append(dict(row_index=index,sha256=row_hash(row),
            symbol=symbol,close_time=row["time"],
            original_reason=row.get("reason"),
            status=status,reason=reason,proof=proof))
    counts=Counter(x["status"] for x in results)
    causes=Counter(x["proof"]["recovered_exit_cause"] for x in results
                   if x["proof"])
    mismatched=[dict(index=x["row_index"],symbol=x["symbol"],
                     time=x["close_time"],recorded=x["original_reason"],
                     recovered=x["proof"]["recovered_exit_cause"])
                for x in results if x["proof"] and
                   not x["proof"]["journal_reason_agrees"]]
    return dict(version="v33-32",mode="EXCHANGE_GET_RECONSTRUCTION",
        may_execute=False,observed_at=datetime.now(KST).isoformat(),
        original_journal_sha256=hashlib.sha256(raw).hexdigest(),
        targets=len(targets),status_counts=dict(counts),
        recovered_exit_reasons=dict(causes),
        original_reason_disagreements=mismatched,
        results=results,
        notice="GET-only exact chain. No trade journal, trade PnL, or live policy changes.")

def save(path,body):
    p=Path(path)
    fd,tmp=tempfile.mkstemp(dir=p.parent,prefix=".v33_32_")
    try:
        os.fchmod(fd,0o600)
        with os.fdopen(fd,"w",encoding="utf-8") as out:
            json.dump(body,out,ensure_ascii=False,indent=2,allow_nan=False)
            out.flush();os.fsync(out.fileno())
        os.replace(tmp,p)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)
def read_report(user_dir):
    root=Path(user_dir);p=root/FILE
    try: report=json.loads(p.read_text())
    except (OSError,ValueError):
        return dict(status="NOT_COLLECTED",may_execute=False)
    if report.get("version")!="v33-32":
        return dict(status="INVALID_REPORT",may_execute=False)
    journal_bytes=(root/"trades_log.jsonl").read_bytes()
    rows=[json.loads(s) for s in journal_bytes.decode().splitlines() if s.strip()]
    current={}
    for result in report.get("results",[]):
        idx=result.get("row_index")
        if type(idx)!=int or idx<0 or idx>=len(rows):continue
        if row_hash(rows[idx])!=result.get("sha256"):continue
        current[idx]=result
    return dict(report,status="KNOWN",
        currently_bound_rows=len(current),
        bound_verified_rows=sum(
            x.get("status")=="RECONSTRUCTED_EXCHANGE_PROOF"
            and isinstance(x.get("proof"),dict)
            and x["proof"].get("chain")=="EXACT_POSITION_ENTRY_ATTACHED_OCO_CHILD_ORDER_AND_FILLS"
            for x in current.values()),
        missing_or_changed_rows=len(report.get("results",[]))-len(current),
        append_only_journal_changed=(
            hashlib.sha256(journal_bytes).hexdigest()!=report.get("original_journal_sha256")),
        may_execute=False)

def main():
    import argparse,config,okx_client
    parser=argparse.ArgumentParser()
    parser.add_argument("--user-dir",required=True)
    options=parser.parse_args()
    cfg=config.UserConfig(options.user_dir)
    result=collect(options.user_dir,lambda sym:okx_client.OkxClient(sym,cfg))
    save(Path(options.user_dir)/FILE,result)
    print(json.dumps(dict(version="v33-32",targets=result["targets"],
        counts=result["status_counts"],
        recovered_reasons=result["recovered_exit_reasons"],
        reason_disagreements=len(result["original_reason_disagreements"])),
        ensure_ascii=False))
    return 0
if __name__=="__main__":raise SystemExit(main())
