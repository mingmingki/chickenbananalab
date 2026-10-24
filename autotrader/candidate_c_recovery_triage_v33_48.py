"""v33-48: conservative GET-only candidate C reconciliation triage.

Only reports proof-backed *shadow* recovery candidates. Never appends to
intents, changes orders, releases a gate, or activates entry. This is a safety
qualification phase for possible future automated repair.
"""
import argparse
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import candidate_c_protection_parity_v33_47 as baseline

FILE="candidate_c_recovery_triage_v33_48.json"
FRESH_MAX_SECONDS=1200


def _same_position(a,b):
    keys=("position_id","entry_timestamp_ms","side","entry_price","contracts")
    return isinstance(a,dict) and isinstance(b,dict) and all(a.get(k)==b.get(k) for k in keys)


def _pending_entry_candidates(intents,symbol):
    return [r for r in intents.values() if r.get("kind")=="EntryIntent"
            and r.get("symbol")==symbol and r.get("state")=="protection_pending"]


def _prior_owned_protection(root,symbol,entry_id,algo_id):
    # The chain has been authenticated by baseline.read_ledger; *never*
    # manufacture ownership from a current OCO alone.
    filename="candidate_c_intent_ledger_"+symbol.replace("/","_").replace(":","_")+".jsonl"
    raw=(Path(root)/filename).read_bytes()
    _,sha=baseline.read_ledger(root,symbol)
    if hashlib.sha256(raw).hexdigest()!=sha:
        raise ValueError("LEDGER_CHANGED_DURING_EVENT_REPLAY")
    previous=[]
    for line in raw.splitlines():
        event=json.loads(line)
        if (event.get("intent_id")==entry_id and event.get("event")=="transition"
            and event.get("state")=="protected" and algo_id in
              [str(x) for x in (event.get("protective_algo_ids") or [])]):
            previous.append(event)
    return bool(previous)


def strict_parent_proof(root,symbol,intents,client):
    """May only nominate historical protected->pending parents, not grant release."""
    pending=_pending_entry_candidates(intents,symbol)
    if len(pending)!=1:
        raise ValueError("PENDING_PARENT_NOT_UNIQUE")
    parent=pending[0]
    live_entries=[r for r in intents.values()
                  if r.get("kind")=="EntryIntent" and r.get("state")=="protected"]
    if live_entries:
        raise ValueError("OTHER_PROTECTED_PARENT_PRESENT")
    if any(r.get("kind") in ("ReduceIntent","StopUpdateIntent","ExitIntent","ReversalIntent")
           and r.get("state")!="terminal" for r in intents.values()):
        raise ValueError("MANAGEMENT_OR_ORDER_PENDING")
    if not parent.get("remote_submission_finalized"):
        raise ValueError("ENTRY_SUBMISSION_NOT_FINALIZED")
    if len(parent.get("protective_algo_ids") or [])!=1:
        raise ValueError("PARENT_OCO_ID_NOT_UNIQUE")
    algo_id=str(parent["protective_algo_ids"][0])
    if not _prior_owned_protection(root,symbol,parent["intent_id"],algo_id):
        raise ValueError("NO_PRIOR_VERIFIED_PROTECTION_EVENT")
    epoch=baseline._last_epoch(root,parent["intent_id"])
    pos=client.fetch_position()
    orders=client.exchange.fetch_open_orders(symbol)
    algos=client.fetch_pending_protection_orders()
    if pos is None or orders != [] or not isinstance(algos,list) or len(algos)!=1:
        raise ValueError("POSITION_ORDER_OCO_SCOPE_NOT_EXACT")
    algo=algos[0]
    if (str(algo.get("algoId"))!=algo_id or
        str(algo.get("algoClOrdId"))!=str(parent.get("attach_algo_cl_ord_id"))):
        raise ValueError("OCO_OWNERSHIP_UNVERIFIED")
    if (algo.get("ordType")!="oco" or algo.get("state")!="live" or
        algo.get("instId")!=symbol.split(":")[0].replace("/","-")+"-SWAP" or
        algo.get("side")!=("buy" if pos.get("side")=="short" else "sell") or
        str(algo.get("reduceOnly")).lower()!="true"):
        raise ValueError("OCO_TERMINAL_OR_NOT_REDUCE_ONLY")
    if (epoch.side!=pos.get("side") or parent.get("requested_side")!=epoch.side or
        str(epoch.exchange_position_id)!=str(pos.get("position_id")) or
        str(epoch.exchange_entry_timestamp_ms)!=str(pos.get("entry_timestamp_ms")) or
        epoch.config_hash!=parent.get("config_hash") or
        epoch.config_version_id!=parent.get("config_version_id") or
        epoch.reservation_id!=parent.get("reservation_id")):
        raise ValueError("POSITION_CONFIG_IDENTITY_MISMATCH")
    if (not baseline.close(epoch.raw_entry_price,pos.get("entry_price")) or
        not baseline.close(epoch.remaining_contracts,pos.get("contracts")) or
        not baseline.close(parent.get("remaining_quantity"),pos.get("contracts")) or
        not baseline.close(algo.get("sz"),pos.get("contracts")) or
        not baseline.close(algo.get("slTriggerPx"),epoch.current_stop_price) or
        not baseline.close(algo.get("tpTriggerPx"),epoch.target_price)):
        raise ValueError("POST_REDUCE_PRICE_OR_QUANTITY_MISMATCH")
    if baseline.dec(parent.get("reserved_risk_usdt"))<=0 or baseline.dec(epoch.remaining_reserved_risk_usdt)<=0:
        raise ValueError("RISK_RESERVATION_MISSING")
    # Original entry is proved by exact clOrdId, its terminal status and all
    # fills, not by time- or price-proximity alone.
    order=client.fetch_order_status_by_client_id(parent["intent_id"])
    if not order or str(order.get("clientOrderId"))!=parent["intent_id"]:
        raise ValueError("ENTRY_ORDER_NOT_OWNED")
    if (order.get("status")!="closed" or
        order.get("side")!=("sell" if epoch.side=="short" else "buy") or
        not order.get("id")):
        raise ValueError("ENTRY_ORDER_NOT_TERMINAL_FILLED")
    trades=client.fetch_entry_order_fills(order["id"])
    if not trades or len(trades)>=100:
        raise ValueError("ENTRY_FILLS_INCOMPLETE")
    ids=[str(t.get("id")) for t in trades]
    if any(i in ("", "None") for i in ids) or len(ids)!=len(set(ids)):
        raise ValueError("DUPLICATE_OR_INVALID_FILL_ID")
    if any(str(t.get("order"))!=str(order["id"]) for t in trades):
        raise ValueError("FOREIGN_FILL_INCLUDED")
    qty=sum((baseline.dec(t.get("amount")) for t in trades),Decimal(0))
    if qty<=0 or not baseline.close(qty,parent.get("requested_quantity")) or not baseline.close(qty,order.get("filled")):
        raise ValueError("ENTRY_FILL_QUANTITY_INCOMPLETE")
    weighted=sum((baseline.dec(t.get("amount"))*baseline.dec(t.get("price")) for t in trades),Decimal(0))/qty
    if not baseline.close(weighted,epoch.raw_entry_price):
        raise ValueError("ENTRY_FILL_VWAP_MISMATCH")
    pos2=client.fetch_position()
    if not _same_position(pos,pos2):
        raise ValueError("LIVE_POSITION_CHANGED_DURING_READS")
    # A second raw ledger hash closes the local read window; still no atomic
    # cross-system transaction, so the verdict is explicitly SHADOW_ONLY.
    return dict(recovery_type="PARENT_PROTECTED_STATE",entry_intent_id=parent["intent_id"],
                owned_oco_id=algo_id,entry_order_id=str(order["id"]),
                entry_fill_ids=sorted(ids),
                entry_filled_contracts=str(qty),remaining_contracts=str(pos["contracts"]),
                sl=str(algo.get("slTriggerPx")),tp=str(algo.get("tpTriggerPx")),
                evidence="EXACT_ORDER_FILL_EPOCH_OCO_POSITION_AND_PRIOR_PROTECTED_EVENT")


def classify(root,previous_row,client_factory=None):
    symbol=previous_row["symbol"]
    outcome=dict(symbol=symbol,original_status=previous_row.get("status"),
        status="MANUAL_REVIEW_REQUIRED",shadow_only=True,may_mutate_ledger=False,
        may_release_entry_gate=False,may_send_orders=False)
    old=previous_row.get("status")
    if old in ("FLAT_CLEAR","PROTECTED_PARITY_VERIFIED"):
        outcome["status"]="NO_REPAIR_REQUIRED"
        return outcome
    if old in ("EXCHANGE_UNKNOWN","READ_ONLY_QUERY_UNCONFIRMED","LEDGER_INTEGRITY_UNCONFIRMED",
               "EXCHANGE_SNAPSHOT_CHANGED","FLAT_WITH_UNRESOLVED_EVIDENCE"):
        outcome["reason"]="EXTERNAL_OR_LEDGER_STATE_UNKNOWN"
        return outcome
    if old in ("MANAGEMENT_ACTION_UNRESOLVED","REDUCE_RESIZE_PENDING"):
        outcome["reason"]="UNRESOLVED_ORDER_OR_PROTECTION_RESIZE_NEEDS_RECONCILIATION"
        return outcome
    if old=="EVIDENCE_MISMATCH":
        outcome["reason"]="PRICE_QUANTITY_OR_OWNERSHIP_MISMATCH"
        return outcome
    if old!="ENTRY_OWNER_UNCONFIRMED":
        outcome["reason"]="NOT_AN_EXPLICIT_RECOVERY_CASE"
        return outcome
    if client_factory is None:
        outcome["reason"]="CLIENT_REQUIRED_FOR_INDEPENDENT_PROOF"
        return outcome
    try:
        intents,sha=baseline.read_ledger(root,symbol)
        if sha!=previous_row.get("ledger_sha256"):
            raise ValueError("LEDGER_CHANGED_SINCE_BASELINE")
        client=client_factory(symbol)
        proof=strict_parent_proof(root,symbol,intents,client)
        _,again=baseline.read_ledger(root,symbol)
        if sha!=again:
            raise ValueError("LEDGER_CHANGED_AFTER_PROOF")
        outcome.update(status="SHADOW_RECOVERY_CANDIDATE",ledger_sha256=sha,**proof)
    except Exception as exc:
        outcome["reason"]=(str(exc)[:140] if isinstance(exc,(ValueError,KeyError,TypeError))
                           else type(exc).__name__)
    return outcome


def analyze(root,report,client_factory=None,now=None):
    current=now or datetime.now(timezone.utc)
    try:
        checked=datetime.fromisoformat(report["observed_at"])
        age=(current-checked).total_seconds()
    except (KeyError,ValueError,TypeError):
        age=float("inf")
    statuses=[]
    for row in report.get("results",[]):
        if age < 0 or age>FRESH_MAX_SECONDS:
            statuses.append(dict(symbol=row.get("symbol","UNKNOWN"),
                status="STALE_BASELINE",reason="V33_47_BASELINE_NOT_FRESH",
                shadow_only=True,may_mutate_ledger=False,
                may_release_entry_gate=False,may_send_orders=False))
        else:
            statuses.append(classify(root,row,client_factory))
    return dict(version="v33-48",mode="SHADOW_ONLY_EVIDENCE_BASED_RECOVERY_TRIAGE",
        observed_at=current.isoformat(),v33_47_observed_at=report.get("observed_at"),
        baseline_age_seconds=None if age==float("inf") else round(age),
        can_apply_recovery=False,may_mutate_ledger=False,may_release_entry_gate=False,
        may_send_orders=False,
        count_by_status=dict(Counter(x["status"] for x in statuses)),
        results=statuses,limitations=[
          "A SHADOW_RECOVERY_CANDIDATE is NOT an authorization to auto-append a ledger row.",
          "Risk configuration, exchange orders, SL/TP and entry permissions are unchanged.",
          "Definitive recovery requires an atomic production lock and independent post-write verification."])


def main():
    import config,okx_client
    parser=argparse.ArgumentParser()
    parser.add_argument("--user-dir",required=True)
    args=parser.parse_args()
    root=Path(args.user_dir)
    raw=(root/baseline.REPORT).read_bytes()
    report=json.loads(raw)
    if report.get("version")!="v33-47":
        raise ValueError("INVALID_BASELINE_VERSION")
    cfg=config.UserConfig(str(root))
    result=analyze(root,report,lambda symbol:okx_client.OkxClient(symbol,cfg))
    result["baseline_report_sha256"]=hashlib.sha256(raw).hexdigest()
    baseline.save_atomic(root/FILE,result)
    print(json.dumps({"version":result["version"],"count_by_status":result["count_by_status"],
        "results":[{"symbol":r["symbol"],"status":r["status"],"reason":r.get("reason")}
                   for r in result["results"]]},ensure_ascii=False))
if __name__=="__main__":main()
