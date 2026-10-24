"""Proven profitable partial-close evidence for a monotonic residual stop floor.

Only an exact lifecycle, every recorded executed reduction ID, OKX fill evidence,
and strictly positive verified net settlement can arm the residual protection.
Never updates exchange orders; the existing owned-OCO floor writer does that.
"""
import math
from datetime import datetime,timezone,timedelta

def finite(value, positive=False):
    if isinstance(value, bool):
        return None
    try:
        x=float(value)
        if not math.isfinite(x) or (positive and x<=0):
            return None
        return x
    except (TypeError, ValueError, OverflowError):
        return None

def confirmed_profitable_partial(symbol,position,state,reductions):
    if not isinstance(position,dict) or not isinstance(state,dict):
        return False
    import reduce_v2_state
    identity=reduce_v2_state.position_identity(position)
    if (not identity or state.get('lifecycle_id')!=identity
            or state.get('pending_order')
            or not state.get('baseline_known')
            or (finite(state.get('actual_reduced_contracts'),positive=True) is None)):
        return False
    ids=state.get('executed_ids')
    if not isinstance(ids,list) or not ids or len(set(map(str,ids)))!=len(ids):
        return False
    candidates={}
    for row in reductions or ():
        if not isinstance(row,dict) or row.get('type')!='reduce':
            continue
        eid=row.get('execution_id')
        if eid not in ids:
            continue
        if eid in candidates:
            return False
        candidates[eid]=row
    if len(candidates)!=len(ids):
        return False
    side=position.get('side')
    entry=finite(position.get('entry_price'),positive=True)
    if side not in ('long','short') or entry is None:
        return False
    # Exact exchange epoch time is essential: OKX may reuse posId, and two
    # unrelated positions can share side + entry price. Never apply a prior
    # lifecycle's profitable reduction to a new position.
    start_ms=position.get('entry_timestamp_ms')
    if type(start_ms) is not int or start_ms<=0:
        return False
    total_net=0.
    for eid in ids:
        row=candidates[eid]
        if (row.get('symbol')!=symbol or row.get('strategy_group')!='core'
                or row.get('side')!=side or row.get('dry_run') is not False
                or row.get('pnl_source')!='order_fills'):
            return False
        # A verified trade row must have an unambiguous post-entry timestamp.
        try:
            ts=datetime.fromisoformat(str(row.get('time')))
            if ts.tzinfo is None:
                ts=ts.replace(tzinfo=timezone(timedelta(hours=9)))
            reduced_at_ms=int(ts.timestamp()*1000)
        except (TypeError,ValueError,OverflowError):
            return False
        if reduced_at_ms<start_ms or reduced_at_ms>int(datetime.now(timezone.utc).timestamp()*1000)+60000:
            return False
        row_entry=finite(row.get('entry_price'),positive=True)
        amount=finite(row.get('amount'),positive=True)
        close=finite(row.get('close_price'),positive=True)
        net=finite(row.get('okx_net_pnl'))
        proof=row.get('close_execution_evidence') or {}
        if (row_entry is None or abs(row_entry-entry)/entry>.001
                or amount is None or close is None or net is None
                or proof.get('price_basis')!='verified_order_fills'
                or not proof.get('order_id')):
            return False
        tids=proof.get('trade_ids')
        if (not isinstance(tids,list) or not tids
                or any(not str(tid).strip() for tid in tids)
                or len(set(map(str,tids)))!=len(tids)):
            return False
        px=finite(proof.get('vwap'),positive=True)
        filled=finite(proof.get('filled_contracts'),positive=True)
        if (px is None or filled is None
                or abs(px-close)>max(1e-6,px*1e-6)
                or abs(filled-amount)>1e-8):
            return False
        total_net+=net
    return math.isfinite(total_net) and total_net>0
