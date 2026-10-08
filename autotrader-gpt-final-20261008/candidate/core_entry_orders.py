"""One durable client ID per CORE entry decision; unresolved orders never resubmit."""
import math
import re
import time
import ccxt
import core_entry_events as events


def _safe_exchange_error(exc):
    # Extract numeric OKX code only. Never persist a credential-bearing exception.
    match=re.search(r'["\'](?:sCode|code)["\']\s*:\s*["\'](\d+)["\']',str(exc))
    return dict(exchange_code=match.group(1) if match else None,
                reason='exchange_rejected:'+type(exc).__name__)


def _identity(order, receipt, side):
    if not isinstance(order,dict): return False
    info=order.get('info') or {}
    cid=order.get('clientOrderId') or info.get('clOrdId')
    return (cid == receipt['client_order_id'] and bool(order.get('id'))
            and order.get('symbol') == receipt['symbol']
            and order.get('side') == ('buy' if side=='long' else 'sell'))


def _result(order,receipt):
    payload=receipt['payload'];side=payload['side']
    base=dict(symbol=receipt['symbol'],order_id=(order or {}).get('id'),client_order_id=receipt['client_order_id'],
              decision_id=receipt['decision_id'],payload=payload,
              exchange_order_created_ms=(order or {}).get('timestamp') or ((order or {}).get('info') or {}).get('cTime'))
    if not _identity(order,receipt,side):
        return dict(base,status='ORDER_PENDING',reason='exchange_order_identity_unconfirmed')
    try:
        filled=float(order.get('filled') or 0)
        expected=float(payload['contracts'])
        remaining=float(order['remaining'])
        if not all(math.isfinite(x) for x in (filled,expected,remaining)) or min(filled,remaining)<0:
            raise ValueError
    except (KeyError,TypeError,ValueError):
        return dict(base,status='ORDER_PENDING',reason='exchange_fill_unconfirmed')
    status=order.get('status')
    base.update(average=order.get('average'),exchange_status=status,filled=filled,remaining=remaining,
                original_order_terminal=status in ('closed','canceled','rejected','expired'))
    if status == 'closed' and filled>0 and remaining==0 and abs(filled-expected)<=1e-8:
        return dict(base,status='FILLED_UNJOURNALED',reason='exchange_fill_confirmed',
                    average=order.get('average'))
    if status in ('canceled','rejected','expired') and filled==0:
        return dict(base,status='ORDER_FAILED',reason='exchange_order_'+status)
    return dict(base,status='ORDER_PENDING',reason='partial_fill_requires_reconciliation' if filled else 'exchange_order_pending')


def submit_once(cfg,client,symbol,decision_id,side,amount,sl,tp,on_submitted,metadata=None):
    import okx_client
    payload=dict(side=side,quantity_coin=amount,contracts=amount/client.contract_size(),
                 sl_price=sl,tp_price=tp,leverage=cfg.LEVERAGE)
    payload.update(metadata or {})
    try:
        reserved,receipt=events.reserve_order(cfg.user_dir,decision_id,symbol,payload)
    except Exception as exc:
        return dict(status='LOCAL_BLOCKED',reason='order_receipt_unavailable:'+type(exc).__name__)
    if not reserved and receipt['status'] in ('FILLED','ORDER_FAILED'):
        return dict(receipt['payload'],status=receipt['status'],reason='decision_already_processed',
                    duplicate=True,client_order_id=receipt['client_order_id'],decision_id=receipt['decision_id'])
    if not reserved and receipt['decision_id'] != decision_id:
        return dict(status='LOCAL_BLOCKED',reason='unresolved_prior_entry_order',
                    prior_decision_id=receipt['decision_id'])
    if not reserved:
        # Reconcile original parameters/decision, never the new callback's plan.
        try:
            order=client.fetch_order_status_by_client_id(receipt['client_order_id'])
        except Exception:
            order=None
        result=_result(order,receipt)
    else:
        events.update_order(cfg.user_dir,decision_id,'ORDER_SUBMITTED')
        on_submitted(dict({k:payload[k] for k in ('side','quantity_coin','contracts','sl_price','tp_price','leverage')},
                          client_order_id=receipt['client_order_id']))
        try:
            order=client.create_position_with_sl_tp(side,amount,sl,tp,
                client_order_id=receipt['client_order_id'],
                attach_algo_cl_ord_id='ca'+receipt['client_order_id'][2:])
        except okx_client.UnknownOrderStateError:
            try:
                order=client.fetch_order_status_by_client_id(receipt['client_order_id'])
            except Exception:
                order=None
        except ccxt.ExchangeError as exc:
            # OkxClient only propagates known definite rejections as ExchangeError.
            result=dict(status='ORDER_FAILED',client_order_id=receipt['client_order_id'],
                        decision_id=decision_id,payload=payload,**_safe_exchange_error(exc))
            events.update_order(cfg.user_dir,decision_id,'ORDER_PENDING',terminal_status='ORDER_FAILED',**_safe_exchange_error(exc))
            return result
        except Exception as exc:
            # Exceptions before/after acknowledgement may still have reached OKX.
            try:
                order=client.fetch_order_status_by_client_id(receipt['client_order_id'])
            except Exception:
                order=None
        result=_result(order,receipt)
        # OKX's immediate market-order acknowledgement may omit filled/remaining.
        # Re-read this exact client order ID briefly. Never submit again or infer
        # a fill from a position or incomplete response.
        if result['status']=='ORDER_PENDING' and result['reason'] in (
                'exchange_fill_unconfirmed','exchange_order_identity_unconfirmed','exchange_order_pending'):
            for delay in (0.35, 0.70):
                time.sleep(delay)
                try:
                    refreshed=client.fetch_order_status_by_client_id(receipt['client_order_id'])
                except Exception:
                    continue
                checked=_result(refreshed,receipt)
                if checked['status']=='FILLED_UNJOURNALED' or checked['status']=='ORDER_FAILED':
                    result=checked
                    break
                if checked.get('order_id'):
                    result=checked
    events.update_order(cfg.user_dir,receipt['decision_id'],'ORDER_PENDING' if result['status']=='ORDER_FAILED' else result['status'],
                        terminal_status='ORDER_FAILED' if result['status']=='ORDER_FAILED' else None,
                        order_id=result.get('order_id'),reason=result['reason'])
    return result


def reconcile(cfg,client,receipt):
    """Exchange reads only: never resubmit an ambiguous/reserved order."""
    if receipt['payload'].get('terminal_status')=='ORDER_FAILED':
        return dict(status='ORDER_FAILED',payload=receipt['payload'],decision_id=receipt['decision_id'],
            client_order_id=receipt['client_order_id'],order_id=receipt['payload'].get('order_id'),
            reason=receipt['payload'].get('reason','exchange_order_failed'),exchange_code=receipt['payload'].get('exchange_code'))
    try:
        order=client.fetch_order_status_by_client_id(receipt['client_order_id'])
    except Exception:
        order=None
    result=_result(order,receipt)
    events.update_order(cfg.user_dir,receipt['decision_id'],'ORDER_PENDING' if result['status']=='ORDER_FAILED' else result['status'],
                        terminal_status='ORDER_FAILED' if result['status']=='ORDER_FAILED' else None,
                        order_id=result.get('order_id'),reason=result['reason'])
    return result


def confirmed_fill_position_proof(client, result, position):
    """Bind only exchange-proven original fills to the still-current lifecycle.

    OKX's position tradeId identifies its latest mutation. It must be an actual
    fill of this durable order, with complete fill quantity and a matching
    lifecycle creation window. A similar size/side or timestamp alone is never
    sufficient authority to close a position.
    """
    payload=result.get('payload') or {}
    if (result.get('status') not in ('FILLED_UNJOURNALED','ORDER_PENDING') or not result.get('filled',0)
            or not payload.get('preflight_flat_at') or not position
            or not position.get('position_id') or not position.get('last_trade_id')):
        return None
    try:
        trades=client.fetch_entry_order_fills(result['order_id'])
        if not trades or len(trades)>=100: return None
        expected=float(result['filled'])
        if abs(expected+float(result.get('remaining',0))-float(payload['contracts']))>1e-8 and not result.get('original_order_terminal'):
            return None
        created=float(result.get('exchange_order_created_ms') or 0)
        pos_created=float(position.get('entry_timestamp_ms') or 0)
        preflight_ms=float(payload['preflight_flat_at'])*1000
        ids=set()
        total=weighted=0.;last=0.
        for trade in trades:
            tid=str(trade.get('id') or '')
            if (not tid or tid in ids or str(trade.get('order'))!=str(result['order_id'])
                    or trade.get('symbol')!=result['symbol']
                    or trade.get('side')!=('buy' if payload['side']=='long' else 'sell')):
                return None
            ids.add(tid)
            qty=float(trade['amount']);price=float(trade['price']);stamp=float(trade['timestamp'])
            if not all(math.isfinite(v) and v>0 for v in (qty,price,stamp)):return None
            total+=qty;weighted+=qty*price;last=max(last,stamp)
        if (str(position['last_trade_id']) not in ids or position.get('side')!=payload['side']
                or abs(float(position.get('contracts',0))-expected)>1e-8 or abs(total-expected)>1e-8
                or not preflight_ms-1000<=created<=pos_created<=last+1000):
            return None
        tick=float((client.exchange.market(result['symbol']).get('precision') or {}).get('price') or 1e-8)
        if abs(float(position.get('entry_price',0))-weighted/total)>max(tick*2,1e-8):return None
        if abs(float(result.get('average') or 0)-weighted/total)>max(tick*2,1e-8):return None
        return dict(last_trade_id=str(position['last_trade_id']),fill_ids=sorted(ids),order_id=result['order_id'])
    except Exception:
        return None
