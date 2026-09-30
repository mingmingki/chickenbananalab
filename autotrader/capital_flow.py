"""Cash-flow-adjusted account performance for OKX Trading Account.

Only OKX account bill type=1 (transfer) is treated as external capital flow.
Trade/funding/fees remain investment performance. Non-USDT transfers are valued
at the transfer-time 1m close of CCY/USDT. Unknown valuation fails closed.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import math
import os
import time

import process_lock

SCHEMA_VERSION = 1
TRANSFER_TYPE = "1"
KST = datetime.timezone(datetime.timedelta(hours=9))
STATE_FILE = "capital_flow_state.json"
LOCK_KEY = "capital_flow_state"
MAX_PAGES = 20
PAGE_LIMIT = 100


def _path(user_dir: str) -> str:
    return os.path.join(user_dir, STATE_FILE)


def _read_state(user_dir: str) -> dict:
    try:
        with open(_path(user_dir), encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return {}
    if not isinstance(data, dict):
        raise ValueError("capital flow state must be an object")
    return data


def _save_state(user_dir: str, data: dict) -> None:
    process_lock.save_json_atomic(_path(user_dir), data)


def _baseline_key(baseline_equity: float, baseline_meta: dict) -> str:
    payload = {
        "equity": float(baseline_equity),
        "date": baseline_meta.get("baseline_set_at"),
        "exact_ms": baseline_meta.get("baseline_set_at_ms")
            if baseline_meta.get("baseline_time_source") == "reset_event" else None,
        "source": baseline_meta.get("baseline_time_source") or "legacy_date_only",
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def tracking_window(baseline_meta: dict, *, fallback_now_ms: int | None = None) -> dict:
    source = baseline_meta.get("baseline_time_source")
    exact = baseline_meta.get("baseline_set_at_ms")
    if source == "reset_event" and isinstance(exact, (int, float)) and exact > 0:
        exact_ms = int(exact)
        return {
            "query_start_ms": exact_ms,
            "adjustment_start_ms": exact_ms,
            "boundary_policy": "exact_reset_timestamp",
        }

    date_text = baseline_meta.get("baseline_set_at")
    if date_text:
        day = datetime.date.fromisoformat(str(date_text))
        start = datetime.datetime.combine(day, datetime.time.min, tzinfo=KST)
        next_day = start + datetime.timedelta(days=1)
        return {
            "query_start_ms": int(start.timestamp() * 1000),
            "adjustment_start_ms": int(next_day.timestamp() * 1000),
            "boundary_policy": "legacy_date_only_exclude_baseline_day",
        }

    now_ms = int(time.time() * 1000) if fallback_now_ms is None else int(fallback_now_ms)
    return {
        "query_start_ms": now_ms,
        "adjustment_start_ms": now_ms,
        "boundary_policy": "tracking_from_first_observation_only",
    }


def _finite(value) -> float | None:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _normalize_bill(raw: dict) -> dict | None:
    if str(raw.get("type")) != TRANSFER_TYPE:
        return None
    bill_id = str(raw.get("billId") or "")
    ts_ms = int(raw.get("ts") or 0)
    amount = _finite(raw.get("balChg"))
    ccy = str(raw.get("ccy") or "")
    if not bill_id or ts_ms <= 0 or amount is None or not ccy:
        return None
    return {
        "bill_id": bill_id,
        "ts_ms": ts_ms,
        "ccy": ccy,
        "amount_ccy": amount,
        "from_account": raw.get("from"),
        "to_account": raw.get("to"),
        "sub_type": str(raw.get("subType") or ""),
        "notes": str(raw.get("notes") or ""),
        "value_usdt": None,
        "price_usdt": None,
        "valuation_source": None,
        "valuation_error": None,
    }


def _historical_usdt_price(exchange, ccy: str, ts_ms: int) -> tuple[float, str]:
    if ccy == "USDT":
        return 1.0, "native_usdt"
    symbol = f"{ccy}/USDT"
    rows = exchange.fetch_ohlcv(symbol, "1m", since=max(0, ts_ms - 120_000), limit=5)
    if not rows:
        raise ValueError("historical_price_unavailable")

    before = [row for row in rows if row and len(row) >= 5 and int(row[0]) <= ts_ms]
    row = max(before, key=lambda r: int(r[0])) if before else min(
        rows, key=lambda r: abs(int(r[0]) - ts_ms)
    )
    price = _finite(row[4])
    if price is None or price <= 0:
        raise ValueError("historical_price_invalid")
    return price, f"{symbol}@1m_close"


def _value_event(exchange, event: dict) -> dict:
    event = dict(event)
    try:
        price, source = _historical_usdt_price(exchange, event["ccy"], event["ts_ms"])
        event["price_usdt"] = price
        event["value_usdt"] = event["amount_ccy"] * price
        event["valuation_source"] = source
        event["valuation_error"] = None
    except Exception as exc:
        event["value_usdt"] = None
        event["price_usdt"] = None
        event["valuation_source"] = None
        event["valuation_error"] = f"{type(exc).__name__}: {exc}"[:300]
    return event


def _fetch_pages(method, lower_bound_ms: int) -> list[dict]:
    out, seen = [], set()
    cursor = None
    for _ in range(MAX_PAGES):
        params = {"type": TRANSFER_TYPE, "limit": str(PAGE_LIMIT)}
        if cursor:
            params["after"] = cursor
        response = method(params) or {}
        rows = response.get("data") or []
        if not rows:
            break
        fresh = []
        for row in rows:
            bill_id = str(row.get("billId") or "")
            if bill_id and bill_id not in seen:
                seen.add(bill_id)
                out.append(row)
                fresh.append(row)
        if not fresh or len(rows) < PAGE_LIMIT:
            break
        oldest_ts = min(int(r.get("ts") or 0) for r in rows)
        if oldest_ts and oldest_ts < lower_bound_ms:
            break
        next_cursor = str(rows[-1].get("billId") or "")
        if not next_cursor or next_cursor == cursor:
            break
        cursor = next_cursor
    return out


def fetch_transfer_bills(exchange, lower_bound_ms: int) -> list[dict]:
    raw = []
    for name in ("private_get_account_bills", "private_get_account_bills_archive"):
        method = getattr(exchange, name, None)
        if not callable(method):
            raise RuntimeError(f"{name}_unavailable")
        raw.extend(_fetch_pages(method, lower_bound_ms))
    deduped = {}
    for row in raw:
        bill_id = str(row.get("billId") or "")
        if bill_id:
            deduped[bill_id] = row
    return sorted(
        [row for row in deduped.values() if int(row.get("ts") or 0) >= lower_bound_ms],
        key=lambda row: int(row["ts"]),
    )


def _event_map(events: list[dict]) -> dict[str, dict]:
    return {str(e.get("bill_id")): dict(e) for e in events if e.get("bill_id")}


def _event_ts_ms(event: dict) -> int:
    return int(event.get("ts_ms") or event.get("ts") or 0)


def _included_events(events: list[dict], start_ms: int) -> list[dict]:
    return [e for e in events if _event_ts_ms(e) >= start_ms]


def _boundary_events(events: list[dict], query_start_ms: int, start_ms: int) -> list[dict]:
    return [
        e for e in events
        if query_start_ms <= _event_ts_ms(e) < start_ms
    ]


def compute_summary(
    *, baseline_equity: float, baseline_meta: dict, current_equity: float,
    events: list[dict], now_ms: int | None = None,
    observation_status: str = "KNOWN", last_error: str | None = None,
) -> dict:
    now_ms = int(time.time() * 1000) if now_ms is None else int(now_ms)
    baseline_equity = float(baseline_equity)
    current_equity = float(current_equity)
    window = tracking_window(baseline_meta, fallback_now_ms=now_ms)
    start_ms = window["adjustment_start_ms"]
    query_start_ms = window["query_start_ms"]

    included = _included_events(events, start_ms)
    boundary = _boundary_events(events, query_start_ms, start_ms)
    valued = [e for e in included if _finite(e.get("value_usdt")) is not None]
    unvalued = [e for e in included if _finite(e.get("value_usdt")) is None]

    values = [float(e["value_usdt"]) for e in valued]
    capital_in = sum(v for v in values if v > 0)
    capital_out = -sum(v for v in values if v < 0)
    net_flow = capital_in - capital_out
    boundary_values = [
        float(e["value_usdt"]) for e in boundary
        if _finite(e.get("value_usdt")) is not None
    ]
    boundary_net = sum(boundary_values)
    raw_profit = current_equity - baseline_equity

    complete = observation_status == "KNOWN" and not unvalued
    adjusted_profit = raw_profit - net_flow if complete else None

    adjusted_pct = None
    if complete:
        duration = max(1, now_ms - start_ms)
        weighted_capital = baseline_equity
        for event in valued:
            ts_ms = _event_ts_ms(event)
            weight = min(1.0, max(0.0, (now_ms - ts_ms) / duration))
            weighted_capital += weight * float(event["value_usdt"])
        if weighted_capital > 0:
            adjusted_pct = adjusted_profit / weighted_capital * 100.0

    return {
        "schema_version": SCHEMA_VERSION,
        "observation_status": observation_status,
        "complete": complete,
        "last_error": last_error,
        "boundary_policy": window["boundary_policy"],
        "query_start_ms": query_start_ms,
        "adjustment_start_ms": start_ms,
        "event_count": len(included),
        "unvalued_count": len(unvalued),
        "boundary_excluded_count": len(boundary),
        "boundary_excluded_net_usdt": boundary_net,
        "capital_in_usdt": capital_in,
        "capital_out_usdt": capital_out,
        "net_capital_flow_usdt": net_flow,
        "raw_total_profit": raw_profit,
        "raw_total_profit_pct": (
            raw_profit / baseline_equity * 100.0 if baseline_equity > 0 else None
        ),
        "cashflow_adjusted_profit": adjusted_profit,
        "cashflow_adjusted_return_pct": adjusted_pct,
        "return_method": (
            "modified_dietz"
            if window["boundary_policy"] == "exact_reset_timestamp"
            else "modified_dietz_legacy_boundary"
        ),
    }


def _new_state(baseline_equity: float, baseline_meta: dict, now_ms: int) -> dict:
    window = tracking_window(baseline_meta, fallback_now_ms=now_ms)
    return {
        "schema_version": SCHEMA_VERSION,
        "baseline_key": _baseline_key(baseline_equity, baseline_meta),
        "baseline_equity": float(baseline_equity),
        "baseline_meta": dict(baseline_meta),
        "query_start_ms": window["query_start_ms"],
        "adjustment_start_ms": window["adjustment_start_ms"],
        "boundary_policy": window["boundary_policy"],
        "events": [],
        "last_refresh_ms": None,
        "last_error": None,
        "last_snapshot_ms": None,
    }


def _ensure_state(
    user_dir: str, baseline_equity: float, baseline_meta: dict, now_ms: int,
) -> dict:
    current = _read_state(user_dir)
    key = _baseline_key(baseline_equity, baseline_meta)
    if current.get("baseline_key") != key:
        current = _new_state(baseline_equity, baseline_meta, now_ms)
        _save_state(user_dir, current)
    return current


def refresh(
    user_dir: str, exchange, *, baseline_equity: float, baseline_meta: dict,
    current_equity: float, now_ms: int | None = None,
) -> dict:
    now_ms = int(time.time() * 1000) if now_ms is None else int(now_ms)
    with process_lock.locked(user_dir, LOCK_KEY):
        state = _ensure_state(user_dir, baseline_equity, baseline_meta, now_ms)
        events = _event_map(state.get("events") or [])
        observation_status = "KNOWN"
        last_error = None
        try:
            raw_bills = fetch_transfer_bills(exchange, state["query_start_ms"])
            for raw in raw_bills:
                event = _normalize_bill(raw)
                if event is None:
                    continue
                existing = events.get(event["bill_id"])
                if existing and _finite(existing.get("value_usdt")) is not None:
                    continue
                events[event["bill_id"]] = _value_event(exchange, event)
        except Exception as exc:
            observation_status = "UNKNOWN"
            last_error = f"{type(exc).__name__}: {exc}"[:500]

        state["events"] = sorted(events.values(), key=lambda e: (int(e["ts_ms"]), e["bill_id"]))
        state["last_refresh_ms"] = now_ms
        state["last_error"] = last_error
        _save_state(user_dir, state)
        return compute_summary(
            baseline_equity=baseline_equity,
            baseline_meta=baseline_meta,
            current_equity=current_equity,
            events=state["events"],
            now_ms=now_ms,
            observation_status=observation_status,
            last_error=last_error,
        )


def cached_summary(
    user_dir: str, *, baseline_equity: float, baseline_meta: dict,
    current_equity: float, now_ms: int | None = None,
) -> dict:
    now_ms = int(time.time() * 1000) if now_ms is None else int(now_ms)
    with process_lock.locked(user_dir, LOCK_KEY):
        state = _ensure_state(user_dir, baseline_equity, baseline_meta, now_ms)
        status = "KNOWN" if state.get("last_refresh_ms") is not None and not state.get("last_error") else "UNKNOWN"
        return compute_summary(
            baseline_equity=baseline_equity,
            baseline_meta=baseline_meta,
            current_equity=current_equity,
            events=state.get("events") or [],
            now_ms=now_ms,
            observation_status=status,
            last_error=state.get("last_error"),
        )


def reset_context(
    user_dir: str, *, baseline_equity: float, baseline_meta: dict,
    now_ms: int | None = None,
) -> dict:
    now_ms = int(time.time() * 1000) if now_ms is None else int(now_ms)
    with process_lock.locked(user_dir, LOCK_KEY):
        state = _new_state(baseline_equity, baseline_meta, now_ms)
        _save_state(user_dir, state)
        return state


def load_state(user_dir: str) -> dict:
    with process_lock.locked(user_dir, LOCK_KEY):
        return _read_state(user_dir)


SNAPSHOT_FILE = "capital_flow_equity_snapshots.jsonl"
SNAPSHOT_MIN_INTERVAL_MS = 60_000


def record_equity_snapshot(
    user_dir: str, *, equity: float, baseline_equity: float,
    baseline_meta: dict, ts_ms: int | None = None,
) -> bool:
    ts_ms = int(time.time() * 1000) if ts_ms is None else int(ts_ms)
    equity = float(equity)
    if not math.isfinite(equity) or equity < 0:
        return False
    with process_lock.locked(user_dir, LOCK_KEY):
        state = _ensure_state(user_dir, baseline_equity, baseline_meta, ts_ms)
        last = int(state.get("last_snapshot_ms") or 0)
        if last and ts_ms - last < SNAPSHOT_MIN_INTERVAL_MS:
            return False
        path = os.path.join(user_dir, SNAPSHOT_FILE)
        record = {
            "schema_version": 1,
            "baseline_key": state["baseline_key"],
            "ts_ms": ts_ms,
            "equity": equity,
        }
        os.makedirs(user_dir, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n")
            f.flush()
            os.fsync(f.fileno())
        state["last_snapshot_ms"] = ts_ms
        _save_state(user_dir, state)
        return True
