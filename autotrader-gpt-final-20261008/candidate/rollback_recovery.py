"""Operator-only settlement after exchange-proven protective exit; no orders."""
import json
from core_unified_policy import number

def record_proven_external_exit(store,symbol,pending_id,evidence):
    # Caller holds account lock and proves: missing original order, two flat
    # reads, no working orders/protection, exact effective owned algo + raw fills.
    with store.transaction():
        state=store.state(symbol);pending=store.pending(symbol)
        if (not pending or pending['id']!=pending_id or pending['kind']!='EXIT'
                or number(pending['filled'])!=0 or not evidence):
            raise ValueError('reservation_changed')
        if (len({e['trade_id'] for e in evidence})!=len(evidence) or
                sum((number(e['qty'],positive=True) for e in evidence),number(0))!=number(state['filled_qty'])):
            raise ValueError('external_quantity_mismatch')
        for e in evidence:
            if not e['order_id'] or not e['trade_id'] or e['filled_ms'] is None:
                raise ValueError('external_identity_missing')
            store._account_fill(symbol,state,'EXIT',e['order_id'],e['trade_id'],e['qty'],e['price'],e['filled_ms'])
        pending.update(settlement='superseded_by_verified_protection_exit',external_close_evidence=evidence)
        store.db.execute("UPDATE intents SET status='complete',payload=? WHERE id=?",(json.dumps(pending),pending_id))
        state.update(filled_qty='0',entry_risk_used='0',phase='FLAT',pending_intent=None,
                     revision=state['revision']+1,external_close_evidence=evidence)
        store.db.execute('UPDATE states SET revision=?,payload=? WHERE symbol=?',(state['revision'],json.dumps(state),symbol))
