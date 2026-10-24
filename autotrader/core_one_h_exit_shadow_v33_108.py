"""Passive Oct 23-Nov 6 CORE 1H/15m adverse exit study.
Never calls an exchange or execution function. Only reads confirmed candles.
Snapshots and historical stop/target comparison are DIAGNOSTIC, NOT fill PnL.
"""
import datetime as dt
import json
import math
import os
from pathlib import Path
import threading
import tempfile

KST = dt.timezone(dt.timedelta(hours=9))
START = dt.datetime(2026, 10, 23, 14, 0, tzinfo=KST).astimezone(dt.timezone.utc)
END = START + dt.timedelta(days=14)
_LOCK = threading.RLock()
HORIZONS = ((60, '1h'), (240, '4h'), (1440, '24h'))


def num(v):
    try:
        v = float(v)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError, OverflowError):
        return None


def utc(v):
    if hasattr(v, 'to_pydatetime'):
        v = v.to_pydatetime()
    if not isinstance(v, dt.datetime):
        v = dt.datetime.fromisoformat(str(v).replace('Z', '+00:00'))
    return v.replace(tzinfo=dt.timezone.utc) if v.tzinfo is None else v.astimezone(dt.timezone.utc)


def closed_last(frame, tf_minutes, clock):
    if frame is None or len(frame) < 1:
        return None
    row = frame.iloc[-1]
    end = utc(row['timestamp']) + dt.timedelta(minutes=tf_minutes)
    age = (clock - end).total_seconds()
    return row if 0 <= age <= tf_minutes * 60 + 10 else None


def signal(position, opening, frames, *, now):
    """Return observation only if EVERY prerequisite has confirmed, fresh bars."""
    side = position.get('side')
    if side not in ('long', 'short') or not isinstance(opening, dict):
        return None
    bar5 = closed_last(frames.get('5m'), 5, now)
    bar1h = closed_last(frames.get('1h'), 60, now)
    bar15 = closed_last(frames.get('15m'), 15, now)
    f15 = frames.get('15m')
    if bar5 is None or bar1h is None or bar15 is None or f15 is None or len(f15) < 2:
        return None
    # Opening record must represent this position, not an older closed lifecycle.
    entry = num(opening.get('price'))
    actual_entry = num(position.get('entry_price'))
    stop = num(opening.get('sl_price'))
    target = num(opening.get('tp_price'))
    px = num(bar5.get('close'))
    hclose = num(bar1h.get('close'))
    hema = num(bar1h.get('ema_20'))
    slope0 = num(f15.iloc[-2].get('ema_20'))
    slope1 = num(bar15.get('ema_20'))
    if any(v is None or v <= 0 for v in (entry, actual_entry, stop, target, px, hclose, hema, slope0, slope1)):
        return None
    if abs(actual_entry - entry) / entry > .02:
        return None
    if side == 'long':
        if stop >= entry or target <= entry:
            return None
        opposite = hclose < hema and slope1 < slope0
        loss_r = (entry - px) / (entry - stop)
    else:
        if stop <= entry or target >= entry:
            return None
        opposite = hclose > hema and slope1 > slope0
        loss_r = (px - entry) / (stop - entry)
    if not opposite or loss_r < .3:
        return None
    at = (utc(bar5['timestamp']) + dt.timedelta(minutes=5)).isoformat()
    return dict(kind='signal', symbol=opening.get('symbol'), side=side,
                entry=entry, original_stop=stop, original_target=target,
                risk_price=abs(entry-stop), price=px, adverse_r=round(loss_r, 7),
                hypothetical_exit_r=round(-loss_r, 7),
                h1_closed_at=(utc(bar1h['timestamp']) + dt.timedelta(hours=1)).isoformat(),
                m15_closed_at=(utc(bar15['timestamp']) + dt.timedelta(minutes=15)).isoformat(),
                at=at, label='SHADOW_ONLY_NO_ORDER', reason='1h_close_ema20_opposite__15m_ema20_slope_opposite__loss_gte_0.3r')


def observe(user_dir, symbol, position, opening, frames, *, now=None):
    now = utc(now or dt.datetime.now(dt.timezone.utc))
    frame = frames.get('5m') if isinstance(frames, dict) else None
    current = closed_last(frame, 5, now)
    if current is None:
        return
    root = Path(user_dir)
    safe = symbol.replace('/', '_').replace(':', '_')
    state_file = root / ('core_1h_exit_shadow_v33_108_' + safe + '.json')
    log_file = root / 'core_1h_exit_shadow_v33_108.jsonl'
    with _LOCK:
        if state_file.is_file():
            try:
                state = json.loads(state_file.read_text())
            except (OSError, json.JSONDecodeError):
                return  # Do not overwrite possibly recoverable state.
        else:
            state = {'events': [], 'keys': []}
        changed = False
        output = []
        # Append at most once per entry lifecycle.
        if START <= now < END and position and opening:
            key = str(opening.get('execution_id') or (opening.get('time'), opening.get('symbol'), opening.get('side')))
            if key not in state['keys']:
                observation = signal(position, opening, frames, now=now)
                if observation:
                    observation['symbol'] = symbol
                    observation['lifecycle_key'] = key
                    observation['trigger_epoch'] = utc(observation['at']).timestamp()
                    observation['last_scan_epoch'] = observation['trigger_epoch']
                    observation['milestones'] = []
                    observation['benchmark'] = None
                    observation['gapped'] = False
                    state['keys'].append(key)
                    state['events'].append(observation)
                    output.append({k:v for k,v in observation.items() if k not in ('last_scan_epoch','milestones','benchmark','gapped')})
                    changed = True
        # Forward-only tracking of 5m candles, including after the live position is closed.
        if frame is not None and len(frame):
            last_available = utc(current['timestamp']).timestamp() + 300
            for e in state['events']:
                if e.get('finished'):
                    continue
                sign = 1 if e['side']=='long' else -1
                for _, bar in frame.iterrows():
                    bar_end = utc(bar['timestamp']).timestamp() + 300
                    if bar_end <= e['last_scan_epoch'] or bar_end > last_available or bar_end <= e['trigger_epoch']:
                        continue
                    if bar_end - e['last_scan_epoch'] > 310:
                        if not e['gapped']:
                            output.append(dict(kind='gap', symbol=symbol, lifecycle_key=e['lifecycle_key'],
                                               after=e['last_scan_epoch'], until=bar_end, label='SHADOW_INCOMPLETE_PATH'))
                        e['gapped'] = True
                    e['last_scan_epoch'] = bar_end
                    changed = True
                    o,h,l,c = (num(bar.get(k)) for k in ('open','high','low','close'))
                    if any(v is None for v in (o,h,l,c)):
                        e['gapped'] = True
                        continue
                    if e['benchmark'] is None and not e['gapped']:
                        stop, target = e['original_stop'], e['original_target']
                        stop_hit = l <= stop if sign == 1 else h >= stop
                        tp_hit = h >= target if sign == 1 else l <= target
                        if stop_hit or tp_hit:
                            at_stop = min(stop,o) if sign == 1 else max(stop,o)
                            fill = at_stop if stop_hit else target
                            benchmark_r = sign*(fill-e['entry'])/e['risk_price']
                            e['benchmark'] = dict(when=dt.datetime.fromtimestamp(bar_end,dt.timezone.utc).isoformat(),
                                                   reason='stop' if stop_hit else 'target',
                                                   hypothetical_px=fill, r=round(benchmark_r,7),
                                                   difference_hold_minus_shadow_r=round(benchmark_r-e['hypothetical_exit_r'],7),
                                                   same_bar_both=bool(stop_hit and tp_hit))
                            output.append(dict(kind='benchmark_hit', symbol=symbol,
                                               lifecycle_key=e['lifecycle_key'], **e['benchmark'],
                                               label='HYPOTHETICAL_NOT_REAL_FILL'))
                    for minutes,label in HORIZONS:
                        if label not in e['milestones'] and bar_end >= e['trigger_epoch']+60*minutes:
                            output.append(dict(kind='followup', symbol=symbol, lifecycle_key=e['lifecycle_key'],
                                               horizon=label, at=dt.datetime.fromtimestamp(bar_end,dt.timezone.utc).isoformat(),
                                               mark=c, mark_r=round(sign*(c-e['entry'])/e['risk_price'],7),
                                               shadow_exit_r=e['hypothetical_exit_r'],
                                               benchmark=e['benchmark'], complete_path=not e['gapped'],
                                               label='CANDLE_MARK_NOT_ORDER_FILL'))
                            e['milestones'].append(label)
                    if '24h' in e['milestones']:
                        e['finished']=True
                        break
        if not changed:
            return
        root.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(dir=root, prefix='.core_exit_shadow_',suffix='.tmp')
        try:
            with os.fdopen(fd,'w') as f:
                json.dump(state, f, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(name,state_file)
        finally:
            if os.path.exists(name):
                os.unlink(name)
        if output:
            with log_file.open('a') as f:
                for row in output:
                    f.write(json.dumps(row,ensure_ascii=False,sort_keys=True) + '\n')
