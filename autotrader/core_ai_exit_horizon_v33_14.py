"""v33-14 post-AI-exit horizon evidence; read-only and proof-gated.

Reports price movement *after* an exit, not a hypothetical realized profit.
Never calls an exchange, never assumes an SL would not have executed.
"""
from datetime import datetime,timedelta,timezone
from pathlib import Path
from collections import Counter
import json,math,statistics

KST=timezone(timedelta(hours=9))
HORIZONS=(15,30,60,120)
MAX_OBSERVATION_LAG_MIN=15

def finite(value,positive=False):
    if value is None or isinstance(value,bool):return None
    try:
        value=float(value)
        return value if math.isfinite(value) and (not positive or value>0) else None
    except (TypeError,ValueError,OverflowError):return None

def parse_time(value):
    try:
        d=datetime.fromisoformat(str(value))
        return d.replace(tzinfo=KST) if d.tzinfo is None else d.astimezone(KST)
    except (TypeError,ValueError,OverflowError):
        return None

def analyze(rows,*,now=None,days=7):
    now=now or datetime.now(KST)
    if now.tzinfo is None:now=now.replace(tzinfo=KST)
    start=now-timedelta(days=days)
    latest={}
    for x in rows:
        if isinstance(x,dict) and x.get('exit_id'):
            latest[x['exit_id']]=x
    recent=[x for x in latest.values()
            if parse_time(x.get('exit_time')) is not None and
            start<=parse_time(x['exit_time'])<=now]
    counts=Counter()
    values={h:[] for h in HORIZONS}
    invalid_lag=Counter()
    for x in recent:
        side=x.get('side')
        price=finite(x.get('exit_price'),positive=True)
        proof=x.get('exit_price_basis')=='verified_order_fills'
        if side not in ('short','long') or price is None:
            counts['exit_price_unusable']+=1;continue
        if not proof:
            counts['exit_price_unproven']+=1;continue
        counts['exact_fill_price_verified']+=1
        time=parse_time(x['exit_time'])
        for h in HORIZONS:
            observed=(x.get('horizons') or {}).get(str(h))
            if not isinstance(observed,dict):continue
            obs_time=parse_time(observed.get('time'))
            close=finite(observed.get('close'),positive=True)
            if obs_time is None or close is None:continue
            elapsed=(obs_time-time).total_seconds()/60
            if elapsed < h or elapsed>h+MAX_OBSERVATION_LAG_MIN:
                invalid_lag[h]+=1;continue
            signed=(close-price)/price*10000*(1 if side=='long' else -1)
            values[h].append(signed)
    return dict(status='KNOWN',as_of=now.isoformat(),window_days=days,
        shadow_ai_exit_records=len(recent),provenance_counts=dict(counts),
        horizon_price_movement={
            str(h):dict(proven_samples=len(values[h]),
                avg_signed_after_exit_bps=round(statistics.mean(values[h]),3) if values[h] else None,
                median_signed_after_exit_bps=round(statistics.median(values[h]),3) if values[h] else None,
                favorable_price_after_exit=sum(x>0 for x in values[h]),
                adverse_price_after_exit=sum(x<0 for x in values[h]),
                observation_lag_rejected=invalid_lag[h]) for h in HORIZONS},
        interpretation='Movement after proven exchange-fill exit, not achievable held-position PnL; ignores contingent SL/TP, fees, funding, liquidity and decision-time alternative.')

def from_user_dir(user_dir,*,now=None,days=7):
    path=Path(user_dir)/'exit_reentry_shadow.jsonl'
    try:
        rows=[]
        with path.open(encoding='utf-8') as f:
            for l in f:
                try:row=json.loads(l)
                except (ValueError,TypeError):continue
                if isinstance(row,dict):rows.append(row)
        return analyze(rows,now=now,days=days)
    except (OSError,TypeError,ValueError) as exc:
        return dict(status='UNKNOWN',reason=type(exc).__name__)
