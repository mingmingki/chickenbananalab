"""Current-lifecycle evidence shared by held-position AIs; zero order authority."""
import datetime as dt
import json
import math


def _number(value):
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def build(symbol, position, opened, peak, reduced, *, now=None):
    out = {"basis": "current_lifecycle_observed_evidence",
           "order_authority": False, "mfe_known": False, "reduction_known": False}
    position, opened, peak, reduced = (x or {} for x in (position, opened, peak, reduced))
    side = position.get("side")
    entry, mark = _number(position.get("entry_price")), _number(position.get("mark_price"))
    quantity = _number(position.get("contracts"))
    if side not in ("long", "short") or entry is None or entry <= 0 or quantity is None or quantity <= 0:
        return out
    out.update(side=side, remaining_contracts=quantity)
    identity = "journal:%s|%s|%s" % (symbol, opened.get("side"), opened.get("time"))
    recorded_entry = _number(opened.get("price", opened.get("entry_price")))
    same_open = bool(opened.get("time") and opened.get("side") == side
                     and recorded_entry is not None and abs(recorded_entry-entry)/entry <= 1e-6)
    try:
        stamp = dt.datetime.fromisoformat(str(peak.get("last_bar_time")).replace("Z", "+00:00"))
        stamp = stamp.replace(tzinfo=dt.timezone.utc) if stamp.tzinfo is None else stamp.astimezone(dt.timezone.utc)
        clock = now or dt.datetime.now(dt.timezone.utc)
        clock = clock.replace(tzinfo=dt.timezone.utc) if clock.tzinfo is None else clock.astimezone(dt.timezone.utc)
        age = (clock - stamp - dt.timedelta(minutes=1)).total_seconds()
        fresh = 0 <= age <= 360
    except (ValueError, TypeError):
        fresh = False
    initial_r, mfe_r = _number(peak.get("initial_r")), _number(peak.get("mfe_r"))
    peak_entry = _number(peak.get("entry_price"))
    entered = _number(position.get("entry_timestamp_ms"))
    peak_entered = _number(peak.get("entry_timestamp_ms"))
    if (same_open and entered is not None and entered > 0 and entered == peak_entered and peak.get("position_identity") == identity and peak.get("side") == side
            and peak_entry is not None and abs(peak_entry-entry)/entry <= 1e-6
            and fresh and initial_r is not None and initial_r > 0
            and mfe_r is not None and mfe_r >= 0 and mark is not None and mark > 0):
        current_r = (mark-entry)*(1 if side == "long" else -1)/initial_r
        out.update(mfe_known=True, initial_r=initial_r, mfe_r=mfe_r,
                   current_r=round(current_r,6), giveback_r=round(max(0.,mfe_r-current_r),6),
                   evidence_bar_time=peak.get("last_bar_time"))
    live_id = "%s:%s:%s" % (position.get("position_id"), position.get("entry_timestamp_ms"), side)
    ratio = _number(reduced.get("cumulative_reduced_ratio"))
    initial = _number(reduced.get("initial_contracts"))
    actual_reduced = _number(reduced.get("actual_reduced_contracts"))
    consistent_reduction = bool(
        reduced.get("baseline_known") is True and initial is not None and initial > 0
        and actual_reduced is not None and 0 <= actual_reduced <= initial
        and abs(quantity+actual_reduced-initial) <= max(1e-8, initial*1e-8)
        and ratio is not None and abs(ratio-actual_reduced/initial) <= 1e-8)
    if (position.get("position_id") and position.get("entry_timestamp_ms") is not None
            and reduced.get("lifecycle_id") == live_id and consistent_reduction):
        out.update(reduction_known=True, cumulative_reduced_ratio=ratio,
                   reduce_stage=reduced.get("reduce_stage"),
                   pending_quantity_change=bool(reduced.get("pending_order")),
                   last_reduction_order_time=reduced.get("last_reduction_order_time"))
    return out


def render(evidence):
    return ("\n[POSITION_MANAGEMENT_EVIDENCE]\n"
            + json.dumps(evidence, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
            + "\nConsider observed peak/giveback, prior reductions and confirmed structure together. "
              "Unknown evidence is not zero. Respect existing order and protection guards.\n")
