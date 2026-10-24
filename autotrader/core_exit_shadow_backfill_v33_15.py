"""v33-15 bounded 5m UTC close backfill for proven CORE AI exits.

READ ONLY exchange OHLCV. Only observational exit_reentry_shadow.jsonl is
appended; source trades, orders, account settings and protection untouched.
"""
from __future__ import annotations
from datetime import datetime,timedelta,timezone
from pathlib import Path
import math

import exit_reentry_shadow

KST=timezone(timedelta(hours=9))
UTC=timezone.utc
HORIZONS=(15,30,60,120)
BAR=timedelta(minutes=5)
MAX_AGE=timedelta(days=7)
MAX_REQUESTS=2
MAX_BARS=38

def _when(raw):
    try:
        d=datetime.fromisoformat(str(raw))
        return d.replace(tzinfo=KST).astimezone(UTC) if d.tzinfo is None else d.astimezone(UTC)
    except (TypeError,ValueError,OverflowError):
        return None

def _num(value):
    if isinstance(value,bool):return None
    try:
        x=float(value)
        return x if math.isfinite(x) and x>0 else None
    except (TypeError,ValueError,OverflowError):return None

def pending_proven_exits(user_dir,now=None,max_items=MAX_REQUESTS):
    now=now or datetime.now(UTC)
    out=[]
    for row in exit_reentry_shadow.recent(str(user_dir),120):
        if (row.get('exit_price_basis')!='verified_order_fills'
            or _num(row.get('exit_price')) is None or
            row.get('side') not in ('long','short')):
            continue
        t=_when(row.get('exit_time'))
        if t is None or t>now or now-t>MAX_AGE:continue
        horizons=row.get('horizons') or {}
        # Fetch only when a previously missing horizon is actually due.
        # A closed 5m observation is expected within 10min of a horizon.
        due=[h for h in HORIZONS if horizons.get(str(h)) is None
             and now>=t+timedelta(minutes=h+6)]
        if due:
            out.append(dict(exit_id=row['exit_id'],symbol=row['symbol'],
                            exit_time=t,missing_due=due))
    out.sort(key=lambda item:item['exit_time'])
    return out[:max_items]

def normalize_confirmed_5m(raw,exit_time,now):
    """Output verified complete OHLCV candles tagged with *UTC close time*.

    Reject insufficient/gapped history: we must not label a 90-minute-late
    observation as the 15-minute post-exit price.
    """
    parsed=[]
    cutoff=now-timedelta(seconds=3)
    for entry in raw or []:
        if not isinstance(entry,(list,tuple)) or len(entry)<5:continue
        try:
            ms=int(entry[0])
            opened=datetime.fromtimestamp(ms/1000,UTC)
            ended=opened+BAR
            close,high,low=map(float,(entry[4],entry[2],entry[3]))
        except (ValueError,TypeError,OverflowError,OSError):continue
        if any(not math.isfinite(v) or v<=0 for v in (close,high,low)):
            continue
        if not (low<=close<=high):continue
        # Full post-exit 5m candle only: its high/low cannot contain
        # pre-exit excursions and fake a protective SL/TP barrier hit.
        if not (exit_time<=opened and ended<=exit_time+timedelta(minutes=125)):continue
        if ended>cutoff:continue
        parsed.append(dict(time=ended,close=close,high=high,low=low))
    parsed.sort(key=lambda x:x['time'])
    if not parsed:return [],'no_confirmed_candles'
    unique=set()
    for item in parsed:
        if item['time'] in unique:return [],'duplicate_candle'
        unique.add(item['time'])
    if parsed[0]['time']>exit_time+timedelta(minutes=10):
        return [],'missing_start_candles'
    for older,newer in zip(parsed,parsed[1:]):
        if newer['time']-older['time']!=BAR:return [],'gap_in_confirmed_candles'
    return parsed,None

def sanitize_pre_exit_overlap(user_dir,*,now=None):
    """One-time observational correction for historical overlapping 5m bars.

    Rebuild path-derived MFE/MAE/SL/TP flags from *complete* post-exit candles.
    Never changes source trades, exit fills, positions, original SL/TP or AI.
    """
    import json
    now=now or datetime.now(UTC)
    corrected=0
    for row in exit_reentry_shadow.recent(str(user_dir),120):
        if row.get('exit_price_basis')!='verified_order_fills':continue
        exit_time=_when(row.get('exit_time'))
        if exit_time is None or exit_time>now or now-exit_time>MAX_AGE:continue
        path=row.get('counterfactual_path') or []
        if not path:continue
        complete=[p for p in path if (
            _when(p.get('time')) is not None and
            _when(p['time'])-BAR>=exit_time)]
        if len(complete)==len(path):continue
        if row.get('analytical_outcome') not in (None,'unresolved'):
            continue  # Do not rewrite an already-attributed outcome.
        repaired=json.loads(json.dumps(row))
        repaired['counterfactual_path']=complete
        repaired.update(original_sl_crossed=False,original_tp_crossed=False,
                        max_favorable_excursion=0.,max_adverse_excursion=0.)
        for p in complete:
            try:
                stop,target,fav,adv=exit_reentry_shadow._crossings_and_excursions(
                    repaired,float(p['close']),float(p['high']),float(p['low']))
            except (KeyError,ValueError,TypeError):
                break
            repaired.update(
                original_sl_crossed=stop,original_tp_crossed=target,
                max_favorable_excursion=max(repaired['max_favorable_excursion'],fav),
                max_adverse_excursion=max(repaired['max_adverse_excursion'],adv))
        else:
            horizons=repaired.get('horizons') or {}
            for h,val in horizons.items():
                if isinstance(val,dict) and _when(val.get('time')) is not None:
                    if _when(val['time'])-BAR<exit_time:
                        horizons[h]=None
            repaired['updated_at']=datetime.now(UTC).isoformat(timespec='seconds')
            repaired['candle_provenance_correction']='v33_15_complete_post_exit_5m_only'
            exit_reentry_shadow._append(str(user_dir),repaired)
            corrected+=1
    return corrected


def backfill(user_dir,*,client_factory=None,now=None,max_queries=MAX_REQUESTS):
    now=now or datetime.now(UTC)
    if now.tzinfo is None:raise ValueError('now_must_be_timezone_aware')
    now=now.astimezone(UTC)
    limit=min(MAX_REQUESTS,max(0,int(max_queries)))
    corrected=sanitize_pre_exit_overlap(user_dir,now=now)
    eligible=pending_proven_exits(user_dir,now=now,max_items=limit)
    if client_factory is None:
        import config,okx_client
        cfg=config.UserConfig(str(user_dir))
        client_factory=lambda symbol:okx_client.OkxClient(symbol,cfg)
    clients={}
    result=dict(status='KNOWN',read_only_ohlcv_queries=0,
        targeted_exits=len(eligible),changed_shadow_versions=0,
        corrected_overlap_exits=corrected,
        exits=[],max_exchange_queries=MAX_REQUESTS)
    for target in eligible:
        symbol=target['symbol']
        report=dict(symbol=symbol,exit_id=target['exit_id'][:14],
                    missing_due=target['missing_due'])
        try:
            client=clients.setdefault(symbol,client_factory(symbol))
            since=int((target['exit_time']-BAR).timestamp()*1000)
            # Explicitly GET historical prices. No create/cancel/amend/order action.
            raw=client.exchange.fetch_ohlcv(symbol,'5m',since=since,limit=MAX_BARS)
            result['read_only_ohlcv_queries']+=1
            candles,reason=normalize_confirmed_5m(raw,target['exit_time'],now)
            if reason:
                report.update(status='UNVERIFIED',reason=reason)
            else:
                changed=0
                for item in candles:
                    changed+=len(exit_reentry_shadow.observe_confirmed_market(
                        str(user_dir),symbol,item['time'],
                        item['close'],item['high'],item['low'],
                        require_complete_post_exit_candle=True))
                report.update(status='RECORDED',bars=len(candles),changed=changed,
                              latest_confirmed_candle=candles[-1]['time'].isoformat())
                result['changed_shadow_versions']+=changed
        except Exception as exc:
            report.update(status='UNVERIFIED',reason=type(exc).__name__)
        result['exits'].append(report)
    return result
