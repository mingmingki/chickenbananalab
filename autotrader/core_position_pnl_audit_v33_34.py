"""v33-33 read-only OKX position-history monetary reconciliation.

For a proven external close, resolve exactly one journal CORE entry matching
the exchange posId creation timestamp and all reductions up to the final close.
Only then compare gross / fees / funding / net / contract quantity against the
independent OKX closed-position history. Never modify trading state.
"""
import hashlib,json,os,tempfile
from collections import Counter
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

import core_exit_outcome_join_v33_25 as base
import core_external_close_audit_v33_31 as a31
import core_external_close_recovery_v33_32 as a32

FILE="core_position_pnl_audit_v33_34.json"
ZERO=Decimal(0)
EPS=Decimal("0.0001")
CORE={"BTC/USDT:USDT","ETH/USDT:USDT","XRP/USDT:USDT","ADA/USDT:USDT"}

def decimal(value):
    if value is None or isinstance(value,bool):raise ValueError("MISSING_NUMERIC")
    result=Decimal(str(value))
    if not result.is_finite():raise ValueError("INVALID_NUMERIC")
    return result

def hash_row(row):
    return hashlib.sha256(json.dumps(row,sort_keys=True,
        separators=(",",":"),ensure_ascii=False,allow_nan=False).encode()).hexdigest()

def supported_close_indices(rows, user_dir):
    prior=a31.read_status(user_dir)
    latest=a32.read_report(user_dir)
    if prior.get("version")!="v33-31" or latest.get("status")!="KNOWN":
        raise ValueError("PREREQUISITE_CLOSE_PROOF_UNAVAILABLE")
    direct={r["row_index"] for r in prior["results"]
        if r.get("status")=="VERIFIED"
        and type(r.get("row_index"))==int
        and 0<=r["row_index"]<len(rows)
        and hash_row(rows[r["row_index"]])==r.get("sha256")}
    restored={r["row_index"] for r in latest["results"]
        if r.get("status")=="RECONSTRUCTED_EXCHANGE_PROOF"
        and type(r.get("row_index"))==int
        and 0<=r["row_index"]<len(rows)
        and hash_row(rows[r["row_index"]])==r.get("sha256")
        and (r.get("proof") or {}).get("chain")==
            "EXACT_POSITION_ENTRY_ATTACHED_OCO_CHILD_ORDER_AND_FILLS"}
    if direct.intersection(restored):raise ValueError("DUPLICATE_EVIDENCE_ROW")
    return sorted(direct|restored)
def journal_lifecycle(rows,close_idx):
    close=rows[close_idx]
    if close.get("type")!="close" or close.get("symbol") not in CORE:
        return None,"OUT_OF_SCOPE"
    try:
        pid,created,side=close["execution_id"].split(":",1)[1].split(":")
        opened=datetime.fromtimestamp(int(created)/1000,base.KST)
    except (ValueError,TypeError,KeyError,IndexError):
        return None,"INVALID_EXTERNAL_LIFECYCLE_KEY"
    sym=close["symbol"]
    matching=[i for i,row in enumerate(rows[:close_idx])
        if row.get("type")=="open" and row.get("strategy_group") in (None,"core")
        and row.get("dry_run") is not True and row.get("symbol")==sym
        and row.get("side")==side and base.dt_kst(row.get("time")) is not None
        and 0<=(base.dt_kst(row["time"])-opened).total_seconds()<=120]
    if len(matching)!=1:return None,"OPEN_NOT_EXACTLY_UNIQUE"
    start=matching[0]
    between=rows[start+1:close_idx+1]
    if any(r.get("type")=="open" and r.get("strategy_group") in (None,"core")
           and r.get("symbol")==sym for r in between):
        return None,"SUPERSEDED_BY_ANOTHER_OPEN"
    indices=[i for i in range(start+1,close_idx+1)
        if rows[i].get("strategy_group") in (None,"core")
        and rows[i].get("dry_run") is not True
        and rows[i].get("symbol")==sym
        and rows[i].get("type") in ("reduce","close")]
    if not indices or indices[-1]!=close_idx:
        return None,"FINAL_CLOSE_NOT_LAST_REALIZATION"
    if any(rows[i].get("side")!=side for i in indices):
        return None,"SIDE_CONFLICT"
    if any(rows[i].get("type")=="close" for i in indices[:-1]):
        return None,"EARLIER_CLOSE_IN_POSITION"
    keys=[rows[i].get("execution_id") for i in indices]
    ids=[x for x in keys if x]
    if len(ids)!=len(set(ids)):return None,"DUPLICATE_EVENT_EXECUTION_ID"
    return dict(position_id=pid,created=str(created),side=side,
        open_row=start,realized_rows=indices,
        bound_rows=[start]+indices),None

def journal_money(rows,indices):
    totals={key:ZERO for key in ("gross","fee","funding","net","quantity")}
    for idx in indices:
        r=rows[idx]
        for key,col in (("gross","pnl"),("fee","fee"),
                        ("net","okx_net_pnl"),("quantity","amount")):
            totals[key]+=decimal(r.get(col))
        totals["funding"]+=decimal(r.get("funding_fee") or 0)
    return totals

def position_money(info):
    return dict(gross=decimal(info.get("pnl")),
        fee=-decimal(info.get("fee")),
        funding=decimal(info.get("fundingFee")),
        net=decimal(info.get("realizedPnl")),
        quantity=decimal(info.get("closeTotalPos")))
def audited_position(rows,close_idx,history):
    life,reason=journal_lifecycle(rows,close_idx)
    if reason:return dict(status="EXCLUDED",reason=reason)
    matches=[r.get("info") or {} for r in history
        if str((r.get("info") or {}).get("posId"))==life["position_id"]
        and str((r.get("info") or {}).get("cTime"))==life["created"]]
    if len(matches)!=1:
        return dict(status="EXCLUDED",reason="OKX_POSITION_HISTORY_NOT_UNIQUE")
    try:
        local=journal_money(rows,life["realized_rows"])
        external=position_money(matches[0])
        differences={k:local[k]-external[k] for k in local}
        journal_residual=local["net"]-(local["gross"]-local["fee"]+local["funding"])
        okx_residual=external["net"]-(external["gross"]-external["fee"]+external["funding"])
        fields=[key for key in differences if abs(differences[key])>EPS]
        if abs(journal_residual)>EPS:fields.append("journal_net_invariant")
        if abs(okx_residual)>EPS:fields.append("okx_net_invariant")
        return dict(status="MATCH" if not fields else "MISMATCH",
            reason="ALL_FIVE_FIELDS_AGREE" if not fields else "POSITION_ACCOUNTING_DIFFERENCE",
            number_of_realizations=len(life["realized_rows"]),
            partial_reductions=len(life["realized_rows"])-1,
            bound_rows=[dict(index=i,sha256=hash_row(rows[i])) for i in life["bound_rows"]],
            journal_values={k:str(v) for k,v in local.items()},
            okx_position_values={k:str(v) for k,v in external.items()},
            differences={k:str(v) for k,v in differences.items()},
            journal_net_residual=str(journal_residual),
            okx_net_residual=str(okx_residual),disputed_fields=fields)
    except (ValueError,TypeError,InvalidOperation,ArithmeticError):
        return dict(status="EXCLUDED",reason="INVALID_FINANCIAL_NUMERIC")

def bill_state_metadata(user_dir):
    details={}
    for name in ("exchange_fee_state.json","exchange_funding_state.json"):
        try:
            state=json.loads((Path(user_dir)/name).read_text())
            events=state.get("events") or []
            ids=[str(x.get("bill_id") or "") for x in events]
            details[name]=dict(event_count=len(events),
                duplicate_bill_ids=len(ids)-len(set(ids)),
                complete_backfill=bool(state.get("backfill_complete")),
                last_error=state.get("last_error"),
                last_refresh_ms=state.get("last_refresh_ms"))
        except (OSError,ValueError,TypeError):
            details[name]=dict(status="MISSING_OR_UNREADABLE")
    return details
def collect(user_dir,client_factory):
    folder=Path(user_dir)
    source=(folder/"trades_log.jsonl").read_bytes()
    rows=[json.loads(line) for line in source.decode().splitlines() if line.strip()]
    eligible=supported_close_indices(rows,folder)
    histories={}
    outcomes=[]
    used_realizations=set()
    for index in eligible:
        row=rows[index]
        symbol=row["symbol"]
        try:
            if symbol not in histories:
                client=client_factory(symbol)
                histories[symbol]=client.exchange.fetch_positions_history(
                    [symbol],limit=100)
            result=audited_position(rows,index,histories[symbol])
            if result["status"] in ("MATCH","MISMATCH"):
                seen={x["index"] for x in result["bound_rows"][1:]}
                if seen.intersection(used_realizations):
                    result=dict(status="EXCLUDED",reason="EVENT_COUNTED_IN_MULTIPLE_POSITIONS")
                else:
                    used_realizations.update(seen)
        except Exception as exc:
            result=dict(status="UNAVAILABLE",reason="READ_ONLY_OKX_LOOKUP_ERROR",
                        error_type=type(exc).__name__)
        outcomes.append(dict(close_index=index,close_row_sha256=hash_row(row),
            symbol=symbol,journal_close_time=row["time"],**result))
    counts=dict(Counter(x["status"] for x in outcomes))
    matched=[x for x in outcomes if x["status"]=="MATCH"]
    totals={key:str(sum((Decimal(x["journal_values"][key]) for x in matched),ZERO))
            for key in ("gross","fee","funding","net","quantity")}
    return dict(version="v33-34",mode="OKX_POSITION_HISTORY_WITH_LEGACY_OPEN",
        may_execute=False,observed_at=datetime.now(base.KST).isoformat(),
        journal_sha256=hashlib.sha256(source).hexdigest(),
        checked_external_closes=len(eligible),status_counts=counts,
        exactly_matching_positions=len(matched),
        realized_event_count=sum(x["number_of_realizations"] for x in matched),
        partial_reduce_event_count=sum(x["partial_reductions"] for x in matched),
        matched_journal_totals_usdt=totals,
        fee_funding_bill_coverage=bill_state_metadata(folder),
        details=outcomes,
        limitations=[
            "Do not sum close-only PnL against OKX whole-position PnL; include reductions.",
            "OKX position history is independent of the local journal, but this is not a per-bill fee/funding reconciliation.",
            "Historical missing/ambiguous entry records remain excluded; zero mismatches does not imply complete coverage.",
            "The original journal and all live trade logic remain unchanged."])
def save_atomic(path,body):
    path=Path(path)
    fd,temp=tempfile.mkstemp(prefix=".v33_33_",dir=path.parent)
    try:
        os.fchmod(fd,0o600)
        with os.fdopen(fd,"w",encoding="utf-8") as handle:
            json.dump(body,handle,ensure_ascii=False,indent=2,allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp,path)
    finally:
        if os.path.exists(temp):os.unlink(temp)

def read_report(user_dir):
    directory=Path(user_dir)
    target=directory/FILE
    try:state=json.loads(target.read_text())
    except (OSError,ValueError):
        return dict(status="NOT_COLLECTED",may_execute=False)
    if state.get("version")!="v33-34":
        return dict(status="INVALID_VERSION",may_execute=False)
    rows,_=base.read_lines(directory/"trades_log.jsonl")
    valid=[]
    for report in state.get("details",[]):
        idx=report.get("close_index")
        if type(idx)!=int or idx<0 or idx>=len(rows):continue
        if hash_row(rows[idx])!=report.get("close_row_sha256"):continue
        if report.get("status") in ("MATCH","MISMATCH"):
            if any(type(item.get("index"))!=int
                   or item["index"]<0 or item["index"]>=len(rows)
                   or hash_row(rows[item["index"]])!=item.get("sha256")
                   for item in report.get("bound_rows",[])):
                continue
        valid.append(report)
    state=dict(state)
    state["status"]="KNOWN"
    state["currently_bound_positions"]=len(valid)
    state["currently_bound_matching_positions"]=sum(
        x.get("status")=="MATCH" for x in valid)
    state["invalidated_position_proofs"]=len(state["details"])-len(valid)
    state["may_execute"]=False
    return state

def main():
    import argparse,config,okx_client
    parser=argparse.ArgumentParser()
    parser.add_argument("--user-dir",required=True)
    options=parser.parse_args()
    cfg=config.UserConfig(options.user_dir)
    report=collect(options.user_dir,
        lambda symbol:okx_client.OkxClient(symbol,cfg))
    save_atomic(Path(options.user_dir)/FILE,report)
    print(json.dumps(dict(version="v33-34",positions=report["checked_external_closes"],
        counts=report["status_counts"],
        matched_net=report["matched_journal_totals_usdt"]["net"],
        partial_reductions=report["partial_reduce_event_count"]),
        ensure_ascii=False))
    return 0

if __name__=="__main__":raise SystemExit(main())
