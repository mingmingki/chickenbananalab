"""v33-23 observability-only hold-risk shadow.

No orders, no protective modification, no AI calls, no strategy authority.
Log deduplicated evidence per closed 5m bar and exact position identity.
"""
from __future__ import annotations
import datetime
import json
import math
import threading
from pathlib import Path

_lock=threading.Lock()
_seen=set()

def _finite(value):
    if isinstance(value,bool):return None
    try:
        number=float(value)
        return number if math.isfinite(number) else None
    except (TypeError,ValueError,OverflowError):
        return None

def assess(position, opening, closed_5m_rows, *, now=None):
    """Decision support only; never returns a trade instruction."""
    now=now or datetime.datetime.now(datetime.timezone.utc)
    side=(position or {}).get('side')
    entry=_finite((position or {}).get('entry_price'))
    mark=_finite((position or {}).get('mark_price'))
    stop=_finite((opening or {}).get('sl_price'))
    identity=((position or {}).get('entry_timestamp_ms')
              or (opening or {}).get('time'))
    base=dict(schema_version=1,mode='SHADOW_ONLY',symbol=None,
              side=side,identity=str(identity) if identity else None,
              entry_price=entry,observed_mark=mark,original_stop=stop,
              loss_r=None,closed_5m_ts=None,valid=False,
              signal=False,reason='insufficient_evidence')
    if side not in ('long','short') or None in (entry,mark,stop):
        return base
    sign=1 if side=='long' else -1
    if entry<=0 or mark<=0 or abs(entry-stop)<=0 or sign*(entry-stop)<=0:
        return dict(base,reason='invalid_original_stop')
    loss_r=sign*(entry-mark)/abs(entry-stop)
    base['loss_r']=round(loss_r,6)
    if len(closed_5m_rows or [])<3:return dict(base,reason='closed_5m_indicators_missing')
    rows=closed_5m_rows[-3:]
    for row in rows:
        if not isinstance(row,dict):
            return dict(base,reason='malformed_indicators')
        if any(_finite(row.get(key)) is None for key in ('close','ema_20','macd','rsi_14')):
            return dict(base,reason='malformed_indicators')
    bar_ts=rows[-1].get('ts')
    base['closed_5m_ts']=bar_ts
    try:
        latest=datetime.datetime.fromisoformat(str(bar_ts))
        if latest.tzinfo is None:latest=latest.replace(tzinfo=datetime.timezone.utc)
        age=(now-latest).total_seconds()
        if age < 0 or age>1200:
            return dict(base,reason='bar_stale_or_future')
    except (ValueError,TypeError):
        return dict(base,reason='invalid_bar_timestamp')
    adverse_ema_two=all(sign*(row['close']-row['ema_20'])<0 for row in rows[-2:])
    adverse_macd=sign*(rows[-1]['macd']-rows[-2]['macd'])<0
    adverse_rsi=sign*(rows[-1]['rsi_14']-50)<0
    signal=bool(loss_r>=.35 and adverse_ema_two and adverse_macd)
    return dict(base,valid=True,reason='candidate' if signal else 'no_candidate',
                signal=signal,closed_5m_ts=str(bar_ts),
                adverse_ema_two=adverse_ema_two,
                adverse_macd=adverse_macd,adverse_rsi=adverse_rsi,
                threshold_r=0.35,
                # A candidate is an observation, NEVER an execution approval.
                may_execute=False)

def record(user_dir,symbol,assessment,*,when=None):
    """Append one research observation per held-position/closed-bar identity."""
    if not assessment.get('valid') or not assessment.get('identity'):
        return False
    bar=assessment.get('closed_5m_ts')
    if not bar:return False
    key=(str(Path(user_dir).resolve()),symbol,assessment['identity'],bar)
    with _lock:
        if key in _seen:return False
        _seen.add(key)
        record=dict(assessment)
        record['symbol']=symbol
        record['observed_at']=(when or datetime.datetime.now()).isoformat(timespec='seconds')
        path=Path(user_dir)/'core_exit_hold_shadow_v33_23.jsonl'
        try:
            with path.open('a',encoding='utf-8') as f:
                f.write(json.dumps(record,ensure_ascii=False,allow_nan=False)+'\n')
        except Exception:
            _seen.discard(key)
            raise
    return True
