"""Account-local runtime control for bounded self-learning live influence."""
from __future__ import annotations

import datetime as dt
import json
import os
import tempfile

NAME = 'self_learning_control.json'


def _path(user_dir: str) -> str:
    return os.path.join(user_dir, NAME)


def get(user_dir: str) -> dict:
    try:
        with open(_path(user_dir), encoding='utf-8') as f:
            row = json.load(f)
        if type(row.get('live_enabled')) is not bool:
            raise ValueError('invalid_live_enabled')
        return {'live_enabled': row['live_enabled'], 'updated_at': row.get('updated_at')}
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return {'live_enabled': False, 'updated_at': None}


def set_live_enabled(user_dir: str, enabled: bool) -> dict:
    if type(enabled) is not bool:
        raise ValueError('boolean_required')
    os.makedirs(user_dir, exist_ok=True)
    row = {
        'live_enabled': enabled,
        'updated_at': dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds'),
    }
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
