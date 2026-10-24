"""v33-43: conservative pre-order CORE partial-close contract normalization.

Correct binary floating-point dust at an already quantized exchange lot boundary.
Never round materially sub-step intent up, force a minimum trade, or permit a
full close. Validate the CCXT serialized quantity before an actual order call.
"""
from decimal import Decimal, ROUND_HALF_UP
import math


class ReduceQuantityInvalid(ValueError):
    pass


def _dec(value):
    if isinstance(value, bool) or value is None:
        raise ReduceQuantityInvalid("NON_NUMERIC")
    try:
        number = Decimal(str(value))
    except (ValueError, ArithmeticError):
        raise ReduceQuantityInvalid("BAD_NUMERIC")
    if not number.is_finite():
        raise ReduceQuantityInvalid("NONFINITE")
    return number


def canonical_reduce_contracts(client, symbol, quantized_coin_amount,
                               intended_coin_cap, current_contracts):
    """Return stable native float contracts or fail closed.

    Only snap values within 1e-10 of one lot step. The true user-defined
    reduction size/cap is checked separately, allowing only the same tiny
    numeric tolerance. Never change min-size or risk-cap policy.
    """
    client.ensure_markets_loaded()
    market = client.exchange.market(symbol)
    size = _dec(market.get("contractSize"))
    precision = _dec((market.get("precision") or {}).get("amount"))
    minimum = _dec(((market.get("limits") or {}).get("amount") or {}).get("min") or 0)
    maximum = ((market.get("limits") or {}).get("amount") or {}).get("max")
    if size <= 0 or precision <= 0 or minimum < 0:
        raise ReduceQuantityInvalid("MARKET_METADATA_INVALID")
    step = (market.get("info") or {}).get("lotSz")
    if step not in (None, "") and _dec(step) != precision:
        raise ReduceQuantityInvalid("LOT_STEP_PRECISION_CONFLICT")
    coin = _dec(quantized_coin_amount)
    cap_coin = _dec(intended_coin_cap)
    position = _dec(current_contracts)
    if coin <= 0 or cap_coin <= 0 or position <= 0:
        raise ReduceQuantityInvalid("NONPOSITIVE")
    raw = coin / size
    target = cap_coin / size
    steps = raw / precision
    count = steps.to_integral_value(rounding=ROUND_HALF_UP)
    tol = precision * Decimal("0.0000000001")
    canonical = count * precision
    if count <= 0 or abs(canonical - raw) > tol:
        raise ReduceQuantityInvalid("OFF_LOT_STEP")
    if canonical - target > tol:
        raise ReduceQuantityInvalid("INTENT_CAP_EXCEEDED")
    if canonical >= position or canonical < minimum:
        raise ReduceQuantityInvalid("POSITION_OR_MINIMUM_INVALID")
    if maximum not in (None, "") and canonical > _dec(maximum):
        raise ReduceQuantityInvalid("MAX_ORDER_INVALID")
    native = float(canonical)
    if not math.isfinite(native) or _dec(native) != canonical:
        raise ReduceQuantityInvalid("FLOAT_ROUNDTRIP_CHANGED")
    try:
        serialized = client.exchange.amount_to_precision(symbol, native)
    except Exception as exc:
        raise ReduceQuantityInvalid("EXCHANGE_PRECISION_FAILED") from exc
    if _dec(serialized) != canonical:
        raise ReduceQuantityInvalid("SERIALIZED_QUANTITY_MISMATCH")
    return native
