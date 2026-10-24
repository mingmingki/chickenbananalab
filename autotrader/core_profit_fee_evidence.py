"""Observed net-fee evidence for CORE protective stop floor. Read-only.

No claim that an exchange stop guarantees an execution at the stop price.
The cost uplift may tighten a stop ONLY if favorable market geometry permits.
"""
import json,math
from pathlib import Path


def numeric(value):
    if isinstance(value,bool):return None
    try:
        v=float(value)
        return v if math.isfinite(v) and v>0 else None
    except (ValueError,TypeError,OverflowError):
        return None


def cost_pct_from_proven_stops(rows,symbol,contract_size,*,minimum_samples=3,
                               base_fee_pct=0.10):
    size=numeric(contract_size)
    if not size:return dict(confirmed=False,fee_pct=base_fee_pct,count=0,reason='unknown_contract_size')
    rates=[]
    for r in rows:
        if (not isinstance(r,dict) or r.get('symbol')!=symbol or r.get('type')!='close'
             or r.get('reason')!='stop_loss' or r.get('dry_run') or
             r.get('pnl_source')!='okx_realized'):
            continue
        e=r.get('close_execution_evidence') or {}
        if e.get('price_basis')!='verified_owned_protection_fills':
            continue
        fee=numeric(r.get('fee'));amount=numeric(r.get('amount'));price=numeric(r.get('close_price'))
        if not fee or not amount or not price:continue
        rate=fee/(amount*size*price)*100
        if math.isfinite(rate) and .01<=rate<=.3:
            rates.append(rate)
    if len(rates)<minimum_samples:
        return dict(confirmed=False,fee_pct=base_fee_pct,count=len(rates),reason='insufficient_proven_stop_cost')
    rates=sorted(rates[-32:])
    # Small sample: use maximum documented cost rather than relying on a
    # normal approximation. 0.01% cushion is for observed execution variation;
    # additional stop slippage may still occur.
    observed=max(rates)
    estimated=max(base_fee_pct,min(.25,observed+.01))
    return dict(confirmed=True,fee_pct=estimated,observed_pct=observed,
                count=len(rates),reason='proven_owned_stop_history')


def select_market_feasible_fee_pct(side,entry,mark,base_pct,recommended_pct):
    """Never drop the original stop-floor protection due to higher cost evidence."""
    try:
        entry=float(entry);mark=float(mark)
        base_pct=float(base_pct);high_pct=float(recommended_pct)
        if (side not in ('long','short') or
            not all(math.isfinite(x) and x>0 for x in (entry,mark)) or
            not math.isfinite(base_pct) or not math.isfinite(high_pct) or
            base_pct<0 or high_pct<base_pct):
            return base_pct,'invalid_or_no_uplift'
        enhanced_floor=entry*(1+(1 if side=='long' else -1)*high_pct/100)
        if (mark>enhanced_floor if side=='long' else mark<enhanced_floor):
            return high_pct,'higher_cost_floor_feasible'
        return base_pct,'higher_cost_floor_not_yet_feasible'
    except (TypeError,ValueError,OverflowError):
        return base_pct,'unavailable'


def from_user_dir(user_dir,symbol,contract_size,*,base_fee_pct=.1):
    try:
        p=Path(user_dir)/'trades_log.jsonl'
        # Existing journal is append-only. Conservative fallback on any parse
        # uncertainty; never adjust the order on inferred fees.
        records=[json.loads(s) for s in p.read_text().splitlines() if s.strip()]
        return cost_pct_from_proven_stops(records,symbol,contract_size,
                                          base_fee_pct=base_fee_pct)
    except (OSError,ValueError,TypeError):
        return dict(confirmed=False,fee_pct=base_fee_pct,count=0,reason='journal_unavailable')
