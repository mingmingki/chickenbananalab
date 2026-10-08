from __future__ import annotations

import math


def _pf(values):
    wins=sum(v for v in values if v>0)
    losses=sum(v for v in values if v<0)
    if losses==0:
        return None if wins==0 else math.inf
    return wins/abs(losses)


def _quantile(values, q):
    if not values: return None
    vals=sorted(values)
    if len(vals)==1: return vals[0]
    pos=(len(vals)-1)*q
    lo=int(math.floor(pos)); hi=int(math.ceil(pos))
    if lo==hi: return vals[lo]
    return vals[lo]+(vals[hi]-vals[lo])*(pos-lo)


def _side(rows, name):
    values=[float(getattr(r,f"{name}_net")) for r in rows if getattr(r,f"{name}_net") is not None]
    fees=sum(float(getattr(r,f"{name}_fee",0.0) or 0.0) for r in rows)
    actions=sum(int(getattr(r,f"{name}_actions",0) or 0) for r in rows)
    losses=[v for v in values if v<0]
    return {
        "net":sum(values),"profit_factor":_pf(values),
        "max_loss":min(losses) if losses else 0.0,
        "loss_p05":_quantile(losses,0.05) if losses else 0.0,
        "fee":fees,"action_count":actions,
        "fee_to_abs_net":fees/max(abs(sum(values)),1e-12),
    }


def compare_policies(results) -> dict:
    rows=[r for r in results if getattr(r,"resolved",False)]
    legacy=_side(rows,"legacy"); adaptive=_side(rows,"adaptive")
    risk=sum(1 for r in rows if getattr(r,"risk_violation",False))
    loosen=sum(1 for r in rows if getattr(r,"stop_loosen_violation",False))
    unresolved=sum(1 for r in results if not getattr(r,"resolved",False))
    windows={}
    for r in rows:
        windows.setdefault(getattr(r,"window_id",None),[0.0,0.0])
        windows[getattr(r,"window_id",None)][0]+=float(getattr(r,"legacy_net",0.0) or 0.0)
        windows[getattr(r,"window_id",None)][1]+=float(getattr(r,"adaptive_net",0.0) or 0.0)
    stable=all(a >= l for l,a in windows.values()) if windows else False
    acceptable=bool(
        rows and risk==0 and loosen==0
        and adaptive["max_loss"] >= legacy["max_loss"]
        and adaptive["action_count"] <= legacy["action_count"]
        and adaptive["net"] >= legacy["net"]
        and stable
    )
    return {
        "resolved_count":len(rows),"unresolved_count":unresolved,
        "legacy":legacy,"adaptive":adaptive,
        "risk_violations":risk,"stop_loosen_violations":loosen,
        "window_stability":stable,"acceptable":acceptable,
    }
