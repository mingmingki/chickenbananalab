"""Strict read-only interpretation of the existing OKX client contract."""
from decimal import Decimal
from core_unified_policy import number


def position_identity(position):
    try:
        raw_id=position['position_id']
        started=position['entry_timestamp_ms']
        side=position['side']
        if (not isinstance(raw_id,str) or not raw_id or type(started) is not int or
                started<=0 or side not in ('long','short')):
            return None
        number(position['contracts'],positive=True)
        return f'{raw_id}:{started}:{side}'
    except (KeyError,ValueError,TypeError):
        return None


def owned_protection(position,orders,*,instrument_id,owned_ids,stop_price):
    def blocked(reason): return dict(confirmed=False,reason=reason)
    try:
        if not position_identity(position) or not isinstance(orders,list) or not owned_ids:
            return blocked('unknown_identity_or_orders')
        # This integration is qualified for net positions only. The old client
        # drops posSide, so an adapter must supply explicit raw account-mode proof.
        if position.get('pos_side')!='net':
            return blocked('position_mode_unqualified')
        qty=number(position['contracts'],positive=True)
        stop=number(stop_price,positive=True)
        sign=1 if position['side']=='long' else -1
        close_side='sell' if sign==1 else 'buy'
        owned=set(owned_ids)
        if any(not isinstance(i,str) or not i for i in owned):
            return blocked('invalid_owned_ids')
        seen,covered={},Decimal(0)
        for row in orders:
            identifier=row.get('algoId')
            if identifier not in owned:
                continue
            if identifier in seen:
                if seen[identifier]!=row:
                    return blocked('conflicting_duplicate')
                continue
            seen[identifier]=row
            if (row.get('instId')!=instrument_id or row.get('side')!=close_side or
                    row.get('posSide')!='net' or
                    row.get('state')!='live' or str(row.get('reduceOnly')).lower()!='true'):
                continue
            actual_stop=number(row['slTriggerPx'],positive=True)
            if sign*(actual_stop-stop)<0:
                continue
            covered+=number(row['sz'],positive=True)
        if covered<qty:
            return blocked('owned_stop_coverage_insufficient')
        return dict(confirmed=True,reason='owned_stop_covers_actual_position',
                    contracts=str(covered),matched_ids=sorted(seen))
    except (KeyError,TypeError,ValueError):
        return blocked('invalid_protection_data')


def normalize_order(order,trades,*,client_id,symbol):
    if order is None:
        return None
    if (not isinstance(order,dict) or order.get('clientOrderId')!=client_id or
            order.get('symbol')!=symbol or
            not isinstance(order.get('id'),str) or not order['id'] or
            not isinstance(trades,list)):
        raise ValueError('order_identity_unknown')
    filled,remaining=number(order['filled']),number(order['remaining'])
    if filled<0 or remaining<0:
        raise ValueError('negative_execution_quantity')
    status=order.get('status')
    if status not in ('open','closed','canceled','expired','rejected'):
        raise ValueError('order_status_unknown')
    seen={}
    for trade in trades:
        if (not isinstance(trade.get('id'),str) or not trade['id'] or
                trade.get('order')!=order['id'] or trade.get('symbol')!=symbol):
            raise ValueError('trade_identity_mismatch')
        row=dict(id=trade['id'],qty=str(number(trade['amount'],positive=True)),
                 price=str(number(trade['price'],positive=True)),
                 filled_ms=trade.get('timestamp'))
        if row['id'] in seen and seen[row['id']]!=row:
            raise ValueError('conflicting_duplicate_trade')
        seen[row['id']]=row
    actual=sum((number(t['qty']) for t in seen.values()),Decimal(0))
    if actual!=filled:
        raise ValueError('fill_history_incomplete')
    terminal=status in ('closed','canceled','expired','rejected')
    if status=='closed' and remaining!=0:
        raise ValueError('closed_order_has_working_quantity')
    return dict(client_id=client_id,exchange_id=order['id'],terminal=terminal,
                filled=str(filled),remaining='0' if terminal else str(remaining),
                canceled_unfilled=str(remaining) if status in ('canceled','expired','rejected') else '0',
                trades=[seen[k] for k in sorted(seen)])
