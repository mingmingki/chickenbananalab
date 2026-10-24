"""v33-70: exact owned reduce-only close proof, read-only; fail closed."""
from decimal import Decimal, InvalidOperation
import datetime
import math


def dec(value):
    try:
        z = Decimal(str(value))
        return z if z.is_finite() else None
    except (InvalidOperation, TypeError, ValueError):
        return None


def verify(client, symbol, ledger, latest_entry, journal_rows):
    """Attest one terminal owned ReversalIntent against OKX order + unique fills.

    Called only after two exchange-flat, order-free snapshots are requested
    by the original v33-49 reconciliation. Never sends orders or edits ledgers.
    """
    try:
        records = ledger.records()
        reversals = [r for r in records if r.kind == "ReversalIntent"]
        if not reversals:
            return None, "NO_OWNED_REVERSAL"
        close = reversals[-1]
        if close is not records[-1] or close.state != "terminal":
            return None, "LATEST_CLOSE_NOT_TERMINAL"
        if (not isinstance(close.position_epoch, str) or
                not close.position_epoch.startswith(latest_entry.intent_id + ":")):
            return None, "CLOSE_NOT_OWNED_BY_LATEST_ENTRY"
        if close.requested_side != latest_entry.requested_side:
            return None, "SIDE_OR_IDENTITY_MISMATCH"
        order_id = str(close.exchange_order_id or "")
        if not order_id.isdecimal():
            return None, "NO_EXACT_CLOSE_ORDER"
        quantity = dec(latest_entry.filled_quantity)
        requested = dec(close.requested_quantity)
        if quantity is None or quantity <= 0 or requested != quantity:
            return None, "OWNED_QUANTITY_MISMATCH"
        matches = [x for x in journal_rows if (
            x.get("type") == "close" and
            x.get("strategy_group") == "candidate_c" and
            x.get("symbol") == symbol and
            x.get("execution_id") == close.intent_id and
            x.get("reason") == "MANUAL_OR_EXTERNAL_CLOSE" and
            x.get("pnl_source") == "okx_realized")]
        if len(matches) != 1 or dec(matches[0].get("amount")) != quantity:
            return None, "OWNED_JOURNAL_RECEIPT_NOT_UNIQUE"
        if matches[0].get("side") != latest_entry.requested_side:
            return None, "JOURNAL_DIRECTION_MISMATCH"
        expected = "sell" if latest_entry.requested_side == "long" else "buy"
        order = client.exchange.fetch_order(order_id, symbol)
        info = order.get("info") or {}
        if (str(order.get("id")) != order_id or
            order.get("status") != "closed" or
            order.get("side") != expected or
            str(info.get("reduceOnly")).lower() != "true" or
            dec(order.get("filled")) != quantity or
            dec(order.get("amount")) != quantity or
            dec(order.get("remaining")) != Decimal(0)):
            return None, "EXCHANGE_CLOSE_ORDER_NOT_FULLY_VERIFIED"
        since_ms = int((datetime.datetime.fromisoformat(matches[0]["time"]) -
                        datetime.timedelta(minutes=15)).timestamp() * 1000)
        trades = client.fetch_trades_for_order(order_id, since_ms, limit=100)
        if not trades or len(trades) >= 100:
            return None, "FILL_LIST_MISSING_OR_TRUNCATED"
        ids = set()
        total = Decimal(0)
        for trade in trades:
            tid = str(trade.get("id") or "")
            size = dec(trade.get("amount"))
            if (not tid or tid in ids or
                str(trade.get("order")) != order_id or
                trade.get("side") != expected or
                size is None or size <= 0):
                return None, "INVALID_EXCHANGE_FILL_IDENTITY"
            ids.add(tid)
            total += size
        if total != quantity:
            return None, "EXCHANGE_FILLS_QUANTITY_MISMATCH"
        return dict(
            close_execution_id=close.intent_id,
            order_id=order_id, trade_ids=sorted(ids),
            external_close_qty=str(quantity), receipt_type="OWNED_REDUCE_ONLY_CLOSE_V33_70",
            filled_contracts=str(total)), "VERIFIED_OWNED_CLOSE"
    except Exception:
        return None, "EXCHANGE_OR_LEDGER_PROOF_UNAVAILABLE"
