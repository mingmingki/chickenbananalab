"""v33-76: fee-aware economic preflight for non-defensive CORE AI profit reductions.

Applies ONLY when the held-position AI still says thesis_intact and the AI
requested a discretionary REDUCE. Does not block weakening/invalidated risk
reductions, protective exchange SL/TP, or independently guarded MFE reductions.
Uses the current exchange price and already-existing cost estimate. Orders,
position state and OCO modification remain owned by trader.py.
"""
from __future__ import annotations
import math

REASON = "discretionary_reduce_below_roundtrip_cost"

def _float(v):
    if isinstance(v,bool):
        return None
    try:
        n=float(v)
    except (ValueError,TypeError,OverflowError):
        return None
    return n if math.isfinite(n) else None

def assess(*, path, gemini_assessment, side, entry_price, current_price,
           reduce_contracts, contract_size, roundtrip_cost_pct):
    """Return whether a discretionary reduction has positive projected net PnL.

    One filled contract represents contract_size coins. Costs are estimated
    roundtrip costs (including the historical entry fee), not a realized
    exchange receipt; positive estimate is NOT a guarantee.
    """
    if path != 'ai_strategy' or gemini_assessment != 'thesis_intact':
        return dict(allowed=True,applies=False,reason='risk_defense_or_independent_policy')
    if side not in ('long','short'):
        return dict(allowed=False,applies=True,reason='unknown_discretionary_side')
    numbers=[_float(v) for v in (entry_price,current_price,reduce_contracts,
                                 contract_size,roundtrip_cost_pct)]
    if any(v is None for v in numbers):
        return dict(allowed=False,applies=True,reason='discretionary_cost_inputs_missing')
    entry,price,contracts,multiplier,cost_pct=numbers
    if min(entry,price,contracts,multiplier)<=0 or not 0<=cost_pct<=5:
        return dict(allowed=False,applies=True,reason='discretionary_cost_inputs_invalid')
    sign=1 if side=='long' else -1
    gross_usdt=sign*(price-entry)*contracts*multiplier
    estimated_cost_usdt=price*contracts*multiplier*cost_pct/100
    net_usdt=gross_usdt-estimated_cost_usdt
    return dict(allowed=net_usdt>0,applies=True,
                reason='fee_positive_discretionary_reduce' if net_usdt>0 else REASON,
                projected_gross_usdt=round(gross_usdt,8),
                estimated_roundtrip_cost_usdt=round(estimated_cost_usdt,8),
                projected_net_after_cost_usdt=round(net_usdt,8),
                current_move_pct=round(sign*(price-entry)/entry*100,6),
                roundtrip_cost_pct=cost_pct)
