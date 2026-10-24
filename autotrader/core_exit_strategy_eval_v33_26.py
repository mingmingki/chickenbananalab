"""v33-26: event-timed, fee-sensitive, chronology-split CORE strategy evaluation.

Compares full verified cohort, including trades without MFE triggers. All
historical observed signal prices are in the contemporaneous MFE journal; no
future bars are consulted. Existing live partials are NOT counterfactually
duplicated: only signals before first partial are eligible.

There are no order/API/model calls in this module. Results are research-only.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
import json
import math
import statistics

import core_exit_outcome_join_v33_25 as join

# These are OKX SWAP contract sizes used only to verify exit-quantity alignment.
# Must revalidate against exchange specifications if contracts change.
CONTRACT_SIZE={
    "BTC/USDT:USDT":0.01, "ETH/USDT:USDT":0.1,
    "XRP/USDT:USDT":100.0, "ADA/USDT:USDT":100.0,
}
POLICIES=("arm_0.40_giveback_0.20","arm_0.50_giveback_0.25")
FRACTIONS=(0.25,0.5)
SLIPPAGES=(0.0005,0.0015)
EXTRA_FEE=0.001 # 10bps absolute entry-price notional cost of diverted fraction


def numeric(x):
    return join.finite(x)


def _max_drawdown(ordered_pnl):
    peak=0.0; capital=0.0; drawdown=0.0
    for net in ordered_pnl:
        capital+=net;peak=max(peak,capital)
        drawdown=max(drawdown,peak-capital)
    return round(drawdown,6)


def _close_events_including_prices(life,raw_events):
    """Strict lifecycle-owned exit prices from the source journal."""
    matches=[]
    sym=life["symbol"]
    for row in raw_events:
        if row.get("symbol")!=sym or row.get("strategy_group")!="core" or row.get("dry_run") is not False:
            continue
        if row.get("type") not in ("reduce","close"):continue
        when=join.dt_kst(row.get("time"))
        if when is None or when<life["open_time"] or (life["close_time"] is not None and when>life["close_time"]):
            continue
        if row.get("side")!=life["side"]:continue
        matches.append(row)
    # Historical journal may contain preceding same-side positions. Restrict
    # by matching ONLY the exact sequence of verified execution IDs.
    expected={x["execution_id"] for x in life["events"]}
    found=[r for r in matches if r.get("execution_id") in expected]
    if len(found)!=len(expected) or len(expected)!=len(life["events"]):
        return None,"missing_matching_execution_ids"
    qty=[];price=[]
    for event in found:
        q=numeric(event.get("amount"))
        p=numeric(event.get("close_price"))
        if q is None or p is None or q<=0 or p<=0:return None,"missing_exit_quantity_or_price"
        qty.append(q);price.append(p)
    opened=[r for r in raw_events if r.get("type")=="open"
            and r.get("symbol")==sym and r.get("side")==life["side"]
            and r.get("time")==life["open_time"].replace(tzinfo=None).isoformat(timespec="seconds")
            and r.get("dry_run") is False and r.get("strategy_group")=="core"]
    if len(opened)!=1:return None,"ambiguous_open_record"
    coin=numeric(opened[0].get("amount"))
    if coin is None or coin<=0:return None,"invalid_open_quantity"
    expected_coins=sum(qty)*CONTRACT_SIZE[sym]
    if abs(expected_coins-coin)>max(coin*0.01,1e-12):
        return None,"exit_quantity_not_reconciled"
    return dict(coin=coin,
                weighted_exit=sum(p*q for p,q in zip(price,qty))/sum(qty),
                exit_events=found,
                actual_net=life["realized_net"]),None


def _signals(mfe):
    by=defaultdict(list)
    for ev in mfe:
        if ev.get("event_type")!="mfe_profit_shadow_trigger" or ev.get("policy") not in POLICIES:
            continue
        if not isinstance(ev.get("position_identity"),str):continue
        t=join.dt_kst(ev.get("observed_at"))
        price=numeric(ev.get("trigger_price"))
        if t is None or price is None or price<=0:continue
        by[(ev["position_identity"],ev["policy"])].append((t,price,ev))
    for key in by:by[key].sort(key=lambda x:x[0])
    return by


def _classification(life,has_profit_signal):
    if life["realized_status"]!="CLOSED_VERIFIED_JOURNAL":
        return "UNVERIFIED_OR_PENDING"
    net=life["realized_net"]
    last=life["events"][-1]["net"]
    partials=life["events"][:-1]
    if partials and any((ev["net"] or 0)>0 for ev in partials) and last<0:
        return "PROFITABLE_PARTIAL_RESIDUAL_LOSS"
    if net<0 and has_profit_signal:
        return "PROFIT_GIVEBACK_TO_NET_LOSS"
    if net<0:
        return "LOSS_WITHOUT_DOCUMENTED_MFE_TRIGGER"
    if net>0:return "PROFITABLE_COMPLETION"
    return "BREAKEVEN"


def _candidate(life,prices,signals,policy,fraction,slippage):
    key=(life["journal_id"],policy)
    positions=signals.get(key) or []
    if not positions:return dict(status="NO_TRIGGER",delta=0.0)
    first_event=min(e["time"] for e in life["events"])
    valid=[event for event in positions
           if life["open_time"]<event[0]<first_event]
    if not valid:return dict(status="TRIGGER_AFTER_FIRST_EXIT",delta=0.0)
    instant,price,ev=valid[0]
    sign=1 if life["side"]=="long" else -1
    entry=life["open_price"]
    # Exit only at already-observed event price, never future optimal price.
    fill=price*(1-slippage if sign==1 else 1+slippage)
    # A nominal profit guard cannot execute in loss/fee-negative territory.
    if sign*(fill-entry)/entry<=EXTRA_FEE:
        return dict(status="FILL_NOT_NET_PROFITABLE",delta=0.0)
    coin=prices["coin"]*fraction
    # First part of the original exit distribution is replaced by the
    # simulated fill. Charge EXTRA_FEE for uncertain adverse costs.
    delta=coin*(sign*(fill-prices["weighted_exit"])-EXTRA_FEE*entry)
    return dict(status="SIMULATED",delta=round(delta,7),
                observed_at=instant.isoformat(timespec="seconds"),
                original_trigger_price=price,assumed_fill=fill,
                weighted_real_exit=prices["weighted_exit"])


def _summarize(rows,policy,fraction,slippage):
    tag=f"{policy}|fraction={fraction:.2f}|slippage_bps={int(slippage*10000)}"
    actual=[r["realized_net"] for r in rows]
    simulated=[]
    triggered=0;benefit=0;harm=0;costs=0.0;missing=Counter()
    for row in rows:
        item=row["studies"][tag]
        delta=item["delta"] if item["status"]=="SIMULATED" else 0.0
        simulated.append(row["realized_net"]+delta)
        if item["status"]=="SIMULATED":
            triggered+=1;benefit+=delta>0;harm+=delta<0
        else:missing[item["status"]]+=1
    return dict(total_completed=len(rows),triggered=triggered,beneficial=benefit,
        harmful=harm,unchanged=len(rows)-triggered,
        baseline_net_usdt=round(sum(actual),4),
        simulated_net_usdt=round(sum(simulated),4),
        delta_net_usdt=round(sum(simulated)-sum(actual),4),
        baseline_realized_curve_mdd_usdt=_max_drawdown(actual),
        simulated_realized_curve_mdd_usdt=_max_drawdown(simulated),
        rejected=dict(missing),status="RESEARCH_ONLY")


def analyze(journal,mfe):
    lives,_=join.lifecycles(journal)
    signs=_signals(mfe)
    eligible=[l for l in lives if l["realized_status"]=="CLOSED_VERIFIED_JOURNAL"]
    eligible.sort(key=lambda l:(l["close_time"],l["symbol"]))
    completed=[]
    incomplete=Counter(l["realized_status"] for l in lives if l not in eligible)
    classifications=Counter()
    quantity_outcomes=Counter()
    for life in eligible:
        has_mfe=any((life["journal_id"],policy) in signs for policy in POLICIES)
        klass=_classification(life,has_mfe)
        classifications[klass]+=1
        prices,reason=_close_events_including_prices(life,journal)
        if reason:quantity_outcomes[reason]+=1
        row=dict(symbol=life["symbol"],side=life["side"],
                 journal_lifecycle_id=life["journal_id"],
                 closed_at=life["close_time"].isoformat(timespec="seconds"),
                 realized_net=life["realized_net"],classification=klass,
                 had_documented_profit_signal=has_mfe,
                 study_eligible=prices is not None,
                 study_exclusion=reason,
                 reduction_count=sum(e["type"]=="reduce" for e in life["events"]),
                 close_net_usdt=life["events"][-1]["net"],
                 studies={})
        for policy in POLICIES:
            for fraction in FRACTIONS:
                for slip in SLIPPAGES:
                    tag=f"{policy}|fraction={fraction:.2f}|slippage_bps={int(slip*10000)}"
                    row["studies"][tag]=(
                        _candidate(life,prices,signs,policy,fraction,slip)
                        if prices else dict(status="MISSING_FULL_FILL_PROOF",delta=0.0))
        completed.append(row)
    # Same all-completed cohort in each train/holdout. Small sample declared.
    cut=max(1,int(len(completed)*.60))
    cohorts={"train":completed[:cut],"holdout":completed[cut:],"all":completed}
    tables={}
    for name,group in cohorts.items():
        tables[name]={
            f"{policy}|fraction={fraction:.2f}|slippage_bps={int(slip*10000)}":
            _summarize(group,policy,fraction,slip)
            for policy in POLICIES for fraction in FRACTIONS for slip in SLIPPAGES
        }
    # Predeclared acceptance gate: must improve both net and realized drawdown,
    # with sufficient independent holdout signals. Avoid declaring success from
    # a single favorable transaction or optimizing on holdout.
    decisions={}
    for key,train in tables["train"].items():
        ho=tables["holdout"][key]
        enough=train["triggered"]>=10 and ho["triggered"]>=10
        ok=bool(enough and train["delta_net_usdt"]>0 and ho["delta_net_usdt"]>0
                and train["simulated_realized_curve_mdd_usdt"]<=train["baseline_realized_curve_mdd_usdt"]
                and ho["simulated_realized_curve_mdd_usdt"]<=ho["baseline_realized_curve_mdd_usdt"])
        decisions[key]=dict(live_eligible=ok,
             reason="PASSED_RESEARCH_SCREEN_NOT_LIVE_APPROVAL" if ok
                  else "INSUFFICIENT_SPLIT_EVIDENCE_OR_NEGATIVE_RESULT")
    return dict(version="v33-26",mode="RESEARCH_ONLY",may_execute=False,
        data_quality=dict(lifecycles_seen=len(lives),verified_completed=len(eligible),
            excluded_lifecycles=dict(incomplete),full_exit_price_exclusions=dict(quantity_outcomes),
            journal_scope="2026-10-09 onward CORE tagged records only",
            caveats=[
                "No automatic changes to orders, signal, SL/TP or live strategy.",
                "Open or unverified journal outcomes excluded from all PnL estimates.",
                "No MFE trigger does NOT establish absence of earlier unrealized profit.",
                "Signal-triggered cohorts overlap and have selection bias.",
                "Historical MFE prices are observed, NOT guaranteed executable prices.",
                "Counterfactual costs are conservative assumptions; actual entry fees and funding may be missing.",
                "Drawdown is on close-ordered realized PnL curve, NOT whole-account equity MDD.",
                "Split is by close-time; coinciding signals still share market-regime exposure.",
            ]),
        classification_counts=dict(classifications),split=dict(train=cut,
            holdout=len(completed)-cut,
            cutoff_closed_at=completed[cut-1]["closed_at"] if completed else None),
        candidate_tables=tables,decisions=decisions,completed=completed)


def from_user_dir(user_dir,*,include_completed=False):
    folder=Path(user_dir)
    j,ja=join.read_lines(folder/"trades_log.jsonl")
    m,ma=join.read_lines(folder/"mfe_profit_shadow_log.jsonl")
    report=analyze(j,m)
    report["data_quality"]["malformed_journal_lines"]=ja
    report["data_quality"]["malformed_mfe_lines"]=ma
    if not include_completed:report.pop("completed")
    return report


def main():
    import argparse
    parser=argparse.ArgumentParser()
    parser.add_argument("--user-dir",required=True)
    parser.add_argument("--output",required=True)
    args=parser.parse_args()
    report=from_user_dir(args.user_dir,include_completed=True)
    Path(args.output).write_text(json.dumps(report,ensure_ascii=False,allow_nan=False,indent=2))
    print("VERIFY",report["data_quality"])
    print("CLASS",report["classification_counts"])
    print("SPLIT",report["split"])
    for key,val in report["candidate_tables"]["all"].items():
        print(key,"TRIG",val["triggered"],"DELTA",val["delta_net_usdt"],
              "TRAIN",report["candidate_tables"]["train"][key]["delta_net_usdt"],
              "HOLDOUT",report["candidate_tables"]["holdout"][key]["delta_net_usdt"],
              "ALLOW_LIVE",report["decisions"][key]["live_eligible"])
    print("REPORT",args.output)


if __name__=="__main__":main()
