"""One durable client ID per CORE entry decision; unresolved orders never resubmit."""
import math
import re
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
    base=dict(order_id=(order or {}).get('id'),client_order_id=receipt['client_order_id'],
              decision_id=receipt['decision_id'],payload=payload)
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
    base.update(exchange_status=status,filled=filled,remaining=remaining,
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
            events.update_order(cfg.user_dir,decision_id,'ORDER_FAILED',**_safe_exchange_error(exc))
            return result
        except Exception as exc:
            # Exceptions before/after acknowledgement may still have reached OKX.
            try:
                order=client.fetch_order_status_by_client_id(receipt['client_order_id'])
            except Exception:
                order=None
        result=_result(order,receipt)
    events.update_order(cfg.user_dir,receipt['decision_id'],result['status'],
                        order_id=result.get('order_id'),reason=result['reason'])
    return result


def reconcile(cfg,client,receipt):
    """Exchange reads only: never resubmit an ambiguous/reserved order."""
    try:
        order=client.fetch_order_status_by_client_id(receipt['client_order_id'])
    except Exception:
        order=None
    result=_result(order,receipt)
    events.update_order(cfg.user_dir,receipt['decision_id'],result['status'],
                        order_id=result.get('order_id'),reason=result['reason'])
    return result
