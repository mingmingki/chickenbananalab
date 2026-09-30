"""Read-only bridge between account equity PnL and bot trade journals."""
from __future__ import annotations
import datetime
import math
import pnl_reconciliation
from trade_learning_lifecycle import build_completed_lifecycles

KST=datetime.timezone(datetime.timedelta(hours=9))


def _finite(v):
    try:x=float(v)
    except (TypeError,ValueError):return None
    return x if math.isfinite(x) else None


def _record_ms(row):
    value=row.get('time') or row.get('exit_time')
    if not value:return None
    try:d=datetime.datetime.fromisoformat(value)
    except ValueError:return None
    if d.tzinfo is None:d=d.replace(tzinfo=KST)
    else:d=d.astimezone(KST)
    return int(d.timestamp()*1000)


def _net(row):
    value=row.get('okx_net_pnl')
    return float(value) if value is not None else float(row.get('pnl') or 0)-float(row.get('fee') or 0)


def realized_window(user_dir,start_ms):
    def selected(rows):
        return [r for r in rows if (_record_ms(r) is not None and _record_ms(r)>=int(start_ms))]
    closed=selected(pnl_reconciliation.load_all_records(user_dir))
    realized=selected(pnl_reconciliation.load_all_records_with_reduces(user_dir))
    def total(rows):
        return {'count':len(rows),'net_pnl':sum(_net(r) for r in rows)}
    return {
        'closed':total(closed),
        'with_reduces':total(realized),
        'journal_allocated_swap_fee_cost':sum(abs(float(r.get('fee') or 0.0)) for r in realized),
        'journal_funding_observed':sum(float(r.get('funding_fee') or 0.0) for r in realized if r.get('funding_fee') is not None),
    }


def _lifecycle_stats(rows):
    nets=[float(r.get("lifecycle_net", r.get("net_pnl") or 0.0)) for r in rows]
    if not nets:
        return {"completed_count":0,"win_rate":None,"profit_factor":None}
    wins=[v for v in nets if v>0]
    losses=[v for v in nets if v<=0]
    loss_sum=sum(losses)
    return {
        "completed_count":len(nets),
        "win_rate":len(wins)/len(nets)*100.0,
        "profit_factor":sum(wins)/abs(loss_sum) if loss_sum else None,
    }


def realized_economic_summary(user_dir):
    """Realized money uses CLOSE+REDUCE; win/PF use completed lifecycles."""
    events=pnl_reconciliation.load_all_records_with_reduces(user_dir)
    lifecycles=build_completed_lifecycles(user_dir)
    groups=("core","candidate_c","fast","legacy")

    def group_event(row):
        g=row.get("_group") or row.get("strategy_group")
        return g if g in groups else "legacy"

    def one(group=None):
        erows=events if group is None else [r for r in events if group_event(r)==group]
        lrows=lifecycles if group is None else [r for r in lifecycles if r.get("strategy_group")==group]
        gross=sum(float(r.get("pnl") or 0.0) for r in erows)
        fee=sum(float(r.get("fee") or 0.0) for r in erows)
        net=sum(_net(r) for r in erows)
        stats=_lifecycle_stats(lrows)
        return {
            "count":stats["completed_count"],
            "completed_count":stats["completed_count"],
            "realized_event_count":len(erows),
            "gross_pnl":gross,"fee":fee,
            "net_adjustment":net-(gross-fee),"net_pnl":net,
            "win_rate":stats["win_rate"],"profit_factor":stats["profit_factor"],
        }
    return {"total":one(), **{g:one(g) for g in groups}}


def fee_window(fee_state,start_ms):
    swap=spot=0.0
    for e in (fee_state or {}).get('events') or []:
        if int(e.get('ts_ms') or 0)<int(start_ms):continue
        value=_finite(e.get('fee_usdt'))
        if value is None:continue
        if str(e.get('inst_type')).upper()=='SWAP':swap+=value
        elif str(e.get('inst_type')).upper()=='SPOT':spot+=value
    return {'swap_fee_cost':swap,'spot_fee_cost':spot}


def compose(*,account_adjusted_pnl,closed_realized_net,realized_with_reduces_net,
            open_unrealized_pnl,spot_fee_cost,swap_fee_cost,boundary_excluded_net,
            adjustment_start_ms,journal_allocated_swap_fee_cost=0.0,
            actual_funding_pnl=None,journal_funding_observed=None):
    values=[account_adjusted_pnl,closed_realized_net,realized_with_reduces_net,
            open_unrealized_pnl,spot_fee_cost,swap_fee_cost,boundary_excluded_net]
    if adjustment_start_ms is None or any(_finite(v) is None for v in values):
        return {'complete':False,'adjustment_start_ms':adjustment_start_ms}
    account,closed,with_reduces,open_pnl,spot,swap,boundary=map(float,values)
    explained=with_reduces+open_pnl-abs(spot)
    return {
        'complete':True,'adjustment_start_ms':int(adjustment_start_ms),
        'account_adjusted_pnl':account,'closed_realized_net':closed,
        'realized_with_reduces_net':with_reduces,
        'reduce_only_realized_net':with_reduces-closed,
        'open_unrealized_pnl':open_pnl,'spot_fee_cost':abs(spot),
        'swap_fee_cost':abs(swap),'boundary_excluded_net':boundary,
        'journal_allocated_swap_fee_cost':abs(float(journal_allocated_swap_fee_cost or 0.0)),
        'swap_fee_allocation_gap':abs(swap)-abs(float(journal_allocated_swap_fee_cost or 0.0)),
        'actual_funding_pnl':(_finite(actual_funding_pnl) if actual_funding_pnl is not None else None),
        'journal_funding_observed':(_finite(journal_funding_observed) if journal_funding_observed is not None else None),
        'explained_subtotal':explained,'unexplained_residual':account-explained,
        'unreconciled_account_gap':account-explained,
        'note':'actual funding and fee-allocation diagnostics are informational because okx_net_pnl may already include them; they are not added again',
    }


def build(user_dir,capital_summary,open_unrealized_pnl,fee_state,funding_summary=None):
    capital_summary=capital_summary or {}
    start=capital_summary.get('adjustment_start_ms')
    account=capital_summary.get('cashflow_adjusted_profit')
    if start is None or account is None:
        return {'complete':False,'adjustment_start_ms':start}
    realized=realized_window(user_dir,start)
    fees=fee_window(fee_state,start)
    return compose(
        account_adjusted_pnl=account,
        closed_realized_net=realized['closed']['net_pnl'],
        realized_with_reduces_net=realized['with_reduces']['net_pnl'],
        open_unrealized_pnl=open_unrealized_pnl,
        spot_fee_cost=fees['spot_fee_cost'],swap_fee_cost=fees['swap_fee_cost'],
        journal_allocated_swap_fee_cost=realized['journal_allocated_swap_fee_cost'],
        actual_funding_pnl=((funding_summary or {}).get('funding_pnl_usdt') if (funding_summary or {}).get('complete') else None),
        journal_funding_observed=realized['journal_funding_observed'],
        boundary_excluded_net=capital_summary.get('boundary_excluded_net_usdt') or 0.0,
        adjustment_start_ms=start,
    )
