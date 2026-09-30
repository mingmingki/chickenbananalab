"""Join read-only, entry-time evidence to completed trade lifecycles."""
from __future__ import annotations

import datetime as dt
import glob
import json
import os

import jsonl_cache
from trade_learning_lifecycle import build_completed_lifecycles

_TFS = ('1m','3m','5m','1h','4h','1d')


def _parse_time(value):
    if not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(dt.timezone(dt.timedelta(hours=9))).replace(tzinfo=None)
        return parsed
    except (TypeError, ValueError):
        return None


def _read_family(user_dir: str, basename: str) -> list[dict]:
    current = os.path.join(user_dir, basename)
    rows = list(jsonl_cache.load_jsonl_cached(current)) if os.path.exists(current) else []
    for path in sorted(glob.glob(current + '.*')):
        if path.endswith('.lock'):
            continue
        try:
            rows.extend(jsonl_cache.tail_jsonl(path, 100000))
        except OSError:
            continue
    return [r for r in rows if isinstance(r, dict)]


def latest_at_or_before(records: list[dict], symbol: str, cutoff: dt.datetime):
    eligible = []
    for row in records:
        if row.get('symbol') != symbol:
            continue
        stamp = _parse_time(row.get('time'))
        if stamp is not None and stamp <= cutoff:
            eligible.append((stamp, row))
    return max(eligible, key=lambda pair: pair[0])[1] if eligible else None


def classify_tf_state(tf_payload: dict | None) -> str:
    closed = (tf_payload or {}).get('closed') or {}
    explicit = closed.get('trend') or closed.get('structure') or closed.get('state')
    if isinstance(explicit, str) and explicit.lower() in ('bullish','bearish','mixed','neutral'):
        return explicit.lower()
    try:
        close = float(closed['close']); ema20 = float(closed['ema20']); ema50 = float(closed['ema50'])
    except (KeyError, TypeError, ValueError):
        return 'unknown'
    if close > ema20 > ema50:
        return 'bullish'
    if close < ema20 < ema50:
        return 'bearish'
    return 'mixed'


def _structure_state(row: dict | None) -> str:
    structures = (row or {}).get('structures') or (row or {}).get('structure') or {}
    for tf in ('5m','3m','1h','4h','1d','1m'):
        payload = structures.get(tf) or {}
        closed = payload.get('closed') if isinstance(payload, dict) else None
        if isinstance(closed, dict):
            value = closed.get('trend') or closed.get('state') or closed.get('structure')
            if isinstance(value, str):
                return value.lower()
        if isinstance(payload, str):
            return payload.lower()
    return 'unknown'


def _first_value(rows, *keys):
    for row in rows:
        if not row:
            continue
        for key in keys:
            if key in row and row.get(key) is not None:
                return row.get(key)
    return None


def _confidence_bucket(value):
    if value is None:
        return 'unknown'
    try: value = float(value)
    except (TypeError, ValueError): return 'unknown'
    if value < 0.6: return '<0.60'
    if value < 0.7: return '0.60-0.69'
    if value < 0.8: return '0.70-0.79'
    if value < 0.9: return '0.80-0.89'
    return '>=0.90'


def _load_sources(user_dir: str) -> dict[str, list[dict]]:
    out = {
        'market': _read_family(user_dir, 'market_structure_log.jsonl'),
        'candle': _read_family(user_dir, 'candle_finality_log.jsonl'),
        'gpt': _read_family(user_dir, 'gpt_shadow_log.jsonl'),
        'posai': _read_family(user_dir, 'position_ai_log.jsonl'),
        'lowfollow': _read_family(user_dir, 'low_follow_through_shadow.jsonl'),
        'daily': _read_family(user_dir, 'entry_tf_daily_backfill.jsonl'),
    }
    out['lowfollow_by_trade'] = {str(r.get('trade_id')): r for r in out['lowfollow'] if r.get('trade_id')}
    out['daily_by_trade'] = {str(r.get('trade_id')): r for r in out['daily'] if r.get('trade_id')}
    return out

def enrich_lifecycle(user_dir: str, lifecycle: dict, sources: dict | None = None) -> dict:
    result = dict(lifecycle)
    cutoff = _parse_time(lifecycle.get('entry_time'))
    tf = {name: {'state':'unknown'} for name in _TFS}
    if cutoff is None:
        result['features'] = {'tf':tf,'market_structure':'unknown','coverage':{'market_structure':False,'candle_finality':False,'gpt':False,'position_ai':False}}
        return result

    symbol = lifecycle.get('symbol')
    sources = sources or _load_sources(user_dir)
    trade_id = str(lifecycle.get('trade_id') or '')
    lowfollow = (sources.get('lowfollow_by_trade') or {}).get(trade_id)
    daily = (sources.get('daily_by_trade') or {}).get(trade_id)
    market = latest_at_or_before(sources.get('market', []), symbol, cutoff)
    candle = latest_at_or_before(sources.get('candle', []), symbol, cutoff)
    gpt = latest_at_or_before(sources.get('gpt', []), symbol, cutoff)
    posai = latest_at_or_before(sources.get('posai', []), symbol, cutoff)
    indicators = (candle or {}).get('indicators') or {}
    for name in _TFS:
        tf[name] = {'state': classify_tf_state(indicators.get(name))}
    fallback_used = False
    low_states = (lowfollow or {}).get('states') or {}
    for name in ('3m','5m','1h','4h'):
        state = str(low_states.get(name) or 'unknown').lower()
        if tf[name]['state'] == 'unknown' and state in ('bullish','bearish','mixed','neutral'):
            tf[name] = {'state': state, 'source': 'asof_backfill'}
            fallback_used = True
    daily_state = str((daily or {}).get('state_1d') or 'unknown').lower()
    if tf['1d']['state'] == 'unknown' and (daily or {}).get('resolved') and daily_state in ('bullish','bearish','mixed','neutral'):
        tf['1d'] = {'state': daily_state, 'source': 'asof_backfill'}
        fallback_used = True
    analysis_tf_complete = bool(gpt is not None and all(tf[name]['state'] != 'unknown' for name in ('3m','5m','1h','4h','1d')))

    open_event = next((e for e in lifecycle.get('events',[]) if e.get('type') == 'open'), {})
    gemini_action = _first_value([candle, market, gpt], 'gemini_action','action')
    gemini_conf = _first_value([candle, market, gpt], 'gemini_confidence')
    gpt_decision = _first_value([gpt], 'gpt_decision','gate_result')
    gpt_conf = _first_value([gpt], 'gpt_confidence')
    short_level = _first_value([gpt, market, candle, open_event], 'short_level','SHORT_LEVEL')
    correction = _first_value([gpt, market, candle, open_event], 'correction_active','CORRECTION_ACTIVE')
    reason = str(open_event.get('reason') or '')
    entry_kind = 'reversal' if 'reversal' in reason.lower() else 'fresh'
    freshness = None
    candle_time = _parse_time((candle or {}).get('time'))
    if candle_time is not None:
        freshness = max(0.0, (cutoff - candle_time).total_seconds())

    result['features'] = {
        'tf': tf,
        'market_structure': _structure_state(market),
        'gemini_action': gemini_action or 'unknown',
        'gemini_confidence': gemini_conf,
        'gemini_confidence_bucket': _confidence_bucket(gemini_conf),
        'gpt_decision': gpt_decision or 'unknown',
        'gpt_confidence': gpt_conf,
        'gpt_confidence_bucket': _confidence_bucket(gpt_conf),
        'short_level': short_level or 'unknown',
        'correction_active': correction if isinstance(correction, bool) else 'unknown',
        'entry_kind': entry_kind,
        'market_regime': open_event.get('market_regime') or 'unknown',
        'trade_alignment': open_event.get('trade_alignment') or 'unknown',
        'entry_hour_kst': cutoff.hour,
        'weekday': cutoff.strftime('%a'),
        'entry_freshness_seconds': freshness,
        'coverage': {
            'market_structure': market is not None,
            'candle_finality': candle is not None,
            'gpt': gpt is not None,
            'position_ai': posai is not None,
            'asof_tf_backfill': fallback_used,
            'analysis_tf_complete': analysis_tf_complete,
        },
    }
    return result


def build_featured_trades(user_dir: str) -> list[dict]:
    sources = _load_sources(user_dir)
    return [enrich_lifecycle(user_dir, row, sources=sources) for row in build_completed_lifecycles(user_dir)]


def compute_lifecycle_excursions(lifecycle: dict, candles: list[dict]) -> dict:
    """Compute MAE/MFE using only candles inside [entry_time, exit_time]."""
    start = _parse_time(lifecycle.get("entry_time"))
    end = _parse_time(lifecycle.get("exit_time"))
    try:
        entry = float(lifecycle.get("entry_price"))
        price_r = float(lifecycle.get("initial_stop_distance"))
    except (TypeError, ValueError):
        start = None
        entry = price_r = 0.0
    empty = {"mae_r":None,"mfe_r":None,"mfe_giveback_pct":None,"time_to_mae":None,"time_to_mfe":None}
    if start is None or end is None or entry <= 0 or price_r <= 0:
        return empty
    eligible = []
    for row in candles or []:
        stamp = _parse_time(row.get("time") or row.get("close_time") or row.get("timestamp"))
        if stamp is None or stamp < start or stamp > end:
            continue
        try:
            high = float(row["high"]); low = float(row["low"])
        except (KeyError, TypeError, ValueError):
            continue
        eligible.append((stamp, high, low))
    if not eligible:
        return empty
    side = lifecycle.get("side")
    favorable = []
    adverse = []
    for stamp, high, low in eligible:
        if side == "long":
            favorable.append((max(0.0, high-entry), stamp))
            adverse.append((max(0.0, entry-low), stamp))
        elif side == "short":
            favorable.append((max(0.0, entry-low), stamp))
            adverse.append((max(0.0, high-entry), stamp))
        else:
            return empty
    mfe, mfe_at = max(favorable, key=lambda x: x[0])
    mae, mae_at = max(adverse, key=lambda x: x[0])
    giveback = None
    try:
        exit_price = float(lifecycle.get("exit_price"))
        realized_favorable = max(0.0, exit_price-entry) if side == "long" else max(0.0, entry-exit_price)
        giveback = max(0.0, (mfe-realized_favorable) / mfe * 100.0) if mfe > 0 else 0.0
    except (TypeError, ValueError):
        pass
    return {
        "mae_r": mae/price_r, "mfe_r": mfe/price_r, "mfe_giveback_pct": giveback,
        "time_to_mae": (mae_at-start).total_seconds()/60.0,
        "time_to_mfe": (mfe_at-start).total_seconds()/60.0,
    }
