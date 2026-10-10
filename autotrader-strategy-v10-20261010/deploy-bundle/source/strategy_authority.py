"""Explicit group strategy authority; execution/protection remains deterministic."""
import math
import functools
import threading
from adaptive_exit_engine import normalize_ai_price_plan


def core_ai(cfg):
    return getattr(cfg, 'CORE_AI_STRATEGY_AUTHORITY', False) is True


def candidate_chart(cfg):
    return getattr(cfg, 'CANDIDATE_C_CHART_ONLY', False) is True


def core_price_policy(cfg, policy, *, entry=None, atr=None):
    result = dict(policy)
    if core_ai(cfg):
        # Same noise floor as the qualified native execution boundary. The AI
        # selects prices; the former 1.5ATR strategy prior is not a veto.
        minimum = .5
        if entry is not None and atr is not None and float(atr) > 0:
            minimum = max(minimum, float(entry) * .002 / float(atr))
        result['initial_atr_min'] = minimum
    return result


def select_core_prices(decision, result, gate):
    plan = normalize_ai_price_plan((decision or {}).get('exit_plan'))
    if not plan:
        return None, 'core_gemini_exit_plan_missing_or_invalid'
    if gate == 'TIMEOUT_BYPASS':
        return plan, 'gemini_confirmed_timeout'
    verdict = (result or {}).get('exit_plan_decision')
    if gate == 'approved' and verdict == 'approve':
        return plan, 'gemini_approved'
    return None, ('core_gpt_exit_plan_rejected' if verdict == 'reject'
                  else 'core_gpt_exit_plan_not_approved')


def held_review_valid(cfg, review):
    confidence = (review or {}).get('confidence')
    return ((review or {}).get('assessment') in ('thesis_intact', 'weakening', 'invalidated')
            and not isinstance(confidence, bool) and isinstance(confidence, (int, float))
            and math.isfinite(confidence) and getattr(cfg, 'MIN_CONFIDENCE', .6) <= confidence <= 1)


_review_locks = {}
_review_locks_guard = threading.Lock()


def single_review(function):
    @functools.wraps(function)
    def wrapped(cfg, state, client, symbol, *args, **kwargs):
        if not core_ai(cfg):
            return function(cfg, state, client, symbol, *args, **kwargs)
        with _review_locks_guard:
            lock = _review_locks.setdefault((cfg.user_dir, symbol), threading.Lock())
        if not lock.acquire(blocking=False):
            return None
        try:
            return function(cfg, state, client, symbol, *args, **kwargs)
        finally:
            lock.release()
    return wrapped


def bounded_add_coin(cfg, position, *, equity, price, stop, proposed, contract_size):
    """Bound added exposure using the actual unchanged Gemini stop."""
    try:
        values = [equity, price, stop, proposed, contract_size, position['entry_price'], position['contracts']]
        if any(not math.isfinite(float(x)) or float(x) <= 0 for x in values):
            return 0.
        sign = 1 if position['side'] == 'long' else -1
        distance = sign * (price - stop)
        leverage = float(cfg.LEVERAGE)
        if distance <= 0 or leverage * distance / price * 100 > 20 + 1e-9:
            return 0.
        risk_pct = min(float(getattr(cfg,'RISK_PER_TRADE_PCT',1)), float(cfg.MAX_DAILY_LOSS_PCT))
        budget = equity * risk_pct / 100
        existing = position['contracts'] * contract_size * max(0., sign * (position['entry_price'] - stop))
        cost = .001 + (float(getattr(cfg,'SPREAD_BPS',0) or 0) + float(getattr(cfg,'SLIPPAGE_BPS',0) or 0)) * .0002
        remaining = max(0., budget - existing - position['contracts'] * contract_size * price * cost)
        return min(proposed, remaining / (distance + price * cost))
    except (AttributeError, KeyError, TypeError, ValueError, ZeroDivisionError):
        return 0.
