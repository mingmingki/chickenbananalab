"""v33-73 Candidate C historical tail-loss and residual-hold counterfactual.

Read-only diagnostic, NOT executable trading policy or candle-by-candle backtest.
The possible immediate remaining-close price is assumed equal to an *observed*
reduce fill; actual full-quantity execution at that price is unproven. Add an
explicit 20bp adverse-execution allowance on that hypothetical quantity.
"""
from __future__ import annotations
import datetime as dt
import hashlib,json,math
from collections import Counter

CONTRACT_SIZE={"DOGE/USDT:USDT":1000.0,"SOL/USDT:USDT":1.0}
EXTRA_ADVERSE_EXECUTION_BPS=20.0
REPLAY_POLICY_THRESHOLDS=(0.0,.15,.25,.35,.5,.75)
DATE_SPLITS={"all":None,"pre_2026_10_05":"before_2026-10-05",
             "since_2026_10_05":"after_2026-10-05",
             "since_2026_10_13":"after_2026-10-13"}

def finite(value):
    try:
        v=float(value)
        return v if math.isfinite(v) else None
    except (TypeError,ValueError,OverflowError):
        return None

def one(life):
    """Only one open, one reduce, one close; exclude ambiguous ownership."""
    events=life.get("events") or []
    if (life.get("coverage")!="complete" or
        [e.get("type") for e in events]!=["open","reduce","close"]):
        return None,"MULTI_REDUCE_OR_INCOMPLETE"
    sym=life.get("symbol")
    contract=CONTRACT_SIZE.get(sym)
    if contract is None:return None,"UNKNOWN_CONTRACT_SIZE"
    op,red,cl=events
    side=op.get("side")
    if side not in ("long","short") or side != red.get("side") or side != cl.get("side"):
        return None,"SIDE_MISMATCH"
    numbers=[finite(x) for x in
             (op.get("price"),op.get("sl_price"),op.get("amount"),
              red.get("close_price"),red.get("amount"),
              cl.get("close_price"),cl.get("amount"))]
    if any(v is None or v<=0 for v in numbers):
        return None,"MISSING_PRICE_OR_STOP"
    entry,stop,original,partial_px,partial_qty,final_px,remaining=numbers
    if abs(original-partial_qty-remaining)>0.011:
        return None,"EXCHANGE_QUANTITY_SPLIT_AMBIGUOUS"
    sign=1 if side=="long" else -1
    if (entry-stop)*sign <=0:
        return None,"INITIAL_STOP_NOT_ADVERSE"
    risk_width=abs(entry-stop)
    drawdown_r=max(0.,-(partial_px-entry)*sign/risk_width)
    delta_gross=(partial_px-final_px)*sign*remaining*contract
    extra_slip=partial_px*remaining*contract*EXTRA_ADVERSE_EXECUTION_BPS/10000
    delta_net_proxy=delta_gross-extra_slip
    notional=original*entry*contract
    initial_risk=risk_width*original*contract
    base=finite(life.get("net_pnl"))
    if base is None:return None,"BASELINE_NET_UNKNOWN"
    ret=dict(symbol=sym,entry_time=life['entry_time'],exit_time=life['exit_time'],
       actual_lifecycle_net_usdt=base,side=side,remaining_contracts=remaining,
       original_contracts=original,initial_stop_distance_pct=100*risk_width/entry,
       planned_stop_gross_usdt=initial_risk,entry_notional_usdt=notional,
       first_reduce_adverse_r=drawdown_r,first_reduce_is_losing=(partial_px-entry)*sign<0,
       retrospective_immediate_residual_exit_delta_before_cost_usdt=delta_gross,
       extra_execution_allowance_usdt=extra_slip,
       retrospective_net_delta_after_20bp_usdt=delta_net_proxy,
       first_reduce_timestamp=red['time'],
       reason=cl.get('reason'))
    return ret,None

def summarize(rows):
    wins=[r for r in rows if r['retrospective_net_delta_after_20bp_usdt']>0]
    net=sum(r['retrospective_net_delta_after_20bp_usdt'] for r in rows)
    return dict(n=len(rows),ex_post_delta_usdt=round(net,6),
        ex_post_positive_cases=len(wins),ex_post_negative_cases=len(rows)-len(wins),
        baseline_realized_net_usdt=round(sum(r['actual_lifecycle_net_usdt'] for r in rows),6))

def report(lifecycles):
    subset=[x for x in lifecycles if x.get('strategy_group')=='candidate_c']
    rows=[];excluded=Counter()
    for life in subset:
        result,reason=one(life)
        if result is None:excluded[reason]+=1
        else:rows.append(result)
    rows.sort(key=lambda x:x['entry_time'])
    periods={
        "all":rows,
        "pre_2026_10_05":[r for r in rows if r['entry_time'][:10]<'2026-10-05'],
        "since_2026_10_05":[r for r in rows if r['entry_time'][:10]>='2026-10-05'],
        "since_2026_10_13":[r for r in rows if r['entry_time'][:10]>='2026-10-13'],
    }
    overall={k:summarize(v) for k,v in periods.items()}
    scenarios={}
    for threshold in REPLAY_POLICY_THRESHOLDS:
        hits=[r for r in rows if r['first_reduce_is_losing'] and
              r['first_reduce_adverse_r']>=threshold]
        scenarios[str(threshold)]={
            key:summarize([r for r in hits if r in split]) for key,split in periods.items()
        }
    largest=sorted(rows,key=lambda x:x['actual_lifecycle_net_usdt'])[:8]
    for r in largest:
        pass
    caution=[
      "EX_POST_COUNTERFACTUAL_NOT_BACKTEST: earlier full-exit price is assumed to equal the observed partial fill.",
      "No contemporaneous 5-minute market depth, additional slippage, bar-by-bar protection or causal execution proof.",
      "20 basis-point allowance is a sensitivity scenario, not proven execution cost.",
      "No strategy threshold approved: multiple historical thresholds examined and recent samples are too small.",
      "Initial-stop planned gross loss is price-distance x contract quantity x instrument contractSize, not actual account loss budget; historical equity is not known.",
    ]
    return dict(version="v33-73",scope="CANDIDATE_C_READ_ONLY_SHADOW",
        total_completed_lifecycles=len(subset),
        matched_one_reduce_lifecycles=len(rows),excluded=dict(excluded),
        contract_size_assumptions=CONTRACT_SIZE,
        assumed_adverse_execution_allowance_bps=EXTRA_ADVERSE_EXECUTION_BPS,
        cohorts=overall,adverse_reduce_thresholds=scenarios,
        top_losing_lifecycles=[{k:r[k] for k in
            ('symbol','entry_time','exit_time','actual_lifecycle_net_usdt',
             'initial_stop_distance_pct','planned_stop_gross_usdt',
             'first_reduce_adverse_r','retrospective_net_delta_after_20bp_usdt')}
             for r in largest],
        all_scenarios=[{k:r[k] for k in
            ('symbol','entry_time','actual_lifecycle_net_usdt',
             'first_reduce_is_losing','first_reduce_adverse_r',
             'retrospective_net_delta_after_20bp_usdt')}
             for r in rows],
        notes=caution,
        recommendation="DO_NOT_DEPLOY_IMMEDIATE_FULL_EXIT_POLICY",
        strategy_changed=False,orders_changed=False)

def run(user_dir):
    import trade_learning_lifecycle
    from pathlib import Path
    path=Path(user_dir)/"trades_log.jsonl"
    before=path.read_bytes()
    results=report(trade_learning_lifecycle.build_completed_lifecycles(str(user_dir)))
    results["journal_sha256"]=hashlib.sha256(before).hexdigest()
    results["observed_at"]=dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).isoformat()
    if path.read_bytes()!=before:raise RuntimeError("JOURNAL_CHANGED_DURING_ANALYSIS")
    return results
