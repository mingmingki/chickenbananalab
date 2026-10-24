"""Causal entry-phase facts. This module has no order or admission authority."""
import datetime as dt
import json
import math

BEGIN = '[CORE_ENTRY_TIMING_BEGIN]'
END = '[CORE_ENTRY_TIMING_END]'
VERSION = 'closed_5m_episode_v1'


def _utc(value):
    if hasattr(value, 'to_pydatetime'):
        value = value.to_pydatetime()
    if isinstance(value, str):
        value = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
    if not isinstance(value, dt.datetime):
        raise ValueError('timestamp_required')
    return value.replace(tzinfo=dt.timezone.utc) if value.tzinfo is None else value.astimezone(dt.timezone.utc)


def confirmed_frame(frame, timeframe, *, now=None):
    """Return native bars closed at least 3 seconds ago, with naive UTC stamps."""
    from candle_finality import TF_SECONDS, GRACE_SECONDS
    result = frame.copy()
    result['timestamp'] = result['timestamp'].map(lambda value: _utc(value).replace(tzinfo=None))
    cutoff = _utc(now or dt.datetime.now(dt.timezone.utc)).replace(tzinfo=None)-dt.timedelta(seconds=GRACE_SECONDS)
    duration = dt.timedelta(seconds=TF_SECONDS[timeframe])
    return result[result['timestamp']+duration <= cutoff].reset_index(drop=True)


def evaluate(closed_dfs, *, now=None):
    """Reconstruct a first 30m range break without resetting it on each new low.

    Origin ATR and reference level are frozen at the first confirmed break.
    A 0.50-origin-ATR pullback followed by a prior-bar high/low reclaim is a
    separately dated continuation setup. These values are evidence for the AIs,
    never a local entry veto, a prediction, or a profitability estimate.
    """
    unknown = {'version': VERSION, 'status': 'unknown', 'event_key': None,
               'phase': 'unknown', 'reason': 'confirmed_5m_data_unavailable'}
    try:
        now = _utc(now or dt.datetime.now(dt.timezone.utc))
        frame = (closed_dfs or {}).get('5m')
        if frame is None or len(frame) < 8:
            return unknown
        rows = []
        for raw in frame.tail(100).to_dict('records'):
            stamp = _utc(raw['timestamp'])
            if stamp + dt.timedelta(minutes=5) > now:
                continue
            row = {k: float(raw[k]) for k in ('open', 'high', 'low', 'close', 'atr_14')}
            if any(not math.isfinite(row[k]) or row[k] <= 0 for k in ('open','high','low','close')):
                return dict(unknown, reason='invalid_prices')
            if not row['low'] <= min(row['open'], row['close']) <= max(row['open'], row['close']) <= row['high']:
                return dict(unknown, reason='invalid_ohlc')
            if not math.isfinite(row['atr_14']) or row['atr_14'] <= 0:
                if not rows:  # Indicator warm-up is a prefix, not a broken market bar.
                    continue
                return dict(unknown, reason='invalid_atr_after_warmup')
            rows.append(dict(row, at=stamp))
        if len(rows) < 8:
            return unknown
        if any(b['at']-a['at'] != dt.timedelta(minutes=5) for a,b in zip(rows, rows[1:])):
            return dict(unknown, reason='noncontiguous_5m_data')
        last = rows[-1]
        as_of = last['at'] + dt.timedelta(minutes=5)
        if now-as_of > dt.timedelta(minutes=6):
            return dict(unknown, reason='stale_confirmed_5m_data')
        episode = None
        for i in range(6, len(rows)):
            row, prev = rows[i], rows[i-1]
            prior = rows[i-6:i]
            high, low = max(x['high'] for x in prior), min(x['low'] for x in prior)
            side = ('long' if row['close'] > high and row['close'] > prev['close'] else
                    'short' if row['close'] < low and row['close'] < prev['close'] else None)
            if side and (episode is None or episode['side'] != side):
                episode = dict(side=side, origin=row['at'], price=row['close'], atr=row['atr_14'],
                               level=high if side=='long' else low,
                               peak=row['high'] if side=='long' else row['low'],
                               pullback=0.0, extreme=row['low'] if side=='long' else row['high'],
                               kind='first_range_break')
                continue
            if episode is None:
                continue
            sign = 1 if episode['side']=='long' else -1
            # Opposite confirmed closes beyond the original break invalidate it.
            if sign*(row['close']-episode['level']) < -0.25*episode['atr']:
                episode = None
                continue
            favorable = row['high'] if sign==1 else row['low']
            adverse = row['low'] if sign==1 else row['high']
            # Use the previous bar's extreme so one candle's unknown high/low
            # ordering cannot create and resolve a pullback in that same candle.
            if episode['pullback'] >= 0.50*episode['atr'] and sign*(row['close']-(prev['high'] if sign==1 else prev['low'])) > 0:
                episode = dict(side=episode['side'], origin=row['at'], price=row['close'], atr=row['atr_14'],
                               level=episode['extreme'], peak=favorable, pullback=0.0, extreme=adverse,
                               kind='pullback_resume')
                continue
            # A favorable crash-bar wick is not a confirmed counter-move.
            # Measure from the prior peak and require an adverse close-to-close move.
            pullback = sign*(episode['peak']-row['close'])
            if sign*(row['close']-prev['close']) < 0 and pullback > episode['pullback']:
                episode['pullback'] = pullback
                episode['extreme'] = adverse
            episode['peak'] = max(episode['peak'], favorable) if sign==1 else min(episode['peak'], favorable)
        current = last['close']
        changes = {}
        for minutes in (15, 30, 60):
            if len(rows) > minutes//5:
                ref = rows[-1-minutes//5]['close']
                changes[str(minutes)+'m_pct'] = round((current/ref-1)*100, 6)
        result = dict(version=VERSION, status='ok', as_of=as_of.isoformat(),
                      current_price=current, changes=changes, event_key=None, phase='range',
                      recent_60m_low=min(x['low'] for x in rows[-12:]),
                      recent_60m_high=max(x['high'] for x in rows[-12:]),
                      interpretation='observed prices, not future reward or a win probability')
        if episode:
            sign = 1 if episode['side']=='long' else -1
            age = (as_of-episode['origin']-dt.timedelta(minutes=5)).total_seconds()/60
            distance = sign*(current-episode['price'])/episode['atr']
            phase = 'extended' if distance >= 1.25 else 'early' if age <= 10 else 'established'
            result.update(side=episode['side'], phase=phase, setup_kind=episode['kind'],
                          origin_closed_at=(episode['origin']+dt.timedelta(minutes=5)).isoformat(),
                          origin_price=episode['price'], origin_atr=episode['atr'],
                          original_break_level=episode['level'], age_minutes=age,
                          move_from_origin_atr=round(distance,6),
                          pullback_atr=round(episode['pullback']/episode['atr'],6),
                          event_key=episode['side']+'|'+episode['origin'].isoformat()+'|'+episode['kind'])
        return result
    except (KeyError, ValueError, TypeError, AttributeError, IndexError, OverflowError):
        return unknown


def format_prompt(context):
    return '\n'+BEGIN+'\n'+json.dumps(context, ensure_ascii=False, separators=(',', ':'))+'\n'+END+'\n'


def preserve_prompt_block(summary):
    start = summary.find(BEGIN)
    end = summary.find(END, start)
    return '\n'+summary[start:end+len(END)]+'\n' if start >= 0 and end >= 0 else ''
