"""Lifecycle counts are close-only; monetary results include every real realization."""
import datetime
import math
from collections import defaultdict

_KST = datetime.timezone(datetime.timedelta(hours=9))

def kst_key(value):
    if not isinstance(value,str) or not value.strip():
        return None
    try:
        d=datetime.datetime.fromisoformat(value.replace("Z","+00:00"))
        if d.tzinfo is not None:
            d=d.astimezone(_KST)
        return d.date().isoformat()
    except ValueError:
        return None

def merge_close_periods(close_periods,realized_events):
    """Preserve closed lifecycle counts/win rates, overlay reduce-inclusive money.
    A day with only partial exits is included with count=0 (NOT a completed trade).
    Unknown/non-finite numeric journal values fail closed rather than invent PnL.
    """
    buckets={view:defaultdict(lambda:{"pnl":0.0,"fee":0.0,"net":0.0,"events":0,"reduces":0})
             for view in ("daily","monthly","yearly")}
    for r in realized_events:
        if r.get("type") not in ("reduce","close"):
            continue
        date=kst_key(r.get("time"))
        if date is None:
            continue
        pnl=r.get("pnl")
        fee=r.get("fee",0)
        net=r.get("okx_net_pnl")
        try:
            pnl=float(pnl)
            fee=float(fee or 0)
            net=float(net) if net is not None else pnl-fee
        except (ValueError,TypeError):
            continue
        if not all(map(math.isfinite,(pnl,fee,net))):
            continue
        for view,key in (("daily",date),("monthly",date[:7]),("yearly",date[:4])):
            b=buckets[view][key]
            b["pnl"]+=pnl
            b["fee"]+=fee
            b["net"]+=net
            b["events"]+=1
            b["reduces"]+=int(r.get("type")=="reduce")
    result={}
    for view in buckets:
        rows={r["period"]:dict(r) for r in close_periods.get(view,[])}
        for key,b in buckets[view].items():
            row=rows.setdefault(key,{"period":key,"count":0,"win_rate":None})
            row.update(total_pnl=b["pnl"],total_fee=b["fee"],net_pnl=b["net"],
                       realized_event_count=b["events"],reduce_event_count=b["reduces"])
        result[view]=[rows[k] for k in sorted(rows,reverse=True)]
    return result
