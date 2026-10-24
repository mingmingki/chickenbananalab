"""User-approved DOGE/SOL automatic protection distance limits (price fractions)."""
import math
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_FLOOR

SYMBOLS = frozenset({'DOGE/USDT:USDT', 'SOL/USDT:USDT'})
MAX_STOP_FRACTION = 0.02
MAX_TARGET_FRACTION = 0.04


def enabled(cfg, symbol):
    return (symbol in SYMBOLS
            and getattr(cfg, 'CANDIDATE_C_COMPACT_PROTECTION_ENABLED', False) is True
            and getattr(cfg, 'CANDIDATE_C_ORDER_MODE', '') != 'MANUAL_ALL')


def bound_prices(side, entry, stop, target):
    values = tuple(float(v) for v in (entry, stop, target))
    if not all(math.isfinite(v) and v > 0 for v in values):
        raise ValueError('invalid_compact_protection_prices')
    entry, stop, target = values
    if side == 'long' and stop < entry < target:
        return max(stop, entry * (1 - MAX_STOP_FRACTION)), min(target, entry * (1 + MAX_TARGET_FRACTION))
    if side == 'short' and target < entry < stop:
        return min(stop, entry * (1 + MAX_STOP_FRACTION)), max(target, entry * (1 - MAX_TARGET_FRACTION))
    raise ValueError('invalid_compact_protection_direction')


def tighten_held_stop(side, entry, current_stop, proposed_stop, mark):
    values = tuple(float(v) for v in (entry, current_stop, proposed_stop, mark))
    if not all(math.isfinite(v) and v > 0 for v in values):
        raise ValueError('invalid_compact_held_prices')
    entry, current_stop, proposed_stop, mark = values
    if side == 'short':
        stop = min(current_stop, proposed_stop, entry * (1 + MAX_STOP_FRACTION))
        return stop if stop > mark else current_stop
    if side == 'long':
        stop = max(current_stop, proposed_stop, entry * (1 - MAX_STOP_FRACTION))
        return stop if stop < mark else current_stop
    raise ValueError('invalid_compact_held_side')


def normalized_prices(side, entry, stop, target, tick_size):
    """Round both prices toward entry, keeping exact submitted ticks inside caps."""
    stop, target = bound_prices(side, entry, stop, target)
    try:
        tick = Decimal(str(tick_size))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError('compact_tick_size_unavailable') from exc
    if not tick.is_finite() or tick <= 0:
        raise ValueError('compact_tick_size_unavailable')
    def inward(value, rounding):
        return float((Decimal(str(value)) / tick).to_integral_value(rounding=rounding) * tick)
    stop = inward(stop, ROUND_CEILING if side == 'long' else ROUND_FLOOR)
    target = inward(target, ROUND_FLOOR if side == 'long' else ROUND_CEILING)
    bound_prices(side, entry, stop, target)  # Direction may collapse at coarse ticks.
    return stop, target
