"""Read-only Candidate C breakout-failure shadow analysis."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import os

import jsonl_cache

JOURNAL = 'candidate_c_breakout_shadow.jsonl'
SETUP_PROVENANCE = 'candidate_c_breakout_setup_provenance.jsonl'


def _path(user_dir):
    return os.path.join(user_dir, JOURNAL)


def _value(obj, name, default=None):
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _read(user_dir):
    path = _path(user_dir)
    if not os.path.exists(path):
        return []
    rows=[]
    with open(path, encoding='utf-8') as f:
        for line in f:
            try: row=json.loads(line)
            except json.JSONDecodeError: continue
            if isinstance(row,dict) and row.get('sample_id'): rows.append(row)
    return rows


def _latest(user_dir):
    latest={}
    for row in _read(user_dir): latest[row['sample_id']]=row
    return latest


def _append(user_dir,row):
    path=_path(user_dir); os.makedirs(user_dir,exist_ok=True)
    with jsonl_cache.get_path_lock(path):
        with open(path,'a',encoding='utf-8') as f:
            f.write(json.dumps(row,ensure_ascii=False,allow_nan=False)+'\n')
            f.flush(); os.fsync(f.fileno())


def _sample_id(symbol, epoch):
    raw=f"{symbol}|{_value(epoch,'entry_intent_id')}|{_value(epoch,'setup_id')}".encode()
    return hashlib.sha256(raw).hexdigest()[:24]


def _valid_setup(symbol, epoch, setup_record):
    if not setup_record or not bool(setup_record.get('provenance_exact')):
        return False,'missing_exact_setup_provenance'
    if (setup_record.get('setup_id') != _value(epoch,'setup_id')
            or setup_record.get('symbol') != symbol
            or setup_record.get('side') != _value(epoch,'side')):
        return False,'setup_identity_mismatch'
    try: ref=float(setup_record.get('breakout_reference'))
    except (TypeError,ValueError): return False,'missing_exact_setup_provenance'
    if not math.isfinite(ref) or ref <= 0: return False,'missing_exact_setup_provenance'
    return True,None


def _monitor_metrics(side, entry_price, breakout_reference, entry_time_ms, monitor_rows):
    rows=[r for r in (monitor_rows or []) if isinstance(r,dict)]
    if not rows:
        return None,None,None
    latest=rows[-1]
    current=latest.get('close', latest.get('latest_5m_close', latest.get('decision_price')))
    try: current=float(current)
    except (TypeError,ValueError): current=None
    distance=None
    if current is not None and breakout_reference:
        if side=='long': distance=(current/breakout_reference-1.0)*100.0
        elif side=='short': distance=(breakout_reference/current-1.0)*100.0
    elapsed=None
    try:
        stamp=float(latest.get('last_closed_bar_ms'))
        if entry_time_ms is not None: elapsed=max(0.0,(stamp-float(entry_time_ms))/60000.0)
    except (TypeError,ValueError): pass
    adverse=0.0
    if entry_price:
        for r in rows:
            try:
                high=float(r.get('high',r.get('latest_5m_high')))
                low=float(r.get('low',r.get('latest_5m_low')))
            except (TypeError,ValueError):
                continue
            if side=='long': adverse=max(adverse,(entry_price-low)/entry_price*100.0)
            elif side=='short': adverse=max(adverse,(high-entry_price)/entry_price*100.0)
    return distance,elapsed,max(0.0,adverse)


def analyze_open_position(user_dir, symbol, epoch, setup_record, monitor_rows):
    sid=_sample_id(symbol,epoch)
    valid,reason=_valid_setup(symbol,epoch,setup_record)
    base={
        'sample_id':sid,'symbol':symbol,'side':_value(epoch,'side'),
        'position_id':_value(epoch,'entry_intent_id'),'setup_id':_value(epoch,'setup_id'),
        'entry_time_ms':_value(epoch,'entry_time_ms'),'entry_price':_value(epoch,'raw_entry_price'),
        'derisk_done':bool(_value(epoch,'derisk_done',False)),
        'partial_take_profit_done':bool(_value(epoch,'partial_take_profit_done',False)),
        'live_action':None,'lifecycle_id':None,'lifecycle_net':None,
        'final_reason':None,'resolution_status':'open',
    }
    if not valid:
        row={**base,'status':'unresolved','breakout_reference':None,
             'unresolved_reason':reason,'distance_through_breakout_pct':None,
             'elapsed_minutes':None,'adverse_excursion_pct':None}
    else:
        ref=float(setup_record['breakout_reference'])
        distance,elapsed,adverse=_monitor_metrics(
            base['side'],float(base['entry_price']),ref,base['entry_time_ms'],monitor_rows)
        row={**base,'status':'observed','breakout_reference':ref,
             'breakout_provenance':setup_record.get('source'),'unresolved_reason':None,
             'distance_through_breakout_pct':distance,'elapsed_minutes':elapsed,
             'adverse_excursion_pct':adverse}
    row['updated_at']=dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')
    prior=_latest(user_dir).get(sid)
    comparable={k:v for k,v in row.items() if k!='updated_at'}
    prior_cmp={k:v for k,v in (prior or {}).items() if k!='updated_at'}
    if comparable!=prior_cmp: _append(user_dir,row)
    return row


def _life_entry_intent(life):
    for event in life.get('events') or []:
        if event.get('type')=='open' and event.get('execution_id'):
            return event.get('execution_id')
    return life.get('entry_intent_id') or life.get('position_id')


def resolve_completed(user_dir, lifecycles):
    updated=[]
    for sid,current in _latest(user_dir).items():
        if current.get('resolution_status')=='resolved':
            continue
        position_id=current.get('position_id')
        matches=[life for life in (lifecycles or [])
                 if life.get('symbol')==current.get('symbol')
                 and life.get('side')==current.get('side')
                 and _life_entry_intent(life)==position_id]
        if len(matches)!=1:
            continue
        life=matches[0]
        row=dict(current)
        row.update(
            lifecycle_id=life.get('trade_id'), lifecycle_net=life.get('lifecycle_net',life.get('net_pnl')),
            final_reason=life.get('final_close_reason'), resolution_status='resolved',
            resolved_at=dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds'),
            updated_at=dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds'),
        )
        _append(user_dir,row); updated.append(row)
    return updated


def recent(user_dir, limit=50):
    rows=list(_latest(user_dir).values())
    rows.sort(key=lambda r:(r.get('entry_time_ms') or 0),reverse=True)
    return rows[:max(0,int(limit))]


def summary(user_dir):
    rows=recent(user_dir,5000)
    observed=[r for r in rows if r.get('status')=='observed']
    resolved=[r for r in rows if r.get('resolution_status')=='resolved']
    lifecycle_net=sum(float(r.get('lifecycle_net') or 0.0) for r in resolved)
    failed=[r for r in observed if (r.get('distance_through_breakout_pct') or 0) < 0]
    return {
        'sample_count':len(rows),'observed_count':len(observed),'unresolved_count':len(rows)-len(observed),
        'resolved_count':len(resolved),'breakout_failed_count':len(failed),
        'derisk_done_count':sum(bool(r.get('derisk_done')) for r in rows),
        'lifecycle_net':lifecycle_net,'recent':rows[:20],
        'label':'관찰용 · 실주문 영향 없음',
    }


def _read_jsonl_path(path):
    rows=[]
    if not os.path.exists(path):
        return rows
    with open(path, encoding='utf-8') as f:
        for line in f:
            try: row=json.loads(line)
            except json.JSONDecodeError: continue
            if isinstance(row,dict): rows.append(row)
    return rows


def _setup_provenance_path(user_dir):
    return os.path.join(user_dir, SETUP_PROVENANCE)


def _latest_setup_provenance(user_dir):
    latest={}
    for row in _read_jsonl_path(_setup_provenance_path(user_dir)):
        if row.get('setup_id'):
            latest[row['setup_id']]=row
    return latest


def _capture_runtime_setup_provenance(user_dir, symbol, fields):
    attempt=(fields or {}).get('last_entry_attempt') or {}
    evaluation=(fields or {}).get('last_evaluation') or {}
    monitor=(fields or {}).get('monitor') or {}
    setup_id=attempt.get('setup_id')
    if not setup_id or not bool(attempt.get('executed')):
        return None
    if evaluation.get('setup_id') != setup_id:
        return None
    side=monitor.get('target_side')
    if side not in ('long','short') or not bool(monitor.get('setup_met')) or monitor.get('status') != 'OK':
        return None
    try: reference=float(monitor.get('target_price'))
    except (TypeError,ValueError): return None
    if not math.isfinite(reference) or reference <= 0:
        return None
    row={
        'setup_id':setup_id,'symbol':symbol,'side':side,
        'breakout_reference':reference,'provenance_exact':True,
        'source':'runtime_entry_monitor_same_cycle',
        'source_candle_close_timestamp':evaluation.get('source_candle_close_timestamp'),
        'captured_at':dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds'),
    }
    prior=_latest_setup_provenance(user_dir).get(setup_id)
    if prior and all(prior.get(k)==row.get(k) for k in ('setup_id','symbol','side','breakout_reference','source_candle_close_timestamp')):
        return prior
    path=_setup_provenance_path(user_dir)
    os.makedirs(user_dir,exist_ok=True)
    with jsonl_cache.get_path_lock(path):
        with open(path,'a',encoding='utf-8') as f:
            f.write(json.dumps(row,ensure_ascii=False,allow_nan=False)+'\n')
            f.flush(); os.fsync(f.fileno())
    return row


def _active_epochs(user_dir):
    path=os.path.join(user_dir,'candidate_c_epoch_store.jsonl')
    states={}
    for row in _read_jsonl_path(path):
        position_id=row.get('position_id')
        if not position_id:
            continue
        if row.get('event')=='discarded':
            states.pop(position_id,None)
            continue
        states[position_id]=row
    return states


def _active_epoch_for_runtime(user_dir, symbol, fields):
    actual=(fields or {}).get('actual_position') or {}
    exchange_position_id=actual.get('position_id') or (actual.get('info') or {}).get('posId')
    side=actual.get('side')
    if not exchange_position_id or side not in ('long','short'):
        return None
    matches=[row for row in _active_epochs(user_dir).values()
             if row.get('symbol')==symbol and row.get('side')==side
             and row.get('exchange_position_id')==exchange_position_id]
    return matches[0] if len(matches)==1 else None


def observe_runtime_publish(user_dir, symbol, fields):
    """Capture exact entry-cycle breakout provenance and refresh open-position shadow.

    Uses only already-published runtime/epoch files. It never calls an exchange or emits an intent.
    """
    _capture_runtime_setup_provenance(user_dir,symbol,fields or {})
    epoch=_active_epoch_for_runtime(user_dir,symbol,fields or {})
    if epoch is None:
        return None
    setup=_latest_setup_provenance(user_dir).get(epoch.get('setup_id'))
    monitor=(fields or {}).get('monitor') or {}
    history=list((fields or {}).get('monitor_history') or [])
    if monitor and (not history or history[-1].get('last_closed_bar_ms') != monitor.get('last_closed_bar_ms')):
        history.append(monitor)
    return analyze_open_position(user_dir,symbol,epoch,setup,history)
