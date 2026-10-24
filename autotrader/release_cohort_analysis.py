"""Read-only post-release cohort accounting."""
from __future__ import annotations
import datetime as dt
import json
from pathlib import Path

KST=dt.timezone(dt.timedelta(hours=9))

def _time(value):
    if isinstance(value, dt.datetime):
        x=value
    elif isinstance(value,(int,float)):
        x=dt.datetime.fromtimestamp(float(value),tz=dt.timezone.utc)
    elif value:
        try: x=dt.datetime.fromisoformat(str(value).replace("Z","+00:00"))
        except (TypeError,ValueError): return None
    else:
        return None
    if x.tzinfo is None: x=x.replace(tzinfo=KST)
    return x.astimezone(KST)

def _pf(values):
    wins=sum(v for v in values if v>0)
    losses=sum(v for v in values if v<0)
    if losses < 0: return wins/abs(losses)
    return None

def summarize_release_cohort(rows, *, release_at, ai_cost_usd=0.0, server_monthly_usd=0.0, now=None):
    start=_time(release_at)
    now=_time(now) if now is not None else dt.datetime.now(KST)
    selected=[]
    for row in rows or []:
        exit_at=_time(row.get("exit_time"))
        if start is not None and exit_at is not None and exit_at >= start:
            selected.append(row)
    nets=[]
    fees=0.0
    for row in selected:
        try: nets.append(float(row.get("net_pnl") or 0.0))
        except (TypeError,ValueError): nets.append(0.0)
        try: fees += float(row.get("fee") or 0.0)
        except (TypeError,ValueError): pass
    trading_net=sum(nets)
    elapsed_hours=max(0.0,(now-start).total_seconds()/3600.0) if start is not None and now is not None else 0.0
    server_prorated=(float(server_monthly_usd or 0.0)/730.0)*elapsed_hours
    ai=float(ai_cost_usd or 0.0)
    return {
        "release_at": start.isoformat(timespec="seconds") if start else None,
        "generated_at": now.isoformat(timespec="seconds") if now else None,
        "trade_count": len(selected),
        "trading_net_pnl": trading_net,
        "fees": fees,
        "profit_factor": _pf(nets),
        "win_rate": (sum(v>0 for v in nets)/len(nets)*100.0) if nets else None,
        "ai_cost_usd": ai,
        "server_prorated_cost_usd": server_prorated,
        "economic_net_after_operating_costs": trading_net-ai-server_prorated,
        "symbols": sorted({str(r.get("symbol")) for r in selected if r.get("symbol")}),
    }


def ai_cost_since(user_dir, release_at, *, now=None):
    start=_time(release_at)
    end=_time(now) if now is not None else dt.datetime.now(KST)
    if start is None:
        return 0.0
    path=Path(user_dir)/"token_usage.jsonl"
    if not path.is_file():
        return 0.0
    total=0.0
    with path.open(encoding="utf-8") as f:
        for line in f:
            try:
                row=json.loads(line)
            except (json.JSONDecodeError, TypeError):
                continue
            stamp=_time(row.get("time"))
            if stamp is None or stamp < start or (end is not None and stamp > end):
                continue
            try:
                total += float(row.get("cost_usd") or 0.0)
            except (TypeError, ValueError):
                continue
    return total
