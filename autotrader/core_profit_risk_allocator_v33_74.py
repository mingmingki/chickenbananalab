"""v33-74: rolling, evidence-gated CORE sizing to reduce persistent negative edge.

No order creation, no pause, no change to stops/targets, approval authority or
position exits. Profit factor is an event-level cautionary proxy, not a forecast.
Only *reduces* already approved risk for persistent losers in two independent
trailing 4-day windows, otherwise preserves the approved budget.
"""
import datetime as dt
import math
import threading
import time
from collections import defaultdict

KST = dt.timezone(dt.timedelta(hours=9))
SCALE_WHEN_UNPROFITABLE = 0.50
MIN_EVENTS_PER_WINDOW = 10
MAX_PF = 0.75
WINDOW_DAYS = 4
_cache = {}
_cache_lock = threading.RLock()

def _summary(values):
    wins = sum(v for v in values if v > 0)
    losses = sum(v for v in values if v < 0)
    pf = wins / abs(losses) if losses < 0 else None
    return dict(n=len(values),net=round(sum(values),5),
                pf=round(pf,5) if pf is not None else None)

def decide(rows, symbol, *, now):
    if symbol not in ('BTC/USDT:USDT','ETH/USDT:USDT','XRP/USDT:USDT','ADA/USDT:USDT'):
        return dict(factor=1.0, reason='not_core_symbol')
    if now.tzinfo is None or now.utcoffset() is None:
        return dict(factor=1.0,reason='timezone_unavailable')
    cut2=now.astimezone(KST)
    cut1=cut2-dt.timedelta(days=WINDOW_DAYS)
    cut0=cut1-dt.timedelta(days=WINDOW_DAYS)
    chunks=([],[])
    for item in rows:
        if item.get('_group')!='core' or item.get('symbol') != symbol or item.get('dry_run'):
            continue
        if item.get('type') not in ('close','reduce'):
            continue
        try:
            stamp=dt.datetime.fromisoformat(str(item.get('time'))).replace(tzinfo=KST)
        except (ValueError,TypeError,OverflowError):
            continue
        if stamp >= cut2 or stamp < cut0:
            continue
        # The central reconciliation supplies a booked numeric net before use.
        value=item.get('_profitability_net')
        if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value):
            continue
        chunks[0 if stamp<cut1 else 1].append(float(value))
    periods=[_summary(chunk) for chunk in chunks]
    sustained=all(p['n']>=MIN_EVENTS_PER_WINDOW and p['net']<0
                  and p['pf'] is not None and p['pf']<MAX_PF for p in periods)
    return dict(factor=SCALE_WHEN_UNPROFITABLE if sustained else 1.0,
                reason='two_window_negative_edge' if sustained else 'normal_budget',
                prior=periods[0],recent=periods[1],
                min_events=MIN_EVENTS_PER_WINDOW,
                window_days=WINDOW_DAYS,
                evidence='booked_core_close_and_reduce_events')

def decision_from_live(user_dir, symbol, *, now=None):
    """At most one journal read per account per five minutes; no writes."""
    moment=time.time() if now is None else now.timestamp()
    now_dt=dt.datetime.fromtimestamp(moment,KST)
    with _cache_lock:
        cache=_cache.get(user_dir)
        if cache and 0<=moment-cache[0]<300:
            rows=cache[1]
        else:
            import pnl_reconciliation
            rows=pnl_reconciliation.load_all_records_with_reduces(user_dir)
            if rows is None or not isinstance(rows,list):
                raise ValueError('invalid_reconciliation_source')
            for row in rows:
                if row.get('_group')=='core':
                    v=pnl_reconciliation._trade_net_pnl(row)
                    if isinstance(v,(float,int)) and not isinstance(v,bool) and math.isfinite(v):
                        row['_profitability_net']=float(v)
            _cache[user_dir]=(moment,rows)
    return decide(rows,symbol,now=now_dt)
