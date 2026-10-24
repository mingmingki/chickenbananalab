"""Account-local runtime control for bounded self-learning live influence."""
from __future__ import annotations

import datetime as dt
import json
import os
import tempfile

NAME = 'self_learning_control.json'

def _path(user_dir: str) -> str:
    return os.path.join(user_dir, NAME)

def _default() -> dict:
    return {'live_enabled': False, 'validation_epoch_at': None, 'updated_at': None}

def get(user_dir: str) -> dict:
    try:
        with open(_path(user_dir), encoding='utf-8') as f:
            row = json.load(f)
        if type(row.get('live_enabled')) is not bool:
            raise ValueError('invalid_live_enabled')
        epoch = row.get('validation_epoch_at')
        if epoch is not None and not isinstance(epoch, str):
            raise ValueError('invalid_validation_epoch')
        return {'live_enabled': row['live_enabled'], 'validation_epoch_at': epoch, 'updated_at': row.get('updated_at')}
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return _default()

def _write(user_dir: str, row: dict) -> dict:
    os.makedirs(user_dir, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='.self_learning_control.', dir=user_dir, text=True)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(row, f, ensure_ascii=False)
            f.flush(); os.fsync(f.fileno())
        os.replace(tmp, _path(user_dir))
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return row

def set_live_enabled(user_dir: str, enabled: bool) -> dict:
    if type(enabled) is not bool:
        raise ValueError('boolean_required')
    current = get(user_dir)
    return _write(user_dir, {
        'live_enabled': enabled,
        'validation_epoch_at': current.get('validation_epoch_at'),
        'updated_at': dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds'),
    })

def set_validation_epoch(user_dir: str, epoch_at=None) -> dict:
    if epoch_at is None:
        stamp = dt.datetime.now(dt.timezone.utc)
    elif isinstance(epoch_at, dt.datetime):
        stamp = epoch_at
    elif isinstance(epoch_at, str):
        try:
            stamp = dt.datetime.fromisoformat(epoch_at.replace('Z','+00:00'))
        except ValueError as exc:
            raise ValueError('invalid_validation_epoch') from exc
    else:
        raise ValueError('invalid_validation_epoch')
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=dt.timezone.utc)
    stamp = stamp.astimezone(dt.timezone.utc).isoformat(timespec='seconds')
    current = get(user_dir)
    return _write(user_dir, {
        'live_enabled': bool(current.get('live_enabled', False)),
        'validation_epoch_at': stamp,
        'updated_at': dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds'),
    })
