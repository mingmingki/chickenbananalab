"""Shared capital-protection policy for every live symbol.

Signal generation stays strategy-specific.  Entry timing, anti-chase, pilot sizing,
profit protection and hard-loss thresholds are intentionally shared by CORE and
Candidate C so a defect fixed for one symbol cannot remain live on another.
"""
from __future__ import annotations

import math

CHASE_30M_PCT = 0.50
LATE_ENTRY_MOVE_ATR = 1.25
MEANINGFUL_PULLBACK_ATR = 0.50
PILOT_ENTRY_FRACTION = 0.50
PROFIT_PROTECT_R = 0.50
PROFIT_LOCK_R = 0.75
HARD_LOSS_R = 0.75
MFE_ARM_R = 0.50
MFE_GIVEBACK_R = 0.25
BREAKEVEN_FEE_PCT = 0.10
STRUCTURE_PROFIT_ARM_R = 0.75


def _finite(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def evaluate_recent_entry(*, side: str, current_price: float,
                          reference_30m_price: float | None = None,
                          move_30m_atr: float | None = None,
                          pullback_atr: float | None = None) -> dict:
    """Symmetric late-entry/chase guard with pullback re-arm.

    A confirmed meaningful pullback re-arms an otherwise stale move; this prevents
    chasing the first extension without permanently banning a healthy pullback and
    continuation during the same 30-minute window.
    """
    if side not in ("long", "short") or not _finite(current_price) or float(current_price) <= 0:
        return {"allowed": False, "reason": "invalid_recent_entry_context"}
    meaningful_pullback = bool(
        _finite(pullback_atr) and (float(pullback_atr) >= MEANINGFUL_PULLBACK_ATR
        or math.isclose(float(pullback_atr), MEANINGFUL_PULLBACK_ATR, abs_tol=1e-9))
    )
    pct = None
    if _finite(reference_30m_price) and float(reference_30m_price) > 0:
        ref = float(reference_30m_price)
        cur = float(current_price)
        pct = ((cur - ref) / ref * 100.0) if side == "long" else ((ref - cur) / ref * 100.0)
        pct = max(0.0, pct)
        if pct >= CHASE_30M_PCT or math.isclose(pct, CHASE_30M_PCT, abs_tol=1e-9):
            return {
                "allowed": False,
                "reason": "long_chase_30m_rise" if side == "long" else "short_chase_30m_drop",
                "directional_30m_pct": pct,
                "meaningful_pullback": False,
            }
    if _finite(move_30m_atr) and (float(move_30m_atr) >= LATE_ENTRY_MOVE_ATR or math.isclose(float(move_30m_atr), LATE_ENTRY_MOVE_ATR, abs_tol=1e-9)) and not meaningful_pullback:
        return {
            "allowed": False, "reason": "entry_late_exhaustion_no_pullback",
            "directional_30m_pct": pct, "meaningful_pullback": False,
        }
    return {
        "allowed": True, "reason": "ok", "directional_30m_pct": pct,
        "meaningful_pullback": meaningful_pullback,
    }


def entry_size_fraction(*, side: str, primary_direction: str,
                        direction_1h: str, direction_5m: str) -> float:
    wanted = "LONG" if side == "long" else "SHORT" if side == "short" else None
    if wanted is None:
        return 0.0
    if primary_direction == wanted:
        return 1.0
    if primary_direction == "NONE" and direction_1h == wanted and direction_5m == wanted:
        return PILOT_ENTRY_FRACTION
    return 0.0


def profit_protect_eligible(profit_r: float | None, *, weakening: bool) -> bool:
    return bool(_finite(profit_r) and float(profit_r) >= PROFIT_PROTECT_R and weakening)


def profit_lock_eligible(profit_r: float | None, *, weakening: bool, extreme: bool) -> bool:
    return bool(_finite(profit_r) and float(profit_r) >= PROFIT_LOCK_R and (weakening or extreme))


def hard_loss_eligible(loss_r: float | None, *, invalidated: bool) -> bool:
    return bool(_finite(loss_r) and float(loss_r) >= HARD_LOSS_R and invalidated)


def profit_floor_eligible(mfe_r: float | None, *, current_r: float | None = None,
                          risk_reduced: bool = False) -> bool:
    ordinary_lock = bool(
        _finite(mfe_r) and (
            float(mfe_r) >= PROFIT_LOCK_R
            or math.isclose(float(mfe_r), PROFIT_LOCK_R, abs_tol=1e-9)
        )
    )
    # Once exposure has been reduced, an armed giveback must also protect the
    # remainder. Reuse the existing +0.50R / 0.25R giveback policy.
    return ordinary_lock or bool(risk_reduced and mfe_giveback_eligible(mfe_r, current_r))


def mfe_giveback_eligible(mfe_r: float | None, current_r: float | None) -> bool:
    if not (_finite(mfe_r) and _finite(current_r)):
        return False
    mfe = float(mfe_r)
    current = float(current_r)
    giveback = max(0.0, mfe - current)
    return bool(
        current > 0.0
        and (mfe >= MFE_ARM_R or math.isclose(mfe, MFE_ARM_R, abs_tol=1e-9))
        and (giveback >= MFE_GIVEBACK_R or math.isclose(giveback, MFE_GIVEBACK_R, abs_tol=1e-9))
    )


def profit_structure_protection(*, side: str, current_r: float | None,
                                current_5m: dict | None, previous_5m: dict | None,
                                one_h: dict | None, already_reduced: bool) -> dict:
    """Shared profitable-position structure defense for all six symbols.

    It only acts after +0.75R is still present. A confirmed 1H adverse swing break is
    immediate; 5m needs two consecutive confirmed adverse breaks to filter noise.
    """
    if already_reduced:
        return {"action": "none", "reason": "already_reduced"}
    if side not in ("long", "short") or not _finite(current_r):
        return {"action": "none", "reason": "invalid_context"}
    if float(current_r) + 1e-9 < STRUCTURE_PROFIT_ARM_R:
        return {"action": "none", "reason": "profit_not_armed"}
    adverse_key = "swing_low_broken" if side == "long" else "swing_high_broken"
    if bool((one_h or {}).get(adverse_key)):
        return {"action": "reduce_25", "reason": "confirmed_1h_structure_break"}
    if bool((current_5m or {}).get(adverse_key)) and bool((previous_5m or {}).get(adverse_key)):
        return {"action": "reduce_25", "reason": "confirmed_5m_structure_break"}
    return {"action": "none", "reason": "structure_intact"}


def entry_risk_score(*, symbol: str, side: str, hour_kst: int, tf_mixed: bool) -> dict:
    factors = []
    if isinstance(hour_kst, int) and 12 <= hour_kst <= 17:
        factors.append("weak_hours_12_17_kst")
    base = str(symbol or "").split("/")[0].upper()
    if base == "PI":
        factors.append("pi")
    if str(side).lower() == "short":
        factors.append("short")
    if bool(tf_mixed):
        factors.append("tf_mixed")
    return {"score": len(factors), "factors": factors}


def entry_risk_policy(score: int) -> dict:
    try:
        value = max(0, int(score))
    except (TypeError, ValueError):
        value = 3
    if value == 0:
        return {"size_fraction": 1.0, "require_strong_confirmation": False, "blocked": False}
    if value == 1:
        return {"size_fraction": 0.5, "require_strong_confirmation": False, "blocked": False}
    if value == 2:
        return {"size_fraction": 0.5, "require_strong_confirmation": True, "blocked": False}
    return {"size_fraction": 0.0, "require_strong_confirmation": True, "blocked": True}


def breakeven_floor_price(side: str, entry_price: float, fee_pct: float = BREAKEVEN_FEE_PCT) -> float:
    if side not in ('long', 'short') or not _finite(entry_price) or float(entry_price) <= 0:
        raise ValueError('invalid_breakeven_context')
    if not _finite(fee_pct) or float(fee_pct) < 0:
        raise ValueError('invalid_fee_pct')
    sign = 1.0 if side == 'long' else -1.0
    return float(entry_price) * (1.0 + sign * float(fee_pct) / 100.0)


def recent_entry_metrics(*, side: str, current_price: float, closes: list[float], atr: float) -> dict:
    if side not in ('long', 'short') or len(closes) < 7 or not _finite(current_price) or not _finite(atr) or float(atr) <= 0:
        return {'reference_30m_price': None, 'move_30m_atr': None, 'pullback_atr': None}
    values = [float(v) for v in closes[-7:]]
    if any(not math.isfinite(v) or v <= 0 for v in values):
        return {'reference_30m_price': None, 'move_30m_atr': None, 'pullback_atr': None}
    sign = 1.0 if side == 'long' else -1.0
    cur = float(current_price)
    atr_value = float(atr)
    move = sign * (cur - values[0]) / atr_value
    peak = sign * values[-4]
    pullback = 0.0
    for close in values[-3:]:
        directional_close = sign * close
        pullback = max(pullback, (peak - directional_close) / atr_value)
        peak = max(peak, directional_close)
    return {
        'reference_30m_price': values[0],
        'move_30m_atr': move,
        'pullback_atr': pullback,
    }
