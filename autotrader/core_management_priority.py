"""Conservative per-position recovery priority shared by CORE loops.

Read-only; never places or changes an exchange order. Unknown durable
state blocks new discretionary management until operator reconciliation.
"""
from __future__ import annotations
import core_add_position_state
import reduce_v2_state

def classify(user_dir, symbol):
    if not user_dir or not symbol:
        return {'status':'STATE_UNKNOWN','sources':['invalid_identity']}
    sources=[]
    errors=[]
    for label, loader in (
        ('reduce', reduce_v2_state.get),
        ('add', core_add_position_state.get),
    ):
        try:
            value=loader(user_dir,symbol)
            if value is not None and not isinstance(value,dict):
                errors.append(label)
            elif isinstance(value,dict) and value.get('pending_order'):
                sources.append(label)
        except Exception:
            errors.append(label)
    if errors:
        return {'status':'STATE_UNKNOWN','sources':errors}
    if sources:
        return {'status':'RECOVERY_PENDING','sources':sources}
    return {'status':'READY','sources':[]}
