"""Durable user-baseline epochs. No exchange access, no trading code.

History is saved in pnl_state.json in the SAME atomic replacement as a user
requested baseline reset. Old baselines can be imported, but missing historical
P&L is never backfilled or guessed.
"""
import datetime
import math
import os
import uuid

import pnl_store
import process_lock


def _strict_record(user_dir):
    record = process_lock.load_json_or_default(
        pnl_store._path(user_dir), dict, corrupted_error_prefix="baseline history")
    if not isinstance(record, dict):
        raise ValueError("baseline_state_not_object")
    return record

KST = pnl_store.KST
HISTORY_SCHEMA = 1


def _numeric(x):
    if type(x) not in (float, int):
        return None
    value = float(x)
    return value if math.isfinite(value) else None


def _iso_utc_to_kst(value):
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt.astimezone(KST).isoformat(timespec="seconds")
    except (ValueError, TypeError):
        return None


def _existing_epoch(rec):
    baseline = _numeric(rec.get("baseline_equity"))
    if baseline is None:
        return None
    date = rec.get("baseline_set_at")
    moment = _iso_utc_to_kst(rec.get("baseline_set_at_utc"))
    if moment is None and isinstance(date, str):
        moment = date  # date-only legacy metadata; do not invent exact time
    return {
        "id": "existing-baseline-" + str(rec.get("baseline_set_at_ms") or date or "unknown"),
        "started_at_kst": moment,
        "started_at_date": date,
        "start_equity_usdt": baseline,
        "ended_at_kst": None,
        "end_equity_usdt": None,
        "trade_contribution_usdt": None,
        "trade_return_pct": None,
        "net_capital_flow_usdt": None,
        "capital_in_usdt": None,
        "capital_out_usdt": None,
        "status": "current",
        "source": "imported_existing_baseline",
        "accounting_status": "not_yet_archived",
    }


def _closed_epoch(epoch, equity, now_kst, old_summary):
    close = dict(epoch, ended_at_kst=now_kst,
                 end_equity_usdt=equity, status="closed")
    complete = isinstance(old_summary, dict) and old_summary.get("complete") is True
    if complete:
        complete = all(_numeric(old_summary.get(k)) is not None for k in (
            "cashflow_adjusted_profit", "net_capital_flow_usdt"))
    if complete:
        close.update(
            trade_contribution_usdt=_numeric(old_summary["cashflow_adjusted_profit"]),
            trade_return_pct=_numeric(old_summary.get("cashflow_adjusted_return_pct")),
            net_capital_flow_usdt=_numeric(old_summary["net_capital_flow_usdt"]),
            capital_in_usdt=_numeric(old_summary.get("capital_in_usdt")),
            capital_out_usdt=_numeric(old_summary.get("capital_out_usdt")),
            accounting_status="verified_cached_cashflow_summary",
        )
    else:
        close["accounting_status"] = "cashflow_unverified"
    return close


def load_history(user_dir):
    rec = _strict_record(user_dir)
    history = rec.get("baseline_history")
    if history is not None and not isinstance(history, list):
        raise ValueError("baseline_history_invalid")
    if isinstance(history, list):
        return [dict(e) for e in history if isinstance(e, dict)]
    existing = _existing_epoch(rec)
    return [existing] if existing else []


def reset_with_history(user_dir, equity, *, old_summary=None, event_id=None, now=None):
    """Archive previous period + store new baseline in one durable write.

    The supplied old_summary must be computed *before* the old baseline is
    replaced. No writes to capital-flow or trading records occur here.
    """
    equity = _numeric(equity)
    if equity is None or equity <= 0:
        raise ValueError("baseline_equity_invalid")
    if event_id is not None and (not isinstance(event_id, str) or
                                 not 1 <= len(event_id) <= 100 or
                                 not all(c.isalnum() or c in "-_" for c in event_id)):
        raise ValueError("invalid_reset_event_id")
    now = now or datetime.datetime.now(datetime.timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=datetime.timezone.utc)
    utc = now.astimezone(datetime.timezone.utc)
    local = utc.astimezone(KST)
    now_local = local.isoformat(timespec="seconds")
    reset_id = event_id or uuid.uuid4().hex
    with process_lock.locked(user_dir, "baseline_history"):
        rec = _strict_record(user_dir)
        history = rec.get("baseline_history")
        if history is None:
            original = _existing_epoch(rec)
            history = [original] if original else []
        elif not isinstance(history, list):
            raise ValueError("baseline_history_invalid")
        history = [dict(item) for item in history]
        for entry in history:
            if entry.get("reset_event_id") == reset_id:
                # Duplicate request; do not write an extra reset or new baseline.
                return {"already_applied": True, "entry": entry}
        if history and history[-1].get("status") == "current":
            history[-1] = _closed_epoch(history[-1], equity, now_local, old_summary)
        new = {
            "id": reset_id,
            "reset_event_id": reset_id,
            "started_at_kst": now_local,
            "started_at_date": local.date().isoformat(),
            "start_equity_usdt": equity,
            "ended_at_kst": None,
            "end_equity_usdt": None,
            "trade_contribution_usdt": None,
            "trade_return_pct": None,
            "net_capital_flow_usdt": None,
            "capital_in_usdt": None,
            "capital_out_usdt": None,
            "status": "current",
            "source": "user_reset",
            "accounting_status": "current",
        }
        history.append(new)
        rec.update({
            "schema_version": max(int(rec.get("schema_version") or 0),
                                  pnl_store.SCHEMA_VERSION),
            "baseline_equity": equity,
            "baseline_set_at": local.date().isoformat(),
            "baseline_set_at_utc": utc.isoformat().replace("+00:00", "Z"),
            "baseline_set_at_ms": int(utc.timestamp()*1000),
            "baseline_time_source": "reset_event",
            "baseline_history_schema_version": HISTORY_SCHEMA,
            "baseline_history": history,
        })
        process_lock.save_json_atomic(pnl_store._path(user_dir), rec)
        return {"already_applied": False, "entry": new}
