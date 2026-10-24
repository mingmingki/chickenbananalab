"""v33-25: read-only causal-timeline attribution, not a causal profitability claim.

Joins historical MFE triggers via exact journal lifecycle identity and v33-24
dual shadow via a bounded entry-time correlation. No order/strategy authority.
Verified figures reflect journal OKX-derived net, NOT a full OKX bills audit.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
import math

KST=timezone(timedelta(hours=9))
CORE=frozenset(("BTC/USDT:USDT","ETH/USDT:USDT",
                "XRP/USDT:USDT","ADA/USDT:USDT"))
VERIFIED_SOURCES=frozenset(("okx_realized","order_fills"))
POLICIES=("arm_0.40_giveback_0.20","arm_0.50_giveback_0.25")


def dt_kst(value):
    if not isinstance(value,str):return None
    try:
        v=datetime.fromisoformat(value.replace("Z","+00:00"))
        return (v.replace(tzinfo=KST) if v.tzinfo is None else v.astimezone(KST))
    except (ValueError,OverflowError,TypeError):return None


def finite(value):
    if isinstance(value,bool):return None
    try:
        result=float(value)
        return result if math.isfinite(result) else None
    except (ValueError,OverflowError,TypeError):return None


def read_lines(path):
    f=Path(path)
    if not f.is_file():return [],1
    out=[];invalid=0
    with f.open(encoding="utf-8") as handle:
        for line in handle:
            try:
                v=json.loads(line)
                if isinstance(v,dict):out.append(v)
                else:invalid+=1
            except (ValueError,TypeError):invalid+=1
    return out,invalid


def lifecycles(rows):
    active={}
    finished=[]
    skipped=Counter()
    execution_seen=set()
    for row in rows:
        symbol=row.get("symbol")
        if symbol not in CORE:continue
        if row.get("dry_run") is not False:continue
        if row.get("strategy_group")!="core":
            skipped["non_core_or_untagged"]+=1
            continue
        when=dt_kst(row.get("time"))
        if when is None:
            skipped["invalid_time"]+=1;continue
        typ=row.get("type")
        if typ=="open":
            side=row.get("side")
            price=finite(row.get("price"))
            if side not in ("long","short") or price is None or price<=0:
                skipped["invalid_open"]+=1;continue
            if symbol in active:
                active[symbol]["status"]="SUPERSEDED_OPEN_WITHOUT_CLOSE"
                finished.append(active.pop(symbol))
            token=f"journal:{symbol}|{side}|{row['time']}"
            active[symbol]=dict(symbol=symbol,side=side,open_time=when,
                open_price=price,journal_id=token,events=[],status="OPEN",
                close_time=None,invalid=False,unknown_reasons=[])
        elif typ in ("reduce","close"):
            item=active.get(symbol)
            if item is None:
                skipped["orphan_exit"]+=1;continue
            if row.get("side")!=item["side"] or when<item["open_time"]:
                item["invalid"]=True
                item["unknown_reasons"].append("exit_side_or_time_conflict")
                continue
            execution_id=row.get("execution_id")
            if not execution_id:
                item["invalid"]=True
                item["unknown_reasons"].append("missing_execution_id")
            elif execution_id in execution_seen:
                # Journal append already deduplicates by execution ID. A collision
                # in externally supplied evidence must not be counted twice.
                item["invalid"]=True
                item["unknown_reasons"].append("duplicate_execution_id")
                skipped["duplicate_exit_execution_id"]+=1
                continue
            else:execution_seen.add(execution_id)
            net=finite(row.get("okx_net_pnl"))
            if net is None or row.get("pnl_source") not in VERIFIED_SOURCES:
                item["invalid"]=True
                item["unknown_reasons"].append("net_not_verified")
            item["events"].append(dict(type=typ,time=when,execution_id=execution_id,
                                       net=net,source=row.get("pnl_source")))
            if typ=="close":
                item["status"]="CLOSED"
                item["close_time"]=when
                finished.append(active.pop(symbol))
    finished.extend(active.values())
    for item in finished:
        complete=(item["status"]=="CLOSED" and bool(item["events"])
                  and item["events"][-1]["type"]=="close")
        verified=complete and not item["invalid"]
        item["realized_status"]=("CLOSED_VERIFIED_JOURNAL" if verified
              else "OPEN_PENDING" if item["status"]=="OPEN"
              else "CLOSED_UNVERIFIED" if complete else "UNKNOWN_LIFECYCLE")
        item["realized_net"]=round(sum(e["net"] for e in item["events"]),8) if verified else None
    return finished,dict(skipped)


def _row(life,source,classification,time,match_proof,raw=None):
    complete=life["realized_status"]=="CLOSED_VERIFIED_JOURNAL"
    events=life["events"]
    after=([e for e in events if e["time"]>=time] if complete else [])
    prior=([e for e in events if e["time"]<time] if complete else [])
    first_reduce=next((e for e in after if e["type"]=="reduce"),None)
    final=life["close_time"]
    duration=(final-time).total_seconds()/60 if final and final>=time else None
    return dict(symbol=life["symbol"],side=life["side"],
       journal_lifecycle_id=life["journal_id"],signal_type=classification,
       signal_source=source,match_proof=match_proof,
       signal_time=time.isoformat(timespec="seconds"),
       open_time=life["open_time"].isoformat(timespec="seconds"),
       close_time=final.isoformat(timespec="seconds") if final else None,
       lifecycle_status=life["realized_status"],
       journal_net_usdt=life["realized_net"],
       signal_to_close_minutes=round(duration,2) if duration is not None else None,
       verified_after_signal_net_usdt=round(sum(e["net"] for e in after),8) if complete else None,
       verified_before_signal_net_usdt=round(sum(e["net"] for e in prior),8) if complete else None,
       after_signal_reduce_count=sum(e["type"]=="reduce" for e in after) if complete else None,
       first_reduce_after_signal_minutes=(round((first_reduce["time"]-time).total_seconds()/60,2)
           if first_reduce else None),
       observed_mfe_r=finite((raw or {}).get("mfe_r")),
       observed_current_r=finite((raw or {}).get("current_r")),
       observed_giveback_r=finite((raw or {}).get("giveback_r")),
       claim="TIMELINE_ASSOCIATION_ONLY_NOT_CAUSAL",
       may_execute=False)


def join(journal_rows,mfe_rows,dual_rows):
    lives,skipped=lifecycles(journal_rows)
    by_id={life["journal_id"]:life for life in lives}
    timeline=[]
    unmatched=Counter()
    mfe_unique=set()
    # Historical exact journal identity, first trigger per policy and lifecycle.
    for record in mfe_rows:
        if record.get("event_type")!="mfe_profit_shadow_trigger":continue
        policy=record.get("policy")
        if policy not in POLICIES:continue
        life=by_id.get(record.get("position_identity"))
        time=dt_kst(record.get("observed_at"))
        if life is None or time is None:
            unmatched["mfe_missing_exact_lifecycle"]+=1;continue
        if (record.get("symbol")!=life["symbol"] or record.get("side")!=life["side"]
            or not life["open_time"]<=time
            or life["close_time"] is not None and time>=life["close_time"]):
            unmatched["mfe_time_or_side_conflict"]+=1;continue
        identity=(life["journal_id"],policy)
        if identity in mfe_unique:continue
        mfe_unique.add(identity)
        timeline.append(_row(life,"MFE_EXACT_JOURNAL_ID",policy,time,
                             "EXACT_JOURNAL_LIFECYCLE_ID",record))
    dual_unique=set()
    for record in dual_rows:
        if record.get("mode")!="SHADOW_ONLY" or record.get("may_execute") is not False:
            unmatched["invalid_dual_shadow_mode"]+=1;continue
        symbol=record.get("symbol")
        side=record.get("side")
        stamp=dt_kst(record.get("observed_at"))
        entry_id=record.get("position_identity")
        if symbol not in CORE or stamp is None or not isinstance(entry_id,str) or not entry_id.isdigit():
            unmatched["dual_missing_time_or_identity"]+=1;continue
        # Execution-time epoch from exchange may precede journal open by a few
        # seconds. It is NOT an execution ID; only a bounded temporal match.
        inferred_entry=datetime.fromtimestamp(int(entry_id)/1000,KST)
        matching=[life for life in lives if life["symbol"]==symbol and life["side"]==side
                  and abs((life["open_time"]-inferred_entry).total_seconds())<=120
                  and life["open_time"]<=stamp
                  and (life["close_time"] is None or stamp<life["close_time"])
                  and life["status"] in ("OPEN","CLOSED")]
        if len(matching)!=1:
            unmatched["dual_not_uniquely_correlated"]+=1;continue
        classification=record.get("classification")
        life=matching[0]
        identity=(life["journal_id"],classification)
        if identity in dual_unique:continue
        dual_unique.add(identity)
        timeline.append(_row(life,"DUAL_BOUNDED_ENTRY_EPOCH",
            classification,stamp,"TEMPORAL_CORRELATION_NOT_EXECUTION_ID",record))

    timeline.sort(key=lambda x:(x["signal_time"],x["symbol"],x["signal_type"]))
    by_bucket=defaultdict(list)
    for row in timeline:
        by_bucket[(row["signal_source"],row["signal_type"])].append(row)
    groups={}
    for key,items in sorted(by_bucket.items()):
        confirmed=[r for r in items if r["lifecycle_status"]=="CLOSED_VERIFIED_JOURNAL"]
        groups["|".join(key)]=dict(
            total_signals=len(items),verified_completed=len(confirmed),
            pending_or_unverified=len(items)-len(confirmed),
            verified_lifecycle_net_usdt=round(sum(r["journal_net_usdt"] for r in confirmed),4),
            # Journal net is a full lifecycle property; signal association is not
            # an incremental strategy gain/loss attributable to the signal.
            verified_after_signal_net_usdt=round(
                sum(r["verified_after_signal_net_usdt"] for r in confirmed),4),
            verified_with_post_signal_reduce=sum((r["after_signal_reduce_count"] or 0)>0
                for r in confirmed),
            post_signal_close_minutes=[
                r["signal_to_close_minutes"] for r in confirmed],
            data_quality="JOURNAL_VERIFIED_SUBSET_ONLY")
    return dict(version="v33-25",scope="CORE_ONLY_READ_ONLY",
                caveats=["No claims of causal gain or optimal exit",
                         "No bill-level all-orders reconciliation",
                         "Current OPEN and unverified closes excluded from net",
                         "Historical trigger cohorts overlap across policies",
                         "DUAL uses bounded time alignment, not exact execution ID",
                         "Observed execution net may omit entry fees/funding"],
                lifecycle_count=len(lives),journal_skips=skipped,
                unmatched_signals=dict(unmatched),
                group_summary=groups,timeline=timeline)


def from_user_dir(user_dir,limit=80):
    folder=Path(user_dir)
    names=("trades_log.jsonl","mfe_profit_shadow_log.jsonl",
           "core_exit_dual_audit_v33_24.jsonl")
    rows=[];malformed={}
    for name in names:
        group,invalid=read_lines(folder/name)
        malformed[name]=invalid
        rows.append(group)
    result=join(*rows)
    result["malformed_source_lines"]=malformed
    result["timeline_count"]=len(result["timeline"])
    result["timeline"]=result["timeline"][-max(0,min(int(limit),500)):] if limit else []
    return result
