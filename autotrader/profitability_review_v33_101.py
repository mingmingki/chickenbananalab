"""v33-101 read-only GPT profitability evidence. NEVER places/changes orders.

All comparisons are observational. Price-path scenarios are explicitly shadow.
"""
from __future__ import annotations
import collections
import datetime as dt
import hashlib
import json
import math
from pathlib import Path

KST = dt.timezone(dt.timedelta(hours=9))
CUTOVER = dt.datetime(2026, 10, 22, 10, 51, tzinfo=KST)
FILE = "profitability_evidence_v33_101.json"

def _time(value):
    try:
        value = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return value.replace(tzinfo=KST) if value.tzinfo is None else value.astimezone(KST)
    except (ValueError, TypeError, OverflowError):
        return None

def _number(value):
    if isinstance(value, bool):return 0.0
    try:
        result=float(value or 0)
        return result if math.isfinite(result) else 0.0
    except (ValueError, TypeError, OverflowError):
        return 0.0

def _jsonl(path):
    if not path.is_file():return []
    rows=[]
    with path.open(encoding="utf-8") as f:
        for line in f:
            try:
                value=json.loads(line)
                if isinstance(value,dict): rows.append(value)
            except (ValueError,TypeError):
                pass
    return rows

def _net(row):
    if row.get("okx_net_pnl") is not None:
        return _number(row["okx_net_pnl"])
    return _number(row.get("pnl"))-_number(row.get("fee"))

def _money(rows):
    gross=sum(_number(x.get("pnl")) for x in rows)
    fee=sum(_number(x.get("fee")) for x in rows)
    net=sum(_net(x) for x in rows)
    return {"events":len(rows),"gross_usdt":round(gross,6),
        "fee_usdt":round(fee,6),"net_usdt":round(net,6),
        "fee_exceeded_gross_count":sum(_net(x)<=0 and _number(x.get("pnl"))>0 for x in rows),
        "negative_net_count":sum(_net(x)<0 for x in rows)}

def evaluate(user_dir, *, now=None, cutover=CUTOVER):
    """Return a bounded, deterministic report from durable existing evidence.

    Report is not a hypothetical performance guarantee or authorization to trade.
    """
    root=Path(user_dir)
    now=now or dt.datetime.now(KST)
    if now.tzinfo is None:now=now.replace(tzinfo=KST)
    eligible=lambda x: (t:=_time(x.get("time"))) is not None and cutover<=t<=now
    records=[x for x in _jsonl(root/"trades_log.jsonl") if eligible(x) and not x.get("dry_run")]
    exits=[x for x in records if x.get("type") in ("reduce","close")]
    reductions=[x for x in exits if x.get("type")=="reduce"]
    closes=[x for x in exits if x.get("type")=="close"]
    decisions=[x for x in _jsonl(root/"token_usage.jsonl") if eligible(x) and x.get("provider")=="openai"]
    attempts=[x for x in _jsonl(root/"ai_call_attempts.jsonl") if eligible(x) and x.get("provider")=="openai"]
    reason_counts=collections.Counter(x.get("reason") or "unknown" for x in exits)
    purposes=collections.defaultdict(list)
    for record in decisions:purposes[str(record.get("purpose"))].append(record)
    by_purpose={
        k:dict(successes=len(v),estimated_cost_usd=round(sum(_number(x.get("cost_usd")) for x in v),6),
               input_tokens=sum(int(x.get("input_tokens") or 0) for x in v),
               output_tokens=sum(int(x.get("output_tokens") or 0) for x in v))
        for k,v in sorted(purposes.items())
    }
    # A repeatable exact request fingerprint, not an assertion of economic equivalence.
    fingerprints=collections.Counter(
        (r.get("model"),r.get("purpose"),r.get("symbol"),r.get("input_fingerprint"))
        for r in decisions if r.get("input_fingerprint"))
    duplicate_fingerprints=sum(max(n-1,0) for n in fingerprints.values())
    with_fingerprint=sum(bool(r.get("input_fingerprint")) for r in decisions)
    output_tokens=sum(v["output_tokens"] for v in by_purpose.values())
    input_tokens=sum(v["input_tokens"] for v in by_purpose.values())
    # Rates are current ledger *assumptions*, not independently verified invoices.
    rate_input=0.25
    rate_output=2.0
    weighted_output_share=(output_tokens*rate_output) / max(1e-15,input_tokens*rate_input+output_tokens*rate_output)
    stages=collections.Counter((r.get("stage") or "unknown",r.get("error_type") or "ok") for r in attempts)
    raw_tracker=_jsonl(root/"candidate_c_setup_tracker.jsonl")
    states=[r for r in raw_tracker if r.get("type")=="state" and
         isinstance(r.get("timestamp"),(int,float)) and cutover.timestamp()*1000<=r["timestamp"]<=now.timestamp()*1000]
    outcome=[]
    for r in raw_tracker:
        if r.get("type")!="attempt_outcome":continue
        parts=str(r.get("setup_id","")).rsplit("|",1)
        if len(parts)<2 or not parts[-1].isdigit():continue
        if cutover.timestamp()*1000<=int(parts[-1])<=now.timestamp()*1000:outcome.append(r)
    reason_frequency=collections.Counter(r.get("outcome") or "unknown" for r in outcome)
    eligibility=collections.defaultdict(lambda:{"true":0,"false":0,"unknown":0})
    for r in states:
        state=r.get("new_state",r.get("entry_eligible"))
        eligibility[str(r.get("symbol") or "unknown")][
            "true" if state is True else "false" if state is False else "unknown"]+=1
    previous=root/"strategy_exit_scenario_v33_55.json"
    try:scenario=json.loads(previous.read_text())
    except (OSError,ValueError,TypeError):scenario={}
    source_hashes={}
    for name in ("trades_log.jsonl","token_usage.jsonl","candidate_c_setup_tracker.jsonl","strategy_exit_scenario_v33_55.json"):
        p=root/name
        if p.exists():
            h=hashlib.sha256()
            with p.open("rb") as f:
                for chunk in iter(lambda:f.read(1024*1024),b""):h.update(chunk)
            source_hashes[name]=h.hexdigest()
    return {
        "version":"v33-101","mode":"READ_ONLY_SHADOW_NO_ORDER_OR_RISK_CHANGE",
        "created_at":now.isoformat(),"since":cutover.isoformat(),
        "workstreams":{
            "partial_reductions":{"observed":_money(reductions),
                "fee_aware_existing_guard":True,
                "recommended_live_change":False,
                "finding":"Discretionary thesis-intact reductions have fee-positive preflight; defensive exits must not be blocked merely for realized fee losses."},
            "full_closes":{"observed":_money(closes),
                "reasons":dict(reason_counts),
                "historical_population":scenario.get("core_ai_exit_population"),
                "verified_market_paths":scenario.get("core_verified_price_path_count"),
                "excluded":scenario.get("core_exclusion_reasons"),
                "first_barrier_counts":scenario.get("core_first_barrier_counts"),
                "price_path_scenarios":scenario.get("scenario_price_proxy_by_incremental_friction_bps"),
                "new_gpt_counterfactual_verified":False,
                "recommended_live_change":False,
                "finding":"Legacy HOLD price proxy includes stop-first cases; changing live CLOSE/HOLD action requires lifecycle-matched replay."},
            "gpt_efficiency":{"successful_calls":len(decisions),"attempts":len(attempts),
                "api_errors":sum(bool(x.get("error_type")) for x in attempts),
                "api_timeouts":sum(x.get("timed_out") is True for x in attempts),
                "calls_with_fingerprint":with_fingerprint,
                "identical_input_duplicate_calls":duplicate_fingerprints,
                "by_purpose":by_purpose,"attempt_stage_errors":{f"{k[0]}|{k[1]}":n for k,n in stages.items()},
                "assumed_output_cost_fraction":round(weighted_output_share,4),
                "recommended_live_change":False,
                "finding":"No proven identical repeated requests. Shorter output/reasoning requires side-by-side directional replay before enabling."},
            "candidate_c":{"setup_transition_count":len(states),
                "eligibility_transition_by_symbol":dict(eligibility),
                "blocked_outcome_reasons":dict(reason_frequency),
                "new_primary_gpt_calls":by_purpose.get("candidate_primary_entry",{}).get("successes",0),
                "recommended_live_change":False,
                "finding":"Upstream 30m stale/mismatch/strong-confirmation filters prevent candidate from reaching GPT; do not drop protection without bar-level outcome evidence."}
        },
        "source_sha256":source_hashes,
        "limitations":[
            "Post-cutover trades may have changing unrealized PnL; this reads realized only.",
            "Price-path comparison is legacy shadow evidence, not proof that recent GPT exits should change.",
            "Log-recorded API costs are estimates and may not equal invoices.",
            "Candidate eligibility times can repeat across event ticks; observations are not independent trades."
        ]
    }

def store(user_dir,*,now=None):
    root=Path(user_dir)
    report=evaluate(root,now=now)
    path=root/FILE
    temp=path.with_suffix(".json.tmp")
    temp.write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding="utf-8")
    temp.replace(path)
    return report

if __name__=="__main__":
    import argparse
    parser=argparse.ArgumentParser()
    parser.add_argument("user_dir")
    args=parser.parse_args()
    print(json.dumps(store(args.user_dir),ensure_ascii=False,indent=2))
