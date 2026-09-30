"""Persistent voluntary entry pause. Resuming never releases financial guards."""
import datetime
import os

import process_lock
from candidate_c_hybrid_ownership import account_order_lock


def get_status(user_dir, symbol):
    import json
    path = os.path.join(user_dir, 'symbol_entry_control.json')
    with account_order_lock(user_dir):
        try:
            with open(path, encoding='utf-8') as source:
                data = json.load(source)
            record = data.get(symbol, {'paused': False})
            if not isinstance(record.get('paused'), bool):
                raise ValueError('invalid pause record')
            return record
        except FileNotFoundError:
            return {'paused': False}
        except (ValueError, TypeError, AttributeError, OSError):
            return {'paused': True, 'reason': 'entry_control_UNKNOWN'}


def is_paused(user_dir, symbol):
    return get_status(user_dir, symbol)['paused']


def set_paused(user_dir, symbol, paused):
    import json
    if not isinstance(paused, bool):
        raise ValueError('paused must be boolean')
    path = os.path.join(user_dir, 'symbol_entry_control.json')
    with account_order_lock(user_dir):
        try:
            with open(path, encoding='utf-8') as source:
                data = json.load(source)
        except FileNotFoundError:
            data = {}
        if not isinstance(data, dict):
            raise ValueError('entry control UNKNOWN; repair required')
        data[symbol] = {'paused': paused, 'updated_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                        'reason': 'user_entry_pause' if paused else 'user_pause_released_other_guards_unchanged'}
        process_lock.save_json_atomic(path, data)
        return data[symbol]
