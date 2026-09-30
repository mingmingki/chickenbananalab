"""Read-only accounting for one original CORE reduction order's exact fills.

The installed ccxt OKX parser maps fillSz to trade.amount without converting
derivative contracts into coins, and negates OKX fee into signed ccxt fee.cost.
This helper supports linear USDT-settled contracts only. It never treats mark
price, order average, a position delta alone, or unrelated recent fees as fills.
"""
from decimal import Decimal, InvalidOperation
import math


def _number(value, *, positive=False):
    if isinstance(value, bool) or value is None:
        raise ValueError('missing numeric evidence')
    result = Decimal(str(value))
    if not result.is_finite() or (positive and result <= 0):
        raise ValueError('invalid numeric evidence')
    return result


def _identifier(unified, raw=None):
    if unified is not None and raw is not None and str(unified) != str(raw):
        raise ValueError('conflicting exchange identity')
    result = unified if unified is not None else raw
    if isinstance(result, bool) or result is None or not str(result).strip():
        raise ValueError('missing exchange identity')
    return str(result)


def resolve_reduce_fill(client, position, filled_contracts, order):
    """Return exact exit-fill amounts/PnL, or None when evidence is incomplete.

    filled_contracts is the caller's independently reconciled position decrease,
    in contracts. All matched unique fills and order.filled must equal it. A
    truncated/delayed query stays unknown; this helper never submits/retries an
    order. The caller must preserve its durable pending order on None.

    net_pnl = price PnL minus these exit fills' signed USDT fees. Entry fees and
    funding are outside this scope (funding_fee=None), and must remain in final
    lifecycle reconciliation. source='order_fills' is not position-history PnL.
    """
    try:
        return _resolve(client, position, filled_contracts, order)
    except (AttributeError, TypeError, ValueError, InvalidOperation, OverflowError):
        return None


def _resolve(client, position, filled_contracts, order):
    if not isinstance(position, dict) or not isinstance(order, dict):
        return None
    symbol = client.symbol
    if not isinstance(symbol, str) or not symbol.endswith('/USDT:USDT'):
        return None
    side = position.get('side')
    if side not in ('long', 'short') or order.get('status') not in ('closed', 'canceled'):
        return None
    info = order.get('info') or {}
    order_id = _identifier(order.get('id'), info.get('ordId'))
    expected = _number(filled_contracts, positive=True)
    if expected > _number(position.get('contracts'), positive=True):
        return None
    if _number(order.get('filled'), positive=True) != expected:
        return None
    entry_price = _number(position.get('entry_price'), positive=True)
    try:
        contract_size = _number(client.contract_size(), positive=True)
    except Exception:
        return None
    since = _number(order.get('timestamp') if order.get('timestamp') is not None else 0)
    if since < 0 or since != since.to_integral_value():
        return None
    try:
        rows = client.fetch_trades_for_order(order_id, int(since))
    except Exception:
        return None
    if not isinstance(rows, list) or not rows:
        return None

    unique = {}
    for row in rows:
        if not isinstance(row, dict):
            return None
        raw = row.get('info') or {}
        row_order = _identifier(row.get('order'), raw.get('ordId'))
        if row_order != order_id:
            continue
        trade_id = _identifier(row.get('id'), raw.get('tradeId'))
        if row.get('symbol') != symbol or row.get('side') != ('sell' if side == 'long' else 'buy'):
            return None
        quantity = _number(row.get('amount'), positive=True)
        price = _number(row.get('price'), positive=True)
        fee_info = row.get('fee')
        if not isinstance(fee_info, dict) or fee_info.get('currency') != 'USDT':
            return None
        fee = _number(fee_info.get('cost'))
        timestamp = _number(row.get('timestamp'))
        if timestamp < since or timestamp != timestamp.to_integral_value():
            return None
        evidence = (quantity, price, fee, timestamp)
        if trade_id in unique and unique[trade_id] != evidence:
            return None
        unique[trade_id] = evidence

    quantity = sum((row[0] for row in unique.values()), Decimal(0))
    if quantity != expected:
        return None
    notional_price = sum((row[0] * row[1] for row in unique.values()), Decimal(0))
    fees = sum((row[2] for row in unique.values()), Decimal(0))
    sign = 1 if side == 'long' else -1
    gross = (notional_price - quantity * entry_price) * contract_size * sign
    numeric = {'gross_pnl': gross, 'fee': fees, 'net_pnl': gross - fees,
               'exit_price': notional_price / quantity, 'filled_contracts': quantity,
               'filled_coin_amount': quantity * contract_size}
    converted = {key: float(value) for key, value in numeric.items()}
    if not all(math.isfinite(value) for value in converted.values()):
        return None
    return {
        **converted, 'funding_fee': None, 'source': 'order_fills',
        'fee_source': 'order_id_matched', 'accounting_scope': 'reduce_exit_fills_only',
        'order_id': order_id, 'trade_ids': sorted(unique),
        'first_fill_timestamp_ms': int(min(row[3] for row in unique.values())),
        'last_fill_timestamp_ms': int(max(row[3] for row in unique.values())),
    }
