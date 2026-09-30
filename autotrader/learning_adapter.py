"""Pure bounded self-learning entry adapter. No trading/config authority."""
from __future__ import annotations

import datetime as dt

import learning_policy
import learning_state


def _bucket_conf(value):
    if value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if v < 0 or v > 1:
        return None
    if v < 0.6: return '<0.60'
    if v < 0.7: return '0.60-0.69'
    if v < 0.8: return '0.70-0.79'
    if v < 0.9: return '0.80-0.89'
    return '>=0.90'


def _parse_time(value):
    try:
        return dt.datetime.fromisoformat(value) if value else None
    except (TypeError, ValueError):
        return None


def _hour_bucket(hour):
    if hour is None:
        return None
    start = (int(hour) // 6) * 6
    return f'{start:02d}-{start+5:02d}'


def _actual_values(candidate: dict, features: dict) -> dict:
    when = _parse_time(candidate.get('timestamp') or features.get('timestamp'))
    if when is not None:
        # entry timestamps in production are KST-local or offset aware; preserve local wall clock.
        hour = when.hour
        weekday = when.strftime('%a')
    else:
        hour = features.get('hour_kst')
        weekday = features.get('weekday')
    return {
        'symbol': candidate.get('symbol') or features.get('symbol'),
        'side': candidate.get('side') or candidate.get('action') or features.get('side'),
        'market_regime': features.get('market_regime', candidate.get('market_regime')),
        'trade_alignment': features.get('trade_alignment', candidate.get('trade_alignment')),
        'short_level': features.get('short_level'),
        'correction_active': features.get('correction_active'),
        'gemini_confidence': _bucket_conf(candidate.get('gemini_confidence', candidate.get('confidence'))),
        'gpt_confidence': _bucket_conf(candidate.get('gpt_confidence')),
        'hour_bucket': _hour_bucket(hour),
        'weekday': weekday,
        'tf_combo': set(features.get('tf_combos') or []),
    }


def _matches(evidence: dict, actual: dict) -> bool:
    dimension = evidence.get('dimension')
    expected = evidence.get('value')
    if not dimension:
        return False
    if str(dimension).startswith('tf_combo:'):
        combo = str(dimension).split(':', 1)[1]
        combos = actual.get('tf_combo') or set()
        return f'{combo}:{expected}' in combos
    if str(dimension).startswith('tf:'):
        tf = str(dimension).split(':', 1)[1]
        states = actual.get('tf_states') or {}
        return states.get(tf) == expected
    if dimension not in actual:
        return False
    got = actual.get(dimension)
    if got is None:
        return False
    if dimension == 'tf_combo':
        return expected in got
    if isinstance(got, bool) and isinstance(expected, str):
        return str(got) == expected
    return got == expected


def _direction(evidence: dict):
    d = evidence.get('long_direction') or evidence.get('recent_direction')
    return d if d in ('positive','negative') else None


def evaluate_entry(user_dir: str, candidate: dict, features: dict, live_enabled: bool) -> dict:
    try:
        state, safe = learning_state.load_active_state(user_dir)
    except Exception as exc:
        return {
            'action':'ALLOW','confidence_delta':0.0,'matched_patterns':[],
            'reason':f'LEARNING_ERROR:{type(exc).__name__}','live_applied':False,
            'shadow_action':'ALLOW','shadow_confidence_delta':0.0,
        }
    if not safe:
        return {
            'action':'ALLOW','confidence_delta':0.0,'matched_patterns':[],
            'reason':'LEARNING_ERROR:data_quality','live_applied':False,
            'shadow_action':'ALLOW','shadow_confidence_delta':0.0,
        }

    actual = _actual_values(candidate, features)
    matched = []
    shadow_delta = 0.0
    live_delta = 0.0
    shadow_hold = False
    live_hold = False
    for pattern_id in sorted(state):
        row = state[pattern_id] or {}
        evidence = dict(row.get('evidence') or {})
        if not _matches(evidence, actual):
            continue
        direction = _direction(evidence)
        item = {
            'pattern_id': pattern_id,
            'state': row.get('state'),
            'direction': direction,
            'dimension': evidence.get('dimension'),
            'value': evidence.get('value'),
            'contribution': learning_policy.score_contribution(evidence),
        }
        matched.append(item)
        if row.get('state') in ('SHADOW_LEARNING','VALIDATED','LIVE_BOUNDED'):
            shadow_delta += item['contribution']
            if direction == 'negative' and learning_policy.eligible_for_validation(evidence):
                shadow_hold = True
        if bool(live_enabled) and row.get('state') == 'LIVE_BOUNDED':
            if learning_policy.eligible_for_live(evidence, True, True):
                live_delta += item['contribution']
                if direction == 'negative':
                    live_hold = True

    shadow_delta = learning_policy.clip_delta(shadow_delta)
    live_delta = learning_policy.clip_delta(live_delta) if live_enabled else 0.0
    return {
        'action': 'HOLD_BY_LEARNING' if live_hold else 'ALLOW',
        'confidence_delta': live_delta,
        'matched_patterns': matched,
        'reason': 'negative_live_pattern' if live_hold else ('matched_live_patterns' if matched and live_enabled else 'shadow_only' if matched else 'no_match'),
        'live_applied': bool(live_hold or (live_enabled and abs(live_delta) > 0)),
        'shadow_action': 'HOLD_BY_LEARNING' if shadow_hold else 'ALLOW',
        'shadow_confidence_delta': shadow_delta,
    }
