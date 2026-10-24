"""Avoid paid flat CORE decisions that the unchanged order gate cannot admit.

This module never grants an entry permission and never handles a held position.
The final order gate remains authoritative even if this optimization fails.
"""
import datetime as dt
import json
import os

import core_trend_simplify_v33_106 as trend


def assess(cfg, position, closed_dfs, *, as_of):
    if (getattr(cfg, 'CORE_ENTRY_AI_PREFILTER_V33_109', False) is not True
            or getattr(cfg, 'CORE_AI_STRATEGY_AUTHORITY', False) is not True
            or getattr(cfg, 'CORE_TREND_SIMPLE_V33_106', False) is not True
            or position is not None):
        return {'skip_ai': False, 'reason': 'not_opted_in_flat_core'}
    try:
        frame = (closed_dfs or {}).get('4h')
        strength = .5 if getattr(cfg, 'CORE_TREND_STRENGTH_V33_108', False) is True else 0.
        gates = {side: trend.assess_entry(side, frame, as_of=as_of, min_strength_atr=strength)
                 for side in ('long', 'short')}
        skip = not any(gate['allowed'] for gate in gates.values())
        return {'skip_ai': skip, 'gates': gates,
                'reason': 'both_directions_forbidden_by_existing_4h_gate' if skip else 'existing_4h_gate_permits_entry'}
    except Exception as exc:
        return {'skip_ai': False, 'reason': 'prefilter_unavailable_continue_existing_pipeline',
                'error_type': type(exc).__name__}


def record_skip(user_dir, symbol, result, *, trigger, now):
    if not result.get('skip_ai'):
        return
    when = trend._as_utc(now)
    if when is None:
        raise ValueError('invalid observation time')
    row = dict(result, symbol=symbol, engine='CORE', ai_called=False,
               time=when.isoformat(), trigger=trigger, label='NO_PAID_CALL_NO_ORDER')
    payload = (json.dumps(row, ensure_ascii=False, sort_keys=True, allow_nan=False)+'\n').encode()
    os.makedirs(user_dir, exist_ok=True)
    fd = os.open(os.path.join(user_dir, 'core_entry_ai_prefilter_v33_109.jsonl'),
                 os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        if os.write(fd, payload) != len(payload):
            raise OSError('incomplete prefilter observation')
    finally:
        os.close(fd)
