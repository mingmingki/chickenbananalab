"""v33-47 GET-only Candidate C protection parity: exchange vs append-only ledger.

No order changes, no release, no core/candidate engine imports or state mutations.
Ledger rows are validated without IntentLedger.load (which can append recovery rows).
"""
import argparse, hashlib, json, os, tempfile
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

import candidate_c_intent_ledger as il
import candidate_c_exit_management as cem

SYMBOLS=("DOGE/USDT:USDT","SOL/USDT:USDT")
REPORT="candidate_c_protection_parity_v33_47.json"
TOL=Decimal("0.00000001")


def dec(value):
    if value is None or isinstance(value,bool):
        raise ValueError("MISSING_OR_INVALID_NUMERIC")
    result=Decimal(str(value))
    if not result.is_finite():
        raise ValueError("NONFINITE_NUMERIC")
    return result


def close(a,b,tol=TOL):
    return abs(dec(a)-dec(b)) <= tol


def replay(data):
    intents={}
    last_hash="GENESIS"
    expected_seq=0
    for line in data.splitlines():
        if not line.strip():
            continue
        rec=json.loads(line, parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x)))
        il._validate_record(rec)
        if rec.get("seq") != expected_seq or rec.get("prev_hash") != last_hash:
            raise ValueError("LEDGER_SEQUENCE_OR_PREV_HASH_MISMATCH")
        if il._checksum(rec) != rec.get("checksum"):
            raise ValueError("LEDGER_SHA_CHAIN_MISMATCH")
        last_hash=rec["checksum"]
        expected_seq+=1
        ident=rec["intent_id"]
        if rec["event"]=="created":
            if ident in intents:
                raise ValueError("DUPLICATE_ENTRY_INTENT_ID")
            intents[ident]=dict(rec)
        else:
            if ident not in intents:
                raise ValueError("TRANSITION_WITHOUT_INTENT")
            old=intents[ident]
            old.update({k:v for k,v in rec.items() if k not in
                ("event","intent_id","schema_version","seq","prev_hash","checksum")})
    return intents


def read_ledger(root,symbol):
    filename="candidate_c_intent_ledger_"+symbol.replace("/","_").replace(":","_")+".jsonl"
    raw=(Path(root)/filename).read_bytes()
    return replay(raw),hashlib.sha256(raw).hexdigest()


def _last_epoch(root, intent_id):
    # This existing loader is GET-only, unlike IntentLedger.load.
    store=cem.PositionEpochStore.load(str(Path(root)/"candidate_c_epoch_store.jsonl"))
    if store.integrity_error:
        raise ValueError("EPOCH_INTEGRITY_ERROR")
    epoch=store.get(intent_id)
    if not epoch.entry_intent_id or epoch.entry_intent_id != intent_id:
        raise ValueError("EPOCH_MISSING")
    return epoch


def evaluate(symbol,position,orders,algos,intents,epoch=None):
    """Evaluate independent snapshots. Only exact verified states get PASS."""
    out=dict(symbol=symbol,status="UNCONFIRMED",may_execute=False,may_release=False)
    if not isinstance(orders,list) or not isinstance(algos,list):
        out["status"]="EXCHANGE_UNKNOWN"
        return out
    if position is None:
        out["status"]=("FLAT_CLEAR" if not orders and not algos and not any(
            r.get("kind")=="EntryIntent" and r.get("state") not in il.TERMINAL_STATES
            for r in intents.values()) else "FLAT_WITH_UNRESOLVED_EVIDENCE")
        return out
    out["observed_contracts"]=position.get("contracts")
    out["side"]=position.get("side")
    entry=[r for r in intents.values() if r.get("kind")=="EntryIntent"
           and r.get("state")=="protected" and r.get("symbol")==symbol]
    out["protected_entry_count"]=len(entry)
    out["unresolved_management"]=[
        dict(intent_id=r["intent_id"],kind=r.get("kind"),state=r.get("state"))
        for r in intents.values() if r.get("kind") in
        ("ReduceIntent","StopUpdateIntent","ExitIntent") and
        r.get("state") in ("submit_attempted","submission_unknown","reconcile_required",
                          "protection_pending","exchange_ack","filled")]
    if out["unresolved_management"]:
        out["status"]="MANAGEMENT_ACTION_UNRESOLVED"
        return out
    if len(entry)!=1:
        out["status"]="ENTRY_OWNER_UNCONFIRMED"
        return out
    record=entry[0]
    out["entry_intent_id"]=record["intent_id"]
    if record.get("protection_phase")=="reduce_resize_pending":
        out["status"]="REDUCE_RESIZE_PENDING"
        return out
    if record.get("protection_phase") not in (None,"complete"):
        out["status"]="PARENT_PROTECTION_PHASE_UNKNOWN"
        return out
    if epoch is None:
        out["status"]="EPOCH_MISSING"
        return out
    try:
        if orders:
            raise ValueError("OPEN_ORDERS_PRESENT")
        if not record.get("remote_submission_finalized"):
            raise ValueError("ENTRY_SUBMISSION_UNCONFIRMED")
        if len(algos)!=1 or len(record.get("protective_algo_ids") or [])!=1:
            raise ValueError("PROTECTIVE_ALGO_COUNT_MISMATCH")
        algo=algos[0]
        actual_id=str(algo.get("algoId") or "")
        owned_id=str(record["protective_algo_ids"][0])
        if actual_id!=owned_id or str(algo.get("algoClOrdId") or "") != str(record.get("attach_algo_cl_ord_id") or ""):
            raise ValueError("OCO_OWNERSHIP_ID_MISMATCH")
        if algo.get("ordType")!="oco" or algo.get("state")!="live" or algo.get("instId")!=symbol.split(":")[0].replace("/","-")+"-SWAP":
            raise ValueError("OCO_NOT_LIVE_OR_WRONG_INSTRUMENT")
        if algo.get("side") != ("sell" if position.get("side")=="long" else "buy"):
            raise ValueError("OCO_CLOSE_SIDE_MISMATCH")
        if str(algo.get("reduceOnly")).lower()!="true":
            raise ValueError("OCO_NOT_REDUCE_ONLY")
        if epoch.side!=position.get("side") or record.get("requested_side")!=epoch.side:
            raise ValueError("POSITION_SIDE_MISMATCH")
        if (str(epoch.exchange_position_id)!=str(position.get("position_id"))
                or str(epoch.exchange_entry_timestamp_ms)!=str(position.get("entry_timestamp_ms"))):
            raise ValueError("POSITION_LIFECYCLE_ID_MISMATCH")
        if epoch.entry_intent_id!=record["intent_id"]:
            raise ValueError("EPOCH_OWNER_MISMATCH")
        if (epoch.config_version_id!=record.get("config_version_id")
                or epoch.config_hash!=record.get("config_hash")
                or epoch.reservation_id!=record.get("reservation_id")):
            raise ValueError("CONFIG_OR_RISK_ID_MISMATCH")
        if dec(record.get("reserved_risk_usdt"))<=0 or dec(epoch.remaining_reserved_risk_usdt)<=0:
            raise ValueError("RISK_RESERVATION_MISSING")
        qty=dec(position["contracts"])
        if qty<=0 or not (close(qty,epoch.remaining_contracts) and
                close(qty,record.get("remaining_quantity")) and close(qty,algo.get("sz"))):
            raise ValueError("POST_REDUCE_QUANTITY_MISMATCH")
        if not close(position["entry_price"],epoch.raw_entry_price):
            raise ValueError("ENTRY_PRICE_MISMATCH")
        if not close(algo.get("slTriggerPx"),epoch.current_stop_price):
            raise ValueError("STOP_PRICE_MISMATCH")
        if epoch.target_price is not None and not close(algo.get("tpTriggerPx"),epoch.target_price):
            raise ValueError("TAKE_PROFIT_PRICE_MISMATCH")
        out.update(status="PROTECTED_PARITY_VERIFIED",oco_id=actual_id,
                   sl=str(algo["slTriggerPx"]),tp=str(algo.get("tpTriggerPx")),
                   verified_contracts=str(qty))
    except (ValueError,KeyError,TypeError,InvalidOperation) as exc:
        out["status"]="EVIDENCE_MISMATCH"
        out["reason"]=str(exc)[:140] if isinstance(exc,ValueError) else type(exc).__name__
    return out


def audit(user_dir,client_factory):
    root=Path(user_dir)
    records=[]
    for sym in SYMBOLS:
        row=dict(symbol=sym,status="NOT_CHECKED",may_execute=False,may_release=False)
        try:
            intents,sha=read_ledger(root,sym)
            row["ledger_sha256"]=sha
        except Exception as exc:
            row.update(status="LEDGER_INTEGRITY_UNCONFIRMED",reason=type(exc).__name__)
            records.append(row)
            continue
        try:
            client=client_factory(sym)
            pos=client.fetch_position()
            orders=client.exchange.fetch_open_orders(sym)
            algos=client.fetch_pending_protection_orders()
            active=[r for r in intents.values() if r.get("kind")=="EntryIntent"
                    and r.get("state")=="protected" and r.get("symbol")==sym]
            epoch=_last_epoch(root,active[0]["intent_id"]) if len(active)==1 and pos else None
            result=evaluate(sym,pos,orders,algos,intents,epoch)
            # A position that changes between independent GETs is not an
            # atomic snapshot. Never promote an inconsistent window to PASS.
            end_position=client.fetch_position()
            keys=("position_id","entry_timestamp_ms","side","entry_price","contracts")
            if ((pos is None)!=(end_position is None)
                or (pos is not None and end_position is not None and
                    any(pos.get(k)!=end_position.get(k) for k in keys))):
                result["status"]="EXCHANGE_SNAPSHOT_CHANGED"
                result.pop("verified_contracts",None)
                result.pop("oco_id",None)
            row.update(result)
        except Exception as exc:
            row.update(status="READ_ONLY_QUERY_UNCONFIRMED",reason=type(exc).__name__)
        records.append(row)
    return dict(version="v33-47",mode="GET_ONLY_CANDIDATE_C_PROTECTION_PARITY",
        observed_at=datetime.now(timezone.utc).isoformat(),
        may_execute=False,may_modify_orders=False,may_release=False,
        count_by_status=dict(Counter(r["status"] for r in records)),
        results=records,limitations=[
          "Protective-order parity is not permission to place additional entry orders.",
          "An unresolved reduce/stop intent or failed GET remains unconfirmed.",
          "This report cannot recover ownership or amend protection by itself."])


def save_atomic(destination,report):
    destination=Path(destination)
    data=json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False).encode()
    with tempfile.NamedTemporaryFile(dir=str(destination.parent),prefix=".parity_v33_47_",
        suffix=".tmp",delete=False) as handle:
        tmp=Path(handle.name)
        try:
            handle.write(data);handle.flush();os.fsync(handle.fileno())
            os.chmod(tmp,0o600)
            os.replace(tmp,destination)
        finally:
            if tmp.exists(): tmp.unlink()


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--user-dir",required=True)
    args=p.parse_args()
    import config,okx_client
    cfg=config.UserConfig(args.user_dir)
    report=audit(args.user_dir,lambda symbol:okx_client.OkxClient(symbol,cfg))
    save_atomic(Path(args.user_dir)/REPORT,report)
    print(json.dumps({"observed_at":report["observed_at"],"count_by_status":report["count_by_status"],
          "results":[{"symbol":r["symbol"],"status":r["status"],"reason":r.get("reason")}
                     for r in report["results"]]},ensure_ascii=False))
    # Report unconfirmed states without failing the timer: this is diagnostic, not a gate.
if __name__=="__main__":main()
