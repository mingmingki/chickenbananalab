"""Durable OKX actual-fee ledger for reporting.

Source of truth is OKX account bill type=2 (trade).  This is intentionally
separate from per-trade fee attribution used by realized-PnL statistics:
actual exchange fees include entry fills for still-open positions and partial
reductions that are not closed trades.
"""
from __future__ import annotations

import datetime
import json
import math
import os
import time

import ccxt

import pnl_reconciliation
import process_lock

STATE_FILE = "exchange_fee_state.json"
LOCK_KEY = "exchange_fee_state"
SCHEMA_VERSION = 1
PAGE_LIMIT = 100
MAX_PAGES = 60
PAGE_DELAY_SECONDS = 0.30
REFRESH_INTERVAL_SECONDS = 300
ARCHIVE_CATCHUP_GAP_MS = 5 * 24 * 60 * 60 * 1000
KST = datetime.timezone(datetime.timedelta(hours=9))


def _path(user_dir: str) -> str:
    return os.path.join(user_dir, STATE_FILE)


def _read(user_dir: str) -> dict:
    try:
        with open(_path(user_dir), encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return {}
    if not isinstance(data, dict):
        raise ValueError("exchange fee state must be an object")
    return data


def _save(user_dir: str, state: dict) -> None:
    process_lock.save_json_atomic(_path(user_dir), state)


def _parse_kst_naive(value: str | None) -> datetime.datetime | None:
    if not value:
        return None
    try:
        dt = datetime.datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(KST).replace(tzinfo=None)
    return dt


def derive_start_ms(user_dir: str) -> int | None:
    """Midnight KST of first recorded bot trade day.

    Using midnight instead of first close time captures the entry fee of the
    first trade, even when old records do not contain an entry timestamp.
    """
    records = pnl_reconciliation.load_all_records(user_dir)
    times = [_parse_kst_naive(r.get("time")) for r in records]
    times = [dt for dt in times if dt is not None]
    if not times:
        return None
    day = min(times).date()
    return int(datetime.datetime.combine(day, datetime.time.min, tzinfo=KST).timestamp() * 1000)


def _finite(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def normalize_bill(raw: dict) -> dict | None:
    if str(raw.get("type")) != "2":
        return None
    bill_id = str(raw.get("billId") or "")
    ts_ms = int(raw.get("ts") or 0)
    fee = _finite(raw.get("fee"))
    ccy = str(raw.get("ccy") or "")
    inst_type = str(raw.get("instType") or "").upper()
    if not bill_id or ts_ms <= 0 or fee is None or not ccy or not inst_type:
        return None
    return {
        "bill_id": bill_id,
        "ts_ms": ts_ms,
        "inst_type": inst_type,
        "inst_id": str(raw.get("instId") or ""),
        "ccy": ccy,
        "fee_amount": fee,
        "sub_type": str(raw.get("subType") or ""),
        "fee_usdt": None,
        "valuation_source": None,
        "valuation_error": None,
    }


def _historical_usdt_price(exchange, ccy: str, ts_ms: int) -> tuple[float, str]:
    if ccy == "USDT":
        return 1.0, "native_usdt"
    symbol = f"{ccy}/USDT"
    rows = exchange.fetch_ohlcv(symbol, "1m", since=max(0, ts_ms - 120_000), limit=5)
    if not rows:
        raise ValueError("historical_fee_price_unavailable")
    before = [r for r in rows if r and len(r) >= 5 and int(r[0]) <= ts_ms]
    row = max(before, key=lambda r: int(r[0])) if before else min(rows, key=lambda r: abs(int(r[0])-ts_ms))
    px = _finite(row[4])
    if px is None or px <= 0:
        raise ValueError("historical_fee_price_invalid")
    return px, f"{symbol}@1m_close"


def value_event(exchange, event: dict) -> dict:
    event = dict(event)
    try:
        px, source = _historical_usdt_price(exchange, event["ccy"], event["ts_ms"])
        # OKX costs are normally negative fee amounts. Positive fee is a rebate.
        event["fee_usdt"] = -event["fee_amount"] * px
        event["valuation_source"] = source
        event["valuation_error"] = None
    except Exception as exc:
        event["fee_usdt"] = None
        event["valuation_source"] = None
        event["valuation_error"] = f"{type(exc).__name__}: {exc}"[:300]
    return event


def _call_with_retry(method, params):
    delays = (0.0, 0.75, 1.5, 3.0)
    last = None
    for delay in delays:
        if delay:
            time.sleep(delay)
        try:
            return method(params)
        except ccxt.RateLimitExceeded as exc:
            last = exc
            continue
    raise last


def _fetch_pages(method, *, start_ms: int, stop_on_known: set[str] | None = None) -> list[dict]:
    out = []
    seen_page_ids = set()
    cursor = None
    for _ in range(MAX_PAGES):
        params = {"type": "2", "limit": str(PAGE_LIMIT)}
        if cursor:
            params["after"] = cursor
        response = _call_with_retry(method, params) or {}
        rows = response.get("data") or []
        if not rows:
            break

        hit_known = False
        for raw in rows:
            bill_id = str(raw.get("billId") or "")
            ts_ms = int(raw.get("ts") or 0)
            if stop_on_known and bill_id in stop_on_known:
                hit_known = True
            if bill_id and bill_id not in seen_page_ids and ts_ms >= start_ms:
                seen_page_ids.add(bill_id)
                out.append(raw)

        oldest_ts = min(int(r.get("ts") or 0) for r in rows)
        if hit_known or oldest_ts < start_ms or len(rows) < PAGE_LIMIT:
            break
        next_cursor = str(rows[-1].get("billId") or "")
        if not next_cursor or next_cursor == cursor:
            break
        cursor = next_cursor
        time.sleep(PAGE_DELAY_SECONDS)
    return out


def compute_summary(events: list[dict], *, start_ms: int, last_refresh_ms: int | None, complete=True, last_error=None) -> dict:
    valued = [e for e in events if _finite(e.get("fee_usdt")) is not None and int(e.get("ts_ms") or 0) >= start_ms]
    unvalued = [e for e in events if _finite(e.get("fee_usdt")) is None and int(e.get("ts_ms") or 0) >= start_ms]
    by_type = {}
    for e in valued:
        inst = e.get("inst_type") or "OTHER"
        by_type[inst] = by_type.get(inst, 0.0) + float(e["fee_usdt"])
    swap = by_type.get("SWAP", 0.0)
    spot = by_type.get("SPOT", 0.0)
    other = sum(v for k,v in by_type.items() if k not in ("SWAP","SPOT"))
    return {
        "schema_version": SCHEMA_VERSION,
        "complete": bool(complete and not unvalued),
        "last_error": last_error,
        "start_ms": int(start_ms),
        "last_refresh_ms": last_refresh_ms,
        "event_count": len(valued),
        "unvalued_count": len(unvalued),
        "swap_fee_usdt": swap,
        "spot_fee_usdt": spot,
        "other_fee_usdt": other,
        "total_fee_usdt": swap + spot + other,
        "by_inst_type": by_type,
    }


def _new_state(start_ms: int) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "start_ms": int(start_ms),
        "events": [],
        "backfill_complete": False,
        "last_refresh_ms": None,
        "last_error": None,
    }


def _event_map(events):
    return {str(e.get("bill_id")): dict(e) for e in events if e.get("bill_id")}


def refresh(user_dir: str, exchange, *, start_ms: int | None = None, now_ms: int | None = None) -> dict:
    now_ms = int(time.time()*1000) if now_ms is None else int(now_ms)
    if start_ms is None:
        start_ms = derive_start_ms(user_dir)
    if start_ms is None:
        return {
            "schema_version": SCHEMA_VERSION, "complete": False,
            "last_error": "fee_coverage_start_unknown", "start_ms": None,
            "last_refresh_ms": None, "event_count": 0, "unvalued_count": 0,
            "swap_fee_usdt": 0.0, "spot_fee_usdt": 0.0,
            "other_fee_usdt": 0.0, "total_fee_usdt": 0.0,
            "by_inst_type": {},
        }

    with process_lock.locked(user_dir, LOCK_KEY):
        state = _read(user_dir)
        if state.get("start_ms") != int(start_ms):
            state = _new_state(start_ms)
        events = _event_map(state.get("events") or [])
        error = None
        complete = True
        try:
            if not state.get("backfill_complete"):
                raw_rows = _fetch_pages(
                    exchange.private_get_account_bills_archive,
                    start_ms=int(start_ms),
                )
                state["backfill_complete"] = True
            else:
                last_refresh = int(state.get("last_refresh_ms") or 0)
                method = (
                    exchange.private_get_account_bills_archive
                    if not last_refresh or now_ms - last_refresh > ARCHIVE_CATCHUP_GAP_MS
                    else exchange.private_get_account_bills
                )
                raw_rows = _fetch_pages(
                    method,
                    start_ms=int(start_ms),
                    stop_on_known=set(events),
                )
            for raw in raw_rows:
                event = normalize_bill(raw)
                if event is None:
                    continue
                old = events.get(event["bill_id"])
                if old and _finite(old.get("fee_usdt")) is not None:
                    continue
                events[event["bill_id"]] = value_event(exchange, event)
        except Exception as exc:
            complete = False
            error = f"{type(exc).__name__}: {exc}"[:500]

        state["events"] = sorted(events.values(), key=lambda e:(int(e["ts_ms"]), e["bill_id"]))
        state["last_refresh_ms"] = now_ms
        state["last_error"] = error
        _save(user_dir, state)
        return compute_summary(
            state["events"], start_ms=int(start_ms), last_refresh_ms=now_ms,
            complete=complete, last_error=error,
        )


def cached_summary(user_dir: str, *, start_ms: int | None = None) -> dict:
    with process_lock.locked(user_dir, LOCK_KEY):
        state = _read(user_dir)
        if start_ms is None and state.get("start_ms") is not None:
            start_ms = int(state["start_ms"])
        if start_ms is None:
            start_ms = derive_start_ms(user_dir)
        if start_ms is None:
            return compute_summary([], start_ms=0, last_refresh_ms=None, complete=False, last_error="fee_coverage_start_unknown")
        if state.get("start_ms") != int(start_ms):
            return compute_summary([], start_ms=int(start_ms), last_refresh_ms=None, complete=False, last_error="fee_ledger_not_initialized")
        return compute_summary(
            state.get("events") or [], start_ms=int(start_ms),
            last_refresh_ms=state.get("last_refresh_ms"),
            complete=not bool(state.get("last_error")) and bool(state.get("backfill_complete")),
            last_error=state.get("last_error"),
        )


def load_state(user_dir: str) -> dict:
    with process_lock.locked(user_dir, LOCK_KEY):
        return _read(user_dir)
