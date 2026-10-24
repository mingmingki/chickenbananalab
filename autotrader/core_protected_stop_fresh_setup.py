"""Protected-exit freshness: legacy setup origin or GPT-primary closed bar.

GPT primary reviews continuation on a new confirmed 5m bar after the exit.
Legacy providers retain the original 30-minute and new-setup policy.
"""
from datetime import datetime, timedelta, timezone
import math
from core_profit_stop_reentry import verified_favorable_stop

UTC=timezone.utc

def instant(value,*,assume_utc=False):
    try:
        if isinstance(value, datetime):
            d=value
        else:
            d=datetime.fromisoformat(str(value))
        if d.tzinfo is None:
            d=d.replace(tzinfo=UTC if assume_utc else timezone(timedelta(hours=9)))
        return d.astimezone(UTC)
    except (ValueError,TypeError,OverflowError):
        return None

def check(row, side, timing, *, now=None, primary_decision=False, bar_closed_at=None):
    """Return (allowed, reason).

    Only apply to an OKX-proven favorable stop of the SAME side within 24h.
    A new confirmed entry setup must originate strictly after the exit.
    """
    if not verified_favorable_stop(row) or side!=row.get('side'):
        return True,'not_applicable'
    exit_at=instant(row.get('time'))
    clock=instant(now) if now is not None else datetime.now(UTC)
    if exit_at is None or clock is None:
        return False,'exit_time_unverified'
    elapsed=(clock-exit_at).total_seconds()
    if elapsed<0:
        return False,'exit_time_in_future'
    if elapsed>24*3600:
        return True,'previous_exit_expired'
    if primary_decision is True:
        # The GPT must see a new completed 5m observation after the exact
        # owned protection close. A fresh bar is different from requiring
        # a brand-new trend origin, which suppresses valid continuation.
        if type(bar_closed_at) not in (int, float) or not math.isfinite(bar_closed_at):
            return False,'primary_confirmed_bar_missing'
        if abs(bar_closed_at % 300) > 1e-6:
            return False,'primary_confirmed_bar_not_5m_boundary'
        if bar_closed_at <= exit_at.timestamp():
            return False,'primary_confirmed_bar_precedes_exit'
        age = clock.timestamp() - bar_closed_at
        if not 0 <= age <= 360:
            return False,'primary_confirmed_bar_stale_or_future'
        return True,'gpt_primary_new_closed_bar_after_exit'
    if not isinstance(timing,dict):
        return False,'fresh_setup_missing'
    if timing.get('side')!=side or timing.get('phase') not in ('early','established'):
        return False,'fresh_setup_wrong_direction_or_phase'
    origin=instant(timing.get('origin_closed_at'),assume_utc=True)
    if origin is None:
        return False,'fresh_setup_origin_missing'
    if origin<=exit_at:
        return False,'fresh_setup_precedes_protected_exit'
    if origin>clock+timedelta(seconds=5):
        return False,'fresh_setup_not_yet_confirmed'
    return True,'new_confirmed_setup_after_exit'
