"""CORE-only opt-in 4h confirmed-trend admission and discretionary exit suppression.

No exchange calls, sizing changes, stop widening, or Candidate C effects.
"""
import datetime as dt
import math


def _num(x):
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _as_utc(value):
    if hasattr(value, "to_pydatetime"):
        value = value.to_pydatetime()
    if not isinstance(value, dt.datetime):
        try:
            value = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=dt.timezone.utc)
    return value.astimezone(dt.timezone.utc)


def assess_entry(side, closed_4h, *, as_of=None, min_strength_atr=0.0):
    """Fail closed for missing/stale final bars; no rule based on future/live candle."""
    base = dict(allowed=False, side=side, timeframe="4h", trend="unknown")
    if side not in ("long", "short"):
        return dict(base, reason="trend_v33_106_invalid_side")
    if closed_4h is None or len(closed_4h) < 55:
        return dict(base, reason="trend_v33_106_4h_history_missing")
    try:
        last = closed_4h.iloc[-1]
        previous = closed_4h.iloc[-4]
        ts = _as_utc(last["timestamp"])
        now = _as_utc(as_of) if as_of is not None else dt.datetime.now(dt.timezone.utc)
        values = dict(close=_num(last["close"]), ema20=_num(last["ema_20"]),
                      ema50=_num(last["ema_50"]), ema20_prior=_num(previous["ema_20"]))
    except (KeyError, TypeError, ValueError, IndexError, AttributeError):
        return dict(base, reason="trend_v33_106_invalid_4h_data")
    if ts is None or now is None or any(v is None or v <= 0 for v in values.values()):
        return dict(base, reason="trend_v33_106_invalid_4h_data")
    close_at = ts + dt.timedelta(hours=4)
    age = (now-close_at).total_seconds()
    if age < 3 or age > 8*3600:
        return dict(base, reason="trend_v33_106_4h_not_final_or_stale", bar_open=ts.isoformat())
    v=values
    bullish = v["ema20"] > v["ema50"] and v["ema20"] > v["ema20_prior"] and v["close"] >= v["ema20"]
    bearish = v["ema20"] < v["ema50"] and v["ema20"] < v["ema20_prior"] and v["close"] <= v["ema20"]
    trend = "bullish" if bullish else "bearish" if bearish else "transition"
    permitted = (side=="long" and bullish) or (side=="short" and bearish)
    # v33-108: optional confirmed-4H trend strength; fail closed on missing ATR.
    # The default 0.0 preserves v33-106 exactly, including the exit policy.
    atr = _num(last.get("atr_14"))
    strength = (abs(v["ema20"] - v["ema50"]) / atr
                if atr is not None and atr > 0 else None)
    if min_strength_atr > 0 and permitted and (
            strength is None or strength < min_strength_atr):
        permitted = False
        reason = ("trend_v33_108_4h_atr_missing" if strength is None
                  else "trend_v33_108_strength_below_05atr")
    else:
        reason = ("trend_v33_106_aligned" if permitted
                  else "trend_v33_106_countertrend_or_transition")
    return dict(base, allowed=permitted, trend=trend, reason=reason,
                strength_atr=strength, minimum_strength_atr=min_strength_atr,
                bar_open=ts.isoformat(), **values)


def ai_action_allowed(action, review_path, close_guard):
    """Keep actual exchange SL/TP and emergency reviews; suppress routine AI churn."""
    if action not in ("CLOSE_ALL", "REDUCE_50", "ADD_POSITION"):
        return True, "not_discretionary_exit"
    if action == "ADD_POSITION":
        return False, "trend_v33_106_no_ai_add"
    if not isinstance(close_guard, dict):
        return False, "trend_v33_106_no_emergency_evidence"
    reason = close_guard.get("reason")
    loss = _num(close_guard.get("loss_r"))
    if reason in ("deterministic_breakdown", "risk_inputs_unavailable_fail_safe"):
        return True, "trend_v33_106_emergency_" + reason
    if loss is not None and loss >= 0.75:
        return True, "trend_v33_106_emergency_loss_075r"
    # Preserve negative_guard's confirmed adverse stage at 0.35R,
    # but never let an ordinary profit review close or reduce simply on MFE.
    if review_path == "negative_guard" and loss is not None and loss >= 0.35:
        return True, "trend_v33_106_negative_guard"
    return False, "trend_v33_106_routine_ai_exit_suppressed"
