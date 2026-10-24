"""v33-41: forensic AI-action correlation and 26 unmatched size diagnostics.

READ-ONLY. No journal mutation, no trading orders, no PnL corrections.
"""
from collections import Counter, defaultdict
from datetime import datetime
from decimal import Decimal
from pathlib import Path
import hashlib
import json
import os
import tempfile

import core_legacy_provenance_v33_40 as prior
import core_legacy_gap_audit_v33_37 as legacy
import core_legacy_full_close_v33_39 as close39
import core_full_ledger_audit_v33_36 as ledger

FILE = "core_legacy_action_correlation_v33_41.json"
VERSION = "v33-41"
AI_SEC = 6
D = close39.decimal


def _read_events(root):
    raw=(root/"position_ai_log.jsonl").read_bytes()
    events=defaultdict(list)
    for line in raw.splitlines():
        if b'"position_ai_reduce_50"' not in line:
            continue
        try:
            e=json.loads(line)
            if e.get("event_type")!="position_ai_reduce_50" or not e.get("time") or not e.get("symbol"):
                continue
            ts=datetime.fromisoformat(e["time"])
            if not (datetime(2026,9,11)<=ts<datetime(2026,9,14)):
                continue
            events[e["symbol"]].append(e)
        except (ValueError,TypeError,KeyError):
            continue
    return events,hashlib.sha256(raw).hexdigest()


def _action_matches(row, events):
    at=datetime.fromisoformat(row["time"])
    results=[]
    qty=D(row["amount"])
    for ev in events:
        try:
            lag=abs((datetime.fromisoformat(ev["time"])-at).total_seconds())
            if lag>AI_SEC:continue
            v=D(ev["reduced_contracts"])
            if abs(v-qty)>max(D("0.0000001"),qty*D("0.0000001")):
                continue
            results.append(dict(review_id=ev.get("review_id"),
                                log_time=ev["time"],
                                reduced_contracts=str(v),
                                event_sha256=hashlib.sha256(
                                    json.dumps(ev,sort_keys=True,ensure_ascii=False).encode()).hexdigest()))
        except (ValueError,TypeError,KeyError):
            continue
    return results


def _fill_group_stats(row,trades):
    near=prior.near_side_fills(row,trades)
    orders=defaultdict(dict)
    for t in near:
        try:
            oid=str(t.get("order") or "")
            tid=str(t.get("id") or "")
            amount=D(t["amount"])
            price=D(t["price"])
            if not oid or not tid or amount<=0 or price<=0:
                continue
            if tid in orders[oid] and orders[oid][tid]!=(amount,price,int(t["timestamp"])):
                raise ValueError("DUPLICATED_TRADE_ID_CONFLICT")
            orders[oid][tid]=(amount,price,int(t["timestamp"]))
        except (ValueError,TypeError,KeyError):
            continue
    target=D(row["amount"])
    when=close39.date_ms(row["time"])
    stats=[]
    for oid,parts in orders.items():
        observed=sum((v[0] for v in parts.values()),Decimal(0))
        if observed<=0:continue
        lag=min(abs(v[2]-when) for v in parts.values())
        diff=observed-target
        stats.append(dict(order_id=oid,fill_count=len(parts),
                          observed_qty=str(observed),
                          journal_qty=str(target),
                          absolute_qty_gap=str(abs(diff)),
                          signed_qty_gap=str(diff),
                          observed_to_journal_ratio=str(observed/target),
                          nearest_fill_lag_ms=lag,
                          observed_vwap=str(sum((v[0]*v[1] for v in parts.values()),Decimal(0))/observed)))
    stats.sort(key=lambda x:(D(x["absolute_qty_gap"]),x["nearest_fill_lag_ms"],x["order_id"]))
    return stats


def _cause(row,stats):
    if not stats:return "NO_VALID_ORDER_IN_NEARBY_SIDE_FILLS"
    if len(stats)>1:return "MULTIPLE_NEARBY_ORDERS_NO_EXACT_QTY"
    c=stats[0]
    gap=D(c["signed_qty_gap"])
    if gap==0:return "EXACT_QTY_CONFLICT_WITH_PRIOR_CLASSIFICATION"
    if gap<0 and abs(gap + D("0.01"))<=D("0.000000001"):
        return "EXCHANGE_OBSERVED_QTY_SHORT_BY_0_01"
    rel=abs(gap)/D(c["journal_qty"])
    if rel<=D("0.02"):return "SMALL_QTY_DIFFERENCE_WITHIN_2PCT"
    return ("OBSERVED_ORDER_QTY_BELOW_JOURNAL" if gap<0
            else "OBSERVED_ORDER_QTY_ABOVE_JOURNAL")


def audit(user_dir,client_factory):
    root=Path(user_dir)
    parent=prior.read_status(root)
    if parent.get("version")!="v33-40" or not parent.get("source_valid") or parent.get("status")!="KNOWN":
        raise ValueError("UPSTREAM_V33_40_STALE")
    reductions=parent["unmatched_reductions"]
    if len(reductions)!=59 or sum(x["status"]=="EXCHANGE_SETTLED_SPLIT_ORDER_TEMPORAL_CANDIDATE" for x in reductions)!=33:
        raise ValueError("UPSTREAM_COHORT_CHANGED")
    journal=(root/"trades_log.jsonl").read_bytes()
    rows=[json.loads(line) for line in journal.splitlines() if line.strip()]
    events, ai_hash=_read_events(root)
    unknown=[x for x in reductions if x["status"]!="EXCHANGE_SETTLED_SPLIT_ORDER_TEMPORAL_CANDIDATE"]
    if len(unknown)!=26 or any(x["status"]!="NEARBY_SIDE_FILLS_NO_ORDER_QTY_MATCH" for x in unknown):
        raise ValueError("UNEXPECTED_26_COHORT")
    grouped={(c["symbol"],c["time"][:10]) for c in unknown}
    clients,cache,errors={},{},{}
    for symbol,day in sorted(grouped):
        try:
            if symbol not in clients:
                clients[symbol]=client_factory(symbol)
            cache[(symbol,day)]=legacy.fetch_symbol_day(clients[symbol],symbol,day)
        except Exception as exc:
            errors[(symbol,day)]=type(exc).__name__
    output=[]
    used_review_ids=defaultdict(list)
    for c in reductions:
        idx=c["row_index"]
        row=rows[idx]
        if ledger.digest(row)!=c["row_sha256"]:
            raise ValueError("ROW_CHANGED")
        action_candidates=_action_matches(row,events.get(c["symbol"],[]))
        for ev in action_candidates:
            if ev["review_id"]:
                used_review_ids[ev["review_id"]].append(idx)
        basic=dict(row_index=idx,row_sha256=c["row_sha256"],symbol=c["symbol"],
                   time=c["time"],previous_status=c["status"],
                   local_ai_action_count=len(action_candidates),
                   local_ai_actions=action_candidates,
                   direct_journal_order_id=None,
                   direct_execution_ownership_proven=False,
                   correction_eligible=False)
        if c["status"]=="EXCHANGE_SETTLED_SPLIT_ORDER_TEMPORAL_CANDIDATE":
            output.append(dict(basic,status="SPLIT_SETTLED_PROVENANCE_UNRESOLVED",
                               candidate_order_id=c["candidate_order"]["order_id"],
                               candidate_fill_count=c["candidate_order"]["n_fills"],
                               settlement_bill_count=len(c["settlement"]["bill_ids"]),
                               reason_code="AI_ACTION_HAS_NO_EXCHANGE_ORDER_ID"))
            continue
        key=(c["symbol"],c["time"][:10])
        if key in errors:
            output.append(dict(basic,status="EXCHANGE_HISTORY_UNAVAILABLE",
                               reason_code=errors[key]))
            continue
        stats=_fill_group_stats(row,cache[key])
        relative_gap=(abs(D(stats[0]["signed_qty_gap"])) / D(stats[0]["journal_qty"])
                      if stats else None)
        output.append(dict(basic,status="QUANTITY_DISCREPANCY_DIAGNOSTIC",
                           reason_code=_cause(row,stats),nearby_order_count=len(stats),
                           relative_gap_pct=(str(relative_gap*100) if relative_gap is not None else None),
                           relative_gap_band=("AT_MOST_2PCT" if relative_gap is not None and relative_gap<=D("0.02") else "ABOVE_2PCT"),
                           nearest_order=(stats[0] if stats else None),
                           nearby_order_candidates=stats[:5]))
    collisions={k for k,v in used_review_ids.items() if len(set(v))>1}
    for rec in output:
        if any(e.get("review_id") in collisions for e in rec["local_ai_actions"]):
            rec["ai_action_correlation"]="REVIEW_ID_REUSED_AMBIGUOUS"
        elif len(rec["local_ai_actions"])==1 and rec["local_ai_actions"][0].get("review_id"):
            rec["ai_action_correlation"]="UNIQUE_LOCAL_ACTION_TIME_QTY_ONLY"
        elif len(rec["local_ai_actions"])>1:
            rec["ai_action_correlation"]="MULTIPLE_LOCAL_ACTIONS"
        else:
            rec["ai_action_correlation"]="NO_UNIQUE_LOCAL_AI_ACTION"
    counts=Counter(r["status"] for r in output)
    sizes=Counter(r.get("reason_code") for r in output if r["status"]=="QUANTITY_DISCREPANCY_DIAGNOSTIC")
    actions=Counter(r["ai_action_correlation"] for r in output)
    relative_bands=Counter(r["relative_gap_band"] for r in output
                           if r["status"]=="QUANTITY_DISCREPANCY_DIAGNOSTIC")
    return dict(version=VERSION,mode="READ_ONLY_LOCAL_ACTION_AND_SIZE_DISCREPANCY",
        may_execute=False,may_correct_ledger=False,
        observed_at=datetime.now(close39.KST).isoformat(),
        journal_sha256=hashlib.sha256(journal).hexdigest(),
        source_v33_40_sha256=hashlib.sha256((root/prior.FILE).read_bytes()).hexdigest(),
        historical_ai_log_sha256=ai_hash,
        reductions_in_scope=len(output),settled_split_candidates=sum(
            r["status"]=="SPLIT_SETTLED_PROVENANCE_UNRESOLVED" for r in output),
        discrepancy_rows=sum(r["status"]=="QUANTITY_DISCREPANCY_DIAGNOSTIC" for r in output),
        status_counts=dict(counts),ai_action_correlation_counts=dict(actions),
        mismatch_reason_counts=dict(sizes),
        mismatch_relative_impact_counts=dict(relative_bands),
        local_execution_to_okx_order_id_proven=0,
        correction_eligible_count=0,
        unavailable_symbol_days=[dict(symbol=s,day=day,error_type=e)
                                 for (s,day),e in sorted(errors.items())],
        records=output,
        caveats=[
            "Local AI action review_id and quantity/time are independent corroboration, NOT direct exchange-order ownership.",
            "A candidate order can be exchange-settled with exact fill and bill IDs while historic journal ownership remains unproven.",
            "Nearby fill quantity discrepancy does NOT establish skipped exchange execution or incorrect journal PnL.",
            "Historical availability may be incomplete, even if API calls succeeded.",
            "No execution, funding, live strategy, original journal, or PnL was changed."])


def save(path,body):
    path=Path(path)
    fd,tmp=tempfile.mkstemp(prefix=".v33_41_",dir=path.parent)
    try:
        os.fchmod(fd,0o600)
        with os.fdopen(fd,"w",encoding="utf-8") as f:
            json.dump(body,f,ensure_ascii=False,indent=2,allow_nan=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)


def read_status(user_dir):
    root=Path(user_dir)
    try:
        data=json.loads((root/FILE).read_text())
        if data.get("version")!=VERSION:return dict(status="INVALID_VERSION",may_execute=False)
        if hashlib.sha256((root/prior.FILE).read_bytes()).hexdigest()!=data["source_v33_40_sha256"]:
            return dict(status="STALE_PARENT",may_execute=False)
        parent=prior.read_status(root)
        if not parent.get("source_valid"):return dict(status="STALE_PARENT",may_execute=False)
        rows=[json.loads(x) for x in (root/"trades_log.jsonl").read_bytes().splitlines() if x.strip()]
        invalid=sum(type(c["row_index"])!=int or c["row_index"]<0
                    or c["row_index"]>=len(rows) or ledger.digest(rows[c["row_index"]])!=c["row_sha256"]
                    for c in data["records"])
        return dict(data,status="KNOWN" if invalid==0 else "STALE",
                    source_valid=invalid==0,invalidated_cases=invalid,
                    may_execute=False,may_correct_ledger=False)
    except (OSError,ValueError,TypeError,KeyError,IndexError):
        return dict(status="UNAVAILABLE",may_execute=False)


def main():
    import argparse
    import config
    import okx_client
    p=argparse.ArgumentParser()
    p.add_argument("--user-dir",required=True)
    opt=p.parse_args()
    conf=config.UserConfig(opt.user_dir)
    data=audit(opt.user_dir,lambda symbol:okx_client.OkxClient(symbol,conf))
    save(Path(opt.user_dir)/FILE,data)
    print(json.dumps(dict(version=data["version"],records=data["reductions_in_scope"],
        action_counts=data["ai_action_correlation_counts"],size_counts=data["mismatch_reason_counts"],
        evidence_statuses=data["status_counts"]),ensure_ascii=False))


if __name__=="__main__":
    raise SystemExit(main())
