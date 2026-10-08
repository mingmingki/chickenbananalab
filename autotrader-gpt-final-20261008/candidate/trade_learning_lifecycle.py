"""Read-only reconstruction of completed trading lifecycles for analysis."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os

import jsonl_cache
import pnl_reconciliation
import trade_log

_EVENT_TYPES = {'open', 'add', 'reduce', 'close'}


def _parse_time(value):
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _journal(user_dir: str) -> list[dict]:
    path = os.path.join(user_dir, 'trades_log.jsonl')
    rows = jsonl_cache.load_jsonl_cached(path)
    return sorted(
        [dict(r) for r in rows if r.get('type') in _EVENT_TYPES and not r.get('dry_run')],
        key=lambda r: (_parse_time(r.get('time')) or dt.datetime.min, r.get('type') or ''),
    )


def _group(row: dict) -> str:
    g = row.get('_group') or row.get('strategy_group')
    return g if g in ('core', 'fast', 'candidate_c') else 'legacy'


def _net(row: dict) -> float:
    return float(pnl_reconciliation._trade_net_pnl(row))



def _event_key(row: dict):
    execution_id = row.get('execution_id')
    if execution_id:
        return ('execution_id', execution_id)
    return (row.get('type'), row.get('symbol'), row.get('side'), row.get('time'))

def _close_key(row: dict):
    return (row.get('symbol'), row.get('side'), row.get('time'))

def _trade_id(symbol, side, entry_time, exit_time, ordinal):
    raw = f'{symbol}|{side}|{entry_time}|{exit_time}|{ordinal}'.encode()
    return hashlib.sha256(raw).hexdigest()[:24]


def _finish(events: list[dict], close: dict, ordinal: int, coverage='complete') -> dict:
    open_row = next((e for e in events if e.get('type') == 'open'), None)
    realized = [e for e in events if e.get('type') in ('reduce', 'close')]
    entry_time = open_row.get('time') if open_row else None
    exit_time = close.get('time')
    start = _parse_time(entry_time)
    end = _parse_time(exit_time)
    holding = ((end - start).total_seconds() / 60.0) if start and end else None
    gross = sum(float(e.get('pnl') or 0.0) for e in realized)
    fee = sum(float(e.get('fee') or 0.0) for e in realized)
    final_close_net = _net(close)
    reduce_net = sum(_net(e) for e in realized if e.get('type') == 'reduce')
    lifecycle_net = sum(_net(e) for e in realized)
    initial_stop = (open_row or {}).get("sl_price")
    entry_price_value = (open_row or {}).get("price", close.get("entry_price"))
    try:
        initial_stop_distance = abs(float(entry_price_value) - float(initial_stop)) if initial_stop is not None else None
    except (TypeError, ValueError):
        initial_stop_distance = None
    effective_stop = close.get("sl_price", initial_stop)
    try:
        effective_stop_distance = abs(float(entry_price_value) - float(effective_stop)) if effective_stop is not None else initial_stop_distance
    except (TypeError, ValueError):
        effective_stop_distance = initial_stop_distance
    observed_gemini = tuple(dict.fromkeys(
        str(v) for v in (e.get("gemini_assessment") or e.get("_gemini_assessment") for e in events) if v
    ))
    return {
        'trade_id': _trade_id(close.get('symbol'), close.get('side'), entry_time, exit_time, ordinal),
        'symbol': close.get('symbol'),
        'side': close.get('side'),
        'entry_time': entry_time,
        'exit_time': exit_time,
        'entry_price': (open_row or {}).get('price', close.get('entry_price')),
        'exit_price': close.get('close_price'),
        'net_pnl': lifecycle_net,
        'lifecycle_net': lifecycle_net,
        'final_close_net': final_close_net,
        'reduce_net': reduce_net,
        'difference_due_to_reduces': lifecycle_net - final_close_net,
        'final_close_reason': close.get('reason'),
        'gross_pnl': gross,
        'fee': fee,
        'fee_cost': fee,
        'initial_stop_distance': initial_stop_distance,
        'effective_stop_distance': effective_stop_distance,
        'partial_reduction_count': sum(1 for e in events if e.get('type') == 'reduce'),
        'observed_gemini_states': observed_gemini,
        'holding_minutes': holding,
        'strategy_group': _group(close),
        'coverage': coverage,
        'events': [dict(e) for e in events],
    }


def build_completed_lifecycles(user_dir: str) -> list[dict]:
    """Build completed lifecycles without mutating any trading source data."""
    canonical_closes = pnl_reconciliation.load_all_records(user_dir)
    canonical_close_map = {_close_key(r): dict(r, type='close') for r in canonical_closes}
    canonical_realized = {
        _event_key(r): r for r in trade_log.load_realized_trades(user_dir, dry_run=False)
    }
    journal = []
    for raw in _journal(user_dir):
        row = canonical_realized.get(_event_key(raw), raw) if raw.get('type') in ('reduce','close') else raw
        if raw.get('type') == 'close':
            row = canonical_close_map.get(_close_key(raw), row)
        journal.append(dict(row))
    active: dict[tuple[str, str], list[dict]] = {}
    completed: list[dict] = []
    ordinal = 0

    for row in journal:
        key = (row.get('symbol'), row.get('side'))
        kind = row.get('type')
        if kind == 'open':
            active[key] = [row]
        elif kind in ('add', 'reduce'):
            if key in active:
                active[key].append(row)
        elif kind == 'close':
            ordinal += 1
            if key in active:
                events = active.pop(key) + [row]
                completed.append(_finish(events, row, ordinal, 'complete'))
            else:
                completed.append(_finish([row], row, ordinal, 'partial'))

    # Canonical closed records may include FAST/legacy records with no source open journal.
    seen = {(t['symbol'], t['side'], t['exit_time']) for t in completed}
    for close in canonical_closes:
        marker = (close.get('symbol'), close.get('side'), close.get('time'))
        if marker in seen:
            continue
        ordinal += 1
        close_event = dict(close, type='close')
        completed.append(_finish([close_event], close_event, ordinal, 'partial'))

    return sorted(completed, key=lambda t: _parse_time(t.get('exit_time')) or dt.datetime.min)
