"""v33-29 exact, owned CORE close-order execution identity.

Proof sources:
1. Fully OKX verified close-order fills (the existing verified_close_fills).
2. Order receipt with matching exchange order ID and GET-verified terminal
   closed, exact full filled quantity, zero remaining, correct side/symbol.
Never derive IDs from timestamp/price and never place orders here.
"""
from __future__ import annotations
from decimal import Decimal,InvalidOperation


def _num(value):
    if value is None or isinstance(value,bool):
        raise ValueError("missing_number")
    result=Decimal(str(value))
    if not result.is_finite() or result<0:
        raise ValueError("invalid_number")
    return result


def receipt_order_id(receipt,symbol):
    if not isinstance(receipt,dict) or receipt.get("symbol") not in (None,symbol):
        return None
    info=receipt.get("info") or {}
    if not isinstance(info,dict):return None
    direct=receipt.get("id")
    raw=info.get("ordId")
    if direct is not None and raw is not None and str(direct)!=str(raw):
        return None
    found=direct if direct is not None else raw
    if found is None or isinstance(found,bool):
        return None
    value=str(found).strip()
    return value if value else None


def verified_identity(client,symbol,position,receipt,close_execution):
    """Return (local journal key, proof method) or (None, diagnosis).

    A locally prefixed ID is an idempotency key incorporating the *verified*
    original exchange order ID; it is not a fictitious OKX trade/fill ID.
    """
    order_id=receipt_order_id(receipt,symbol)
    if not order_id:return None,"receipt_order_id_missing_or_conflicting"
    side=position.get("side")
    if side not in ("long","short"):
        return None,"invalid_side"
    expected_side="sell" if side=="long" else "buy"
    try:
        expected=_num(position.get("contracts"))
        if expected<=0:return None,"invalid_contracts"
    except (ValueError,InvalidOperation,TypeError):
        return None,"invalid_contracts"
    if isinstance(close_execution,dict):
        try:
            if (str(close_execution.get("order_id"))==order_id
                and close_execution.get("price_basis")=="verified_order_fills"
                and _num(close_execution.get("filled_contracts"))==expected
                and _num(close_execution.get("vwap"))>0
                and isinstance(close_execution.get("trade_ids"),list)
                and bool(close_execution["trade_ids"])
                and len(set(map(str,close_execution["trade_ids"])))==len(close_execution["trade_ids"])):
                return f"core-close-order:{symbol}:{order_id}","EXCHANGE_VERIFIED_FILLS"
        except (ValueError,InvalidOperation,TypeError,KeyError):
            pass
        # Conflicting provided fill proof must not be silently "rescued" by a
        # weaker status lookup. Leave it unresolved for reconciliation.
        return None,"provided_fill_proof_conflict"
    try:
        order=client.exchange.fetch_order(order_id,symbol)
        if not isinstance(order,dict):return None,"order_get_unavailable"
        info=order.get("info") or {}
        if not isinstance(info,dict):return None,"malformed_order_info"
        if (str(order.get("id"))!=order_id
            or info.get("ordId") not in (None,order_id)
            or order.get("status")!="closed"
            or order.get("symbol") not in (None,symbol)
            or order.get("side")!=expected_side
            or _num(order.get("filled"))!=expected
            or _num(order.get("remaining"))!=0):
            return None,"terminal_order_not_exactly_confirmed"
        # OKX reduceOnly must not contradict requested close order.
        reduce_only=info.get("reduceOnly",order.get("reduceOnly"))
        if str(reduce_only).lower() not in ("true","1"):
            return None,"reduce_only_not_confirmed"
        return f"core-close-order:{symbol}:{order_id}","EXCHANGE_VERIFIED_TERMINAL_ORDER"
    except Exception:
        return None,"terminal_order_get_unavailable"
