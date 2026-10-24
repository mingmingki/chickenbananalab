"""v33-34 missing-entry reconstruction: read-only sidecar, no journal changes."""
import json,hashlib,os,tempfile
from decimal import Decimal
from pathlib import Path
from collections import Counter
from datetime import datetime,timezone,timedelta
import core_position_pnl_audit_v33_34 as pnl
import core_external_close_recovery_v33_32 as chain
import core_exit_outcome_join_v33_25 as journal

FILE="core_missing_entry_proof_v33_34.json"
KST=timezone(timedelta(hours=9))

def proof_one(rows,entry,client,position_history,oco_history):
    i=entry["close_index"];row=rows[i]
    pid,created_str,side=row["execution_id"].split(":",1)[1].split(":")
    created=int(created_str);symbol=row["symbol"]
    histories=[x.get("info") or {} for x in position_history
        if str((x.get("info") or {}).get("posId"))==pid
        and str((x.get("info") or {}).get("cTime"))==created_str]
    if len(histories)!=1:return dict(status="EXCLUDED",reason="POSITION_ID_NOT_UNIQUE")
    algos=[a for a in oco_history if abs(int(a.get("cTime") or 0)-created)<=1
           and a.get("side")==("sell" if side=="long" else "buy")
           and a.get("state")=="effective"]
    if len(algos)!=1:return dict(status="EXCLUDED",reason="OCO_NOT_UNIQUE")
    linked,reason=chain.find_entry(client,symbol,created,side,algos[0])
    if linked is None:return dict(status="EXCLUDED",reason=reason)
    entry_order_id,raw_entry=linked
    if not isinstance(raw_entry,dict):return dict(status="EXCLUDED",reason="ENTRY_ORDER_MISSING")
    start=datetime.fromtimestamp(created/1000,KST)
    end=journal.dt_kst(row["time"])
    if end is None:return dict(status="EXCLUDED",reason="CLOSE_TIME_MISSING")
    overlaps=[j for j,x in enumerate(rows[:i]) if
        x.get("symbol")==symbol and x.get("side")==side and
        x.get("type")=="open" and x.get("dry_run") is not True
        and journal.dt_kst(x.get("time")) is not None
        and start<=journal.dt_kst(x["time"])<=end]
    delayed_open=None
    if len(overlaps)>1:
        return dict(status="EXCLUDED",reason="MULTIPLE_DELAYED_OPEN_ROWS")
    if len(overlaps)==1:
        idx=overlaps[0];opened=rows[idx]
        if opened.get("strategy_group")!="core":
            return dict(status="EXCLUDED",reason="NON_CORE_DELAYED_OPEN")
        key=str(opened.get("execution_id") or "")
        if not key.startswith("core-entry:"+symbol+"-"):
            return dict(status="EXCLUDED",reason="DELAYED_OPEN_CLIENT_ID_MISSING")
        encoded=key.rsplit("-",1)[-1][:14]
        try:
            started=datetime.strptime(encoded,"%Y%m%d%H%M%S").replace(tzinfo=KST)
            client_created=int(started.timestamp()*1000)
            from decimal import Decimal as D
            market=client.exchange.market(symbol)
            contracted=D(str(raw_entry.get("filled")))
            contract_size=D(str(market.get("contractSize")))
            ledger_qty=D(str(opened["amount"]))
            ledger_px=D(str(opened["price"]))
            exchange_px=D(str(raw_entry["average"]))
            if (abs(client_created-created)>120000
                or not 0<(journal.dt_kst(opened["time"])-start).total_seconds()<=420
                or abs(contracted*contract_size-ledger_qty)>max(D(".00000001"),ledger_qty*D(".0000001"))
                or abs(ledger_px-exchange_px)>max(D(".00000001"),exchange_px*D(".000001"))):
                return dict(status="EXCLUDED",reason="DELAYED_OPEN_NOT_EXACT_ORDER_MATCH")
        except (ValueError,TypeError,ArithmeticError):
            return dict(status="EXCLUDED",reason="DELAYED_OPEN_PROOF_MALFORMED")
        delayed_open=dict(index=idx,sha256=pnl.hash_row(opened),
            matching_entry_contract_size=str(contract_size),
            journal_entry_client_id=key)
    indices=[k for k,x in enumerate(rows[:i+1]) if x.get("symbol")==symbol
             and x.get("side")==side and x.get("type") in ("reduce","close")
             and x.get("strategy_group") in ("core",None)
             and x.get("dry_run") is not True
             and journal.dt_kst(x.get("time")) is not None
             and start<=journal.dt_kst(x["time"])<=end]
    if not indices or indices[-1]!=i:
        return dict(status="EXCLUDED",reason="REALIZATION_EVENTS_NOT_BOUNDED")
    if any(rows[k].get("type")=="close" for k in indices[:-1]):
        return dict(status="EXCLUDED",reason="EARLIER_FULL_CLOSE")
    duplicates=[rows[k].get("execution_id") for k in indices if rows[k].get("execution_id")]
    if len(duplicates)!=len(set(duplicates)):
        return dict(status="EXCLUDED",reason="DUPLICATE_REALIZATION_KEY")
    try:
        local=pnl.journal_money(rows,indices)
        exchange=pnl.position_money(histories[0])
        differences={k:local[k]-exchange[k] for k in local}
        journal_residual=local["net"]-(local["gross"]-local["fee"]+local["funding"])
        exchange_residual=exchange["net"]-(
            exchange["gross"]-exchange["fee"]+exchange["funding"])
        disputed=[k for k,v in differences.items() if abs(v)>pnl.EPS]
        if abs(journal_residual)>pnl.EPS:disputed.append("journal_net_invariant")
        if abs(exchange_residual)>pnl.EPS:disputed.append("okx_net_invariant")
    except (ValueError,ArithmeticError):
        return dict(status="EXCLUDED",reason="FINANCIAL_FIELD_INVALID")
    return dict(
        status="MATCH" if not disputed else "MISMATCH",
        reason="MISSING_OPEN_RECONSTRUCTED_FROM_EXACT_ATTACHED_ENTRY" if not disputed
               else "RECONSTRUCTED_ENTRY_ACCOUNTING_DIFFERENCE",
        position_id=pid,created_ms=created,
        entry_order_id=str(entry_order_id),algo_id=str(algos[0]["algoId"]),
        close_order_id=str(algos[0].get("ordId") or ""),
        entry_order_raw_id=str((raw_entry.get("info") or {}).get("ordId") or ""),
        trade_rows=[dict(index=k,sha256=pnl.hash_row(rows[k])) for k in indices],
        delayed_open=delayed_open,
        realized_count=len(indices),partial_count=len(indices)-1,
        journal_money={k:str(v) for k,v in local.items()},
        exchange_money={k:str(v) for k,v in exchange.items()},
        deviations={k:str(v) for k,v in differences.items()},
        disputed_fields=disputed)
def collect(user_dir,client_factory):
    directory=Path(user_dir)
    raw=(directory/"trades_log.jsonl").read_bytes()
    rows=[json.loads(x) for x in raw.decode().splitlines() if x.strip()]
    existing=pnl.read_report(directory)
    if existing.get("status")!="KNOWN" or existing.get("version")!="v33-34":
        raise ValueError("PREVIOUS_AUDIT_REQUIRED")
    targets=[item for item in existing.get("details",[])
             if item.get("status")=="EXCLUDED"
             and item.get("reason")=="OPEN_NOT_EXACTLY_UNIQUE"]
    clients={};history={};ocos={};records=[]
    for item in targets:
        i=item["close_index"];row=rows[i];sym=row["symbol"]
        try:
            if sym not in clients:
                clients[sym]=client_factory(sym)
                history[sym]=clients[sym].exchange.fetch_positions_history([sym],limit=100)
                rsp=clients[sym].exchange.private_get_trade_orders_algo_history(
                    {"instId":sym.split("/")[0]+"-USDT-SWAP",
                     "ordType":"oco","state":"effective","limit":"100"})
                ocos[sym]=rsp.get("data") or []
            result=proof_one(rows,item,clients[sym],history[sym],ocos[sym])
        except Exception as exc:
            result=dict(status="PENDING",reason="EXCHANGE_GET_UNAVAILABLE",
                        error_type=type(exc).__name__)
        records.append(dict(close_index=i,close_sha256=pnl.hash_row(row),
                            symbol=sym,time=row.get("time"),**result))
    return dict(version="v33-34",mode="MISSING_ENTRY_READ_ONLY_PROOF",
                may_execute=False,original_journal_sha256=hashlib.sha256(raw).hexdigest(),
                observed_at=datetime.now(KST).isoformat(),
                status_counts=dict(Counter(x["status"] for x in records)),
                records=records,
                limitation="This is a synthetic proof of a historical entry, not an inserted journal event.")

def save_atomic(path,record):
    p=Path(path)
    fd,temp=tempfile.mkstemp(dir=p.parent,prefix=".v33_34_")
    try:
        os.fchmod(fd,0o600)
        with os.fdopen(fd,"w",encoding="utf-8") as file:
            json.dump(record,file,ensure_ascii=False,indent=2,allow_nan=False)
            file.flush();os.fsync(file.fileno())
        os.replace(temp,p)
    finally:
        if os.path.exists(temp):os.unlink(temp)
def read_report(user_dir):
    directory=Path(user_dir)
    try:body=json.loads((directory/FILE).read_text())
    except (ValueError,OSError):
        return dict(status="NOT_COLLECTED",may_execute=False)
    if body.get("version")!="v33-34":
        return dict(status="INVALID_VERSION",may_execute=False)
    rows,_=journal.read_lines(directory/"trades_log.jsonl")
    bound=[]
    for rec in body.get("records",[]):
        idx=rec.get("close_index")
        if type(idx)!=int or idx<0 or idx>=len(rows):continue
        if pnl.hash_row(rows[idx])!=rec.get("close_sha256"):continue
        if rec.get("status") in ("MATCH","MISMATCH"):
            entries=rec.get("trade_rows") or []
            if not entries or any(
                type(t.get("index"))!=int or
                t["index"]<0 or t["index"]>=len(rows) or
                pnl.hash_row(rows[t["index"]])!=t.get("sha256")
                for t in entries):
                continue
            extra=rec.get("delayed_open")
            if extra:
                idx2=extra.get("index")
                if (type(idx2)!=int or idx2<0 or idx2>=len(rows)
                    or pnl.hash_row(rows[idx2])!=extra.get("sha256")):
                    continue
        bound.append(rec)
    return dict(body,status="KNOWN",may_execute=False,
                bound_count=len(bound),
                bound_matches=sum(x.get("status")=="MATCH" for x in bound),
                invalidated=len(body.get("records",[]))-len(bound))

def main():
    import argparse,config,okx_client
    ap=argparse.ArgumentParser()
    ap.add_argument("--user-dir",required=True)
    args=ap.parse_args()
    cfg=config.UserConfig(args.user_dir)
    result=collect(args.user_dir,lambda s:okx_client.OkxClient(s,cfg))
    save_atomic(Path(args.user_dir)/FILE,result)
    print(json.dumps(dict(version="v33-34",counts=result["status_counts"],
                          targets=len(result["records"])),ensure_ascii=False))
    return 0
if __name__=="__main__":raise SystemExit(main())
