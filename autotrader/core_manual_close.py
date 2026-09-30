"""Durable account/symbol manual-close reservation; unknown submissions never retry."""
import json
import os
import tempfile
import time
import uuid

import jsonl_cache
import process_lock


def _path(user_dir):
    return os.path.join(user_dir, 'core_manual_close.json')


def _load(user_dir):
    try:
        with open(_path(user_dir), encoding='utf-8') as stream:
            return json.load(stream)
    except FileNotFoundError:
        return {}


def _update(user_dir, symbol, change):
    with jsonl_cache.get_path_lock(_path(user_dir)):
        data = _load(user_dir)
        record = change(data.get(symbol))
        data[symbol] = record
        process_lock.save_json_atomic(_path(user_dir), data)
        return dict(record)


def get(user_dir, symbol):
    with jsonl_cache.get_path_lock(_path(user_dir)):
        return _load(user_dir).get(symbol)


def reserve(user_dir, symbol, now=None):
    now = time.time() if now is None else now
    return _update(user_dir, symbol, lambda old: old if old and old.get('status') != 'completed' else {
        'close_id': 'cm' + uuid.uuid4().hex[:28], 'started_at': now,
        'reason': 'manual_stop', 'status': 'reserved', 'confirmed_at': None,
        'release_at': None, 'position': None, 'journaled': False,
    })


def patch(user_dir, symbol, close_id, **fields):
    def change(old):
        if not old or old['close_id'] != close_id:
            raise ValueError('manual close identity changed')
        return dict(old, **fields)
    return _update(user_dir, symbol, change)


def confirm(user_dir, symbol, close_id, cooldown_seconds=900, now=None):
    now = time.time() if now is None else now
    def change(old):
        if not old or old['close_id'] != close_id:
            raise ValueError('manual close identity changed')
        if old.get('confirmed_at') is not None:
            return old
        return dict(old, status='confirmed', confirmed_at=now, release_at=now + cooldown_seconds)
    return _update(user_dir, symbol, change)


def complete_if_superseded_by_position(user_dir, symbol, position):
    """Complete a confirmed flat-close guard once a newer live position exists.

    A confirmed record with no captured position represents a verified-flat manual
    close/cooldown.  If a later position is already live, the old guard belongs to
    the previous lifecycle and must not block management of the new position.
    Unresolved/submitted closes and records with a captured position remain fail-closed.
    """
    entry_timestamp_ms = (position or {}).get('entry_timestamp_ms')
    if entry_timestamp_ms is None:
        return False
    changed = [False]

    def change(old):
        confirmed_at = (old or {}).get('confirmed_at')
        if (not old or old.get('status') != 'confirmed' or confirmed_at is None
                or old.get('position') is not None or not old.get('journaled')
                or old.get('cleanup_pending')
                or float(entry_timestamp_ms) <= float(confirmed_at) * 1000):
            return old
        changed[0] = True
        return dict(old, status='completed', superseded_by_position_id=position.get('position_id'))

    _update(user_dir, symbol, change)
    return changed[0]


def block_reason(record, now=None, bar_closed_at=None, approval_started_at=None, require_fresh=True):
    if not record or record.get('status') == 'completed':
        return None
    now = time.time() if now is None else now
    if record.get('confirmed_at') is None or record.get('cleanup_pending'):
        return 'manual_close_unresolved'
    if now < record['release_at']:
        return 'manual_close_cooldown'
    if not require_fresh:
        return None
    if bar_closed_at is None or bar_closed_at <= record['confirmed_at']:
        return 'new_closed_bar_required'
    if (approval_started_at is None or approval_started_at < record['release_at']
            or now - approval_started_at > 180):
        return 'stale_preclose_approval'
    return None
