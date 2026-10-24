"""Classify *verified owned* post-reduction OCO resize in progress.

Pure read-only diagnostic: never places, cancels or amends an order.
Short transient overcoverage is not the same as missing protection.
Undercovers, orphan IDs, changed SL/TP, unknown lifecycle and aged
transactions must still be treated as critical confirmed mismatches.
"""
from __future__ import annotations
import math
import time
import reduce_v2_state

MAX_RESIZE_GRACE_SECONDS = 90.0
TOL = 1e-8


def _finite(value):
    if isinstance(value, bool):
        return None
    try:
        n = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return n if math.isfinite(n) else None


def owned_reduce_resizing(user_dir, symbol, position, *, algo_id, observed_size,
                          observed_sl, observed_tp, now_epoch=None):
    """Return classified state only for a known, recent, over-sized owned OCO."""
    if not user_dir or not symbol or not isinstance(position, dict):
        return None
    if position.get('side') not in ('long','short') or not algo_id:
        return None
    try:
        record = reduce_v2_state.get(user_dir, symbol)
        pending = (record or {}).get('pending_order')
        if not isinstance(pending, dict):
            return None
        old = pending.get('protection')
        before = pending.get('position')
        if not isinstance(old, dict) or not isinstance(before, dict):
            return None
        if old.get('algo_id') != algo_id or before.get('side') != position['side']:
            return None
        if pending.get('lifecycle_id') != reduce_v2_state.position_identity(position):
            return None
        # Recovery must be for the same exchange position, not a new entry.
        if pending.get('lifecycle_id') != reduce_v2_state.position_identity(before):
            return None
        submitted = _finite(pending.get('submitted_at'))
        when = time.time() if now_epoch is None else _finite(now_epoch)
        if submitted is None or when is None:
            return None
        age = when-submitted
        if not (0 <= age <= MAX_RESIZE_GRACE_SECONDS):
            return None
        expected = _finite(position.get('contracts'))
        before_size = _finite(pending.get('before_contracts'))
        old_size = _finite(observed_size)
        original_size = _finite(old.get('sz'))
        submitted_reduce = _finite(pending.get('contracts'))
        if any(x is None for x in (expected,before_size,old_size,original_size,submitted_reduce)):
            return None
        if (not (expected>0 and old_size>expected+TOL)
                or abs(old_size-before_size)>TOL
                or abs(old_size-original_size)>TOL
                or not (0 < old_size-expected <= submitted_reduce+TOL)):
            return None
        old_sl = _finite(old.get('sl_price'))
        current_sl = _finite(observed_sl)
        old_tp = _finite(old.get('tp_price'))
        current_tp = _finite(observed_tp)
        if old_sl is None or current_sl is None or abs(old_sl-current_sl)>TOL:
            return None
        if old_tp is None:
            if current_tp is not None:
                return None
        elif current_tp is None or abs(old_tp-current_tp)>TOL:
            return None
        return {'age_seconds':round(age,3),'observed_contracts':old_size,
                'expected_contracts':expected,'algo_id':algo_id,
                'reduction_execution_id':pending.get('client_order_id'),
                'submitted_amend':bool(pending.get('protection_amend_submitted'))}
    except (OSError,ValueError,KeyError,TypeError,OverflowError):
        # Cannot prove ownership / lifecycle: never downgrade the mismatch.
        return None
