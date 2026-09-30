"""OKX actual invested-margin return ledger.

This module is reporting-only. It never places/cancels/modifies orders.

For each filled USDT-margined SWAP order we persist the actual leverage, fill size
and average fill price reported by OKX.  A FIFO position-lot reconstruction then
matches every close/reduce quantity back to the margin that originally opened
that quantity.

Period return:
    OKX realized net (realized PnL + allocated entry/close fees)
    ----------------------------------------------------------- * 100
              matched original entry margin

This avoids applying today's fixed margin to historical trades with different
position sizes/leverage.
"""
from __future__ import annotations

import datetime
import json
import math
import os
import time
from collections import defaultdict, deque

import ccxt

import exchange_fee_ledger
import process_lock

STATE_FILE = "okx_margin_return_state.json"
LOCK_KEY = "okx_margin_return_state"
SCHEMA_VERSION = 1
PAGE_LIMIT = 100
MAX_PAGES = 60
PAGE_DELAY_SECONDS = 0.30
ARCHIVE_CATCHUP_GAP_MS = 5 * 24 * 60 * 60 * 1000
KST = datetime.timezone(datetime.timedelta(hours=9))


def _path(user_dir: str) -> str:
    return os.path.join(user_dir, STATE_FILE)


def _finite(value):
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    return n if math.isfinite(n) else None


def _read(user_dir: str) -> dict:
    try:
        with open(_path(user_dir), encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return {}
    if not isinstance(data, dict):
        raise ValueError("margin return state must be an object")
    return data


def _save(user_dir: str, state: dict) -> None:
    process_lock.save_json_atomic(_path(user_dir), state)


def _new_state(start_ms: int) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "start_ms": int(start_ms),
        "orders": [],
        "backfill_complete": False,
        "last_refresh_ms": None,
        "last_error": None,
    }


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
    raise last


def _fetch_pages(method, *, start_ms: int, stop_on_known: set[str] | None = None) -> list[dict]:
    out = []
    seen = set()
    cursor = None
    for _ in range(MAX_PAGES):
        params = {"instType": "SWAP", "limit": str(PAGE_LIMIT)}
        if cursor:
            params["after"] = cursor
        response = _call_with_retry(method, params) or {}
        rows = response.get("data") or []
        if not rows:
            break

        hit_known = False
        for raw in rows:
            order_id = str(raw.get("ordId") or "")
            ts_ms = int(raw.get("uTime") or raw.get("cTime") or 0)
            if stop_on_known and order_id in stop_on_known:
                hit_known = True
            if order_id and order_id not in seen and ts_ms >= start_ms:
                seen.add(order_id)
                out.append(raw)

        oldest_ts = min(int(r.get("uTime") or r.get("cTime") or 0) for r in rows)
        if hit_known or oldest_ts < start_ms or len(rows) < PAGE_LIMIT:
            break
        next_cursor = str(rows[-1].get("ordId") or "")
        if not next_cursor or next_cursor == cursor:
            break
        cursor = next_cursor
        time.sleep(PAGE_DELAY_SECONDS)
    return out


def _market_contract_sizes(exchange) -> dict[str, float]:
    exchange.load_markets()
    out = {}
    for market in exchange.markets.values():
        if not market.get("swap") or not market.get("linear"):
            continue
        inst_id = str((market.get("info") or {}).get("instId") or market.get("id") or "")
        cs = _finite(market.get("contractSize"))
        if inst_id and cs and cs > 0:
            out[inst_id] = cs
    return out


def normalize_order(raw: dict, contract_sizes: dict[str, float]) -> dict | None:
    if str(raw.get("state") or "").lower() != "filled":
        return None
    order_id = str(raw.get("ordId") or "")
    inst_id = str(raw.get("instId") or "")
    side = str(raw.get("side") or "").lower()
    ts_ms = int(raw.get("uTime") or raw.get("cTime") or 0)
    qty = _finite(raw.get("accFillSz"))
    price = _finite(raw.get("avgPx"))
    leverage = _finite(raw.get("lever"))
    pnl = _finite(raw.get("pnl")) or 0.0
    fee = _finite(raw.get("fee")) or 0.0
    fee_ccy = str(raw.get("feeCcy") or "USDT")
    contract_size = contract_sizes.get(inst_id)

    if (
        not order_id or not inst_id or side not in ("buy", "sell") or ts_ms <= 0
        or qty is None or qty <= 0 or price is None or price <= 0
        or leverage is None or leverage <= 0 or contract_size is None or contract_size <= 0
    ):
        return None

    # This bot trades USDT-margined linear swaps. Non-USDT fees would need a
    # historical FX valuation; fail closed instead of inventing a value.
    valuation_error = None
    if fee_ccy != "USDT":
        valuation_error = f"unsupported_fee_ccy:{fee_ccy}"

    notional = qty * contract_size * price
    margin = notional / leverage
    return {
        "order_id": order_id,
        "ts_ms": ts_ms,
        "inst_id": inst_id,
        "side": side,
        "qty": qty,
        "price": price,
        "leverage": leverage,
        "contract_size": contract_size,
        "notional_usdt": notional,
        "margin_usdt": margin,
        "pnl_usdt": pnl,
        "fee_usdt": fee if valuation_error is None else None,
        "fee_ccy": fee_ccy,
        "reduce_only": str(raw.get("reduceOnly") or "").lower() == "true",
        "client_order_id": str(raw.get("clOrdId") or ""),
        "valuation_error": valuation_error,
    }


def _order_map(rows):
    return {str(r.get("order_id")): dict(r) for r in rows if r.get("order_id")}


def _consume_fifo(lots: deque, quantity: float) -> tuple[float, float, float, dict[str, float]]:
    """Return consumed qty/margin/fee plus each source entry's full original margin.

    The proportional consumed margin is still retained for fee/net accounting.
    The full source-entry margin is separate: it is the historical fixed-money
    denominator the user actually committed when the position was opened.
    """
    consumed = margin = entry_fee = 0.0
    fixed_sources: dict[str, float] = {}
    remaining = quantity
    while remaining > 1e-12 and lots:
        lot = lots[0]
        take = min(remaining, lot["qty"])
        frac = take / lot["qty"]
        lot_margin = lot["margin_usdt"] * frac
        lot_fee = lot["entry_fee_usdt"] * frac
        source_id = str(lot.get("order_id") or id(lot))
        source_margin = _finite(lot.get("original_margin_usdt"))
        if source_margin is None:
            source_margin = float(lot["margin_usdt"])
        fixed_sources[source_id] = source_margin
        lot["qty"] -= take
        lot["margin_usdt"] -= lot_margin
        lot["entry_fee_usdt"] -= lot_fee
        remaining -= take
        consumed += take
        margin += lot_margin
        entry_fee += lot_fee
        if lot["qty"] <= 1e-10:
            lots.popleft()
    return consumed, margin, entry_fee, fixed_sources


def reconstruct_realized_events(orders: list[dict]) -> tuple[list[dict], list[str]]:
    """FIFO net-position reconstruction from filled OKX orders.

    A close/reduce event receives exactly the opening margin and opening fee for
    the quantity it consumes.  Reversal orders are split proportionally between
    the close quantity and the newly-opened remainder.
    """
    positions: dict[str, deque] = defaultdict(deque)
    events = []
    anomalies = []

    for order in sorted(orders, key=lambda r: (int(r.get("ts_ms") or 0), str(r.get("order_id") or ""))):
        if order.get("valuation_error"):
            anomalies.append(f"{order.get('order_id')}: {order['valuation_error']}")
            continue
        qty = _finite(order.get("qty"))
        margin = _finite(order.get("margin_usdt"))
        fee = _finite(order.get("fee_usdt"))
        pnl = _finite(order.get("pnl_usdt"))
        side = order.get("side")
        symbol = order.get("inst_id")
        if (
            qty is None or qty <= 0 or margin is None or margin < 0
            or fee is None or pnl is None or side not in ("buy", "sell") or not symbol
        ):
            anomalies.append(f"{order.get('order_id')}: invalid_normalized_order")
            continue

        sign = 1 if side == "buy" else -1
        lots = positions[symbol]
        remaining = qty
        close_qty = close_margin = allocated_entry_fee = 0.0
        fixed_margin_sources: dict[str, float] = {}

        # Any opposite-side inventory is closed first.  Same-side inventory is
        # an add/open and therefore becomes a new lot.
        while remaining > 1e-12 and lots and lots[0]["sign"] != sign:
            consumed, used_margin, used_entry_fee, used_fixed_sources = _consume_fifo(lots, remaining)
            if consumed <= 0:
                break
            remaining -= consumed
            close_qty += consumed
            close_margin += used_margin
            allocated_entry_fee += used_entry_fee
            fixed_margin_sources.update(used_fixed_sources)

        close_fraction = min(1.0, close_qty / qty) if qty else 0.0
        close_fee = fee * close_fraction

        if close_qty > 1e-12:
            realized_net = pnl + allocated_entry_fee + close_fee
            fixed_margin = sum(fixed_margin_sources.values())
            events.append({
                "ts_ms": int(order["ts_ms"]),
                "order_id": order["order_id"],
                "inst_id": symbol,
                "close_order_side": side,
                "position_side": "long" if side == "sell" else "short",
                "closed_qty": close_qty,
                "invested_margin_usdt": close_margin,
                "fixed_margin_usdt": fixed_margin if fixed_margin > 0 else None,
                "fixed_return_pct": (pnl / fixed_margin * 100.0 if fixed_margin > 0 else None),
                "gross_pnl_usdt": pnl,
                "entry_fee_usdt": allocated_entry_fee,
                "close_fee_usdt": close_fee,
                "realized_net_usdt": realized_net,
                "invested_return_pct": (
                    realized_net / close_margin * 100.0 if close_margin > 0 else None
                ),
                "leverage": order.get("leverage"),
                "reduce_only": bool(order.get("reduce_only")),
            })

        # If an order crossed through flat, its remainder opens a new position.
        if remaining > 1e-12:
            open_fraction = remaining / qty
            lots.append({
                "sign": sign,
                "qty": remaining,
                "margin_usdt": margin * open_fraction,
                "original_margin_usdt": margin * open_fraction,
                "entry_fee_usdt": fee * open_fraction,
                "order_id": order["order_id"],
                "ts_ms": int(order["ts_ms"]),
            })

    return events, anomalies


def _period_key(ts_ms: int, view: str) -> str:
    dt = datetime.datetime.fromtimestamp(ts_ms / 1000.0, tz=KST)
    if view == "daily":
        return dt.strftime("%Y-%m-%d")
    if view == "monthly":
        return dt.strftime("%Y-%m")
    if view == "yearly":
        return dt.strftime("%Y")
    raise ValueError(f"unknown period view: {view}")


def _record_period_key(record: dict, view: str) -> str | None:
    raw = record.get("time")
    if not raw:
        return None
    try:
        dt = datetime.datetime.fromisoformat(str(raw))
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(KST)
    if view == "daily":
        return dt.date().isoformat()
    if view == "monthly":
        return dt.date().isoformat()[:7]
    if view == "yearly":
        return dt.date().isoformat()[:4]
    raise ValueError(f"unknown period view: {view}")


def fixed_return_summary_from_records(records: list[dict]) -> dict:
    """Cumulative gross PnL return on each trade's historical OKX entry margin.

    Fixed margin is reused capital, not turnover.  Therefore returns are added
    trade-by-trade instead of dividing total PnL by the sum of every entry
    margin.  Records with no numeric PnL do not affect the calculation.
    """
    pct = 0.0
    pnl_total = 0.0
    matched = 0
    unmatched = 0
    for record in records:
        pnl = _finite(record.get("pnl"))
        if pnl is None:
            continue
        fixed = _finite(record.get("fixed_margin_usdt"))
        if fixed is None or fixed <= 0:
            unmatched += 1
            continue
        pct += pnl / fixed * 100.0
        pnl_total += pnl
        matched += 1
    return {
        "fixed_return_pct": (pct if unmatched == 0 and matched > 0 else None),
        "fixed_pnl_usdt": pnl_total,
        "fixed_margin_match_count": matched,
        "fixed_margin_unmatched_count": unmatched,
    }


def period_fixed_returns_from_records(records: list[dict]) -> dict:
    result = {"daily": {}, "monthly": {}, "yearly": {}}
    for view in result:
        buckets: dict[str, list[dict]] = {}
        for record in records:
            key = _record_period_key(record, view)
            if key is not None:
                buckets.setdefault(key, []).append(record)
        result[view] = {
            key: fixed_return_summary_from_records(bucket_records)
            for key, bucket_records in buckets.items()
        }
    return result


def period_returns_from_orders(orders: list[dict]) -> tuple[dict, list[str]]:
    events, anomalies = reconstruct_realized_events(orders)
    result = {"daily": {}, "monthly": {}, "yearly": {}}
    for view in result:
        buckets = {}
        for event in events:
            key = _period_key(int(event["ts_ms"]), view)
            b = buckets.setdefault(key, {
                "invested_margin_usdt": 0.0,
                "okx_realized_net_usdt": 0.0,
                "okx_gross_pnl_usdt": 0.0,
                "okx_fee_usdt": 0.0,
                "realization_events": 0,
                "fixed_return_pct": 0.0,
                "fixed_pnl_usdt": 0.0,
                "fixed_margin_match_count": 0,
            })
            b["invested_margin_usdt"] += float(event["invested_margin_usdt"])
            b["okx_realized_net_usdt"] += float(event["realized_net_usdt"])
            b["okx_gross_pnl_usdt"] += float(event["gross_pnl_usdt"])
            # entry/close fees are negative in OKX; expose cost as positive.
            b["okx_fee_usdt"] += -(float(event["entry_fee_usdt"]) + float(event["close_fee_usdt"]))
            b["realization_events"] += 1
            fixed_rate = _finite(event.get("fixed_return_pct"))
            if fixed_rate is not None:
                b["fixed_return_pct"] += fixed_rate
                b["fixed_pnl_usdt"] += float(event["gross_pnl_usdt"])
                b["fixed_margin_match_count"] += 1
        for key, b in buckets.items():
            margin = b["invested_margin_usdt"]
            b["invested_return_pct"] = (
                b["okx_realized_net_usdt"] / margin * 100.0 if margin > 0 else None
            )
            if b["fixed_margin_match_count"] == 0:
                b["fixed_return_pct"] = None
        result[view] = buckets
    return result, anomalies


def refresh(user_dir: str, exchange, *, start_ms: int | None = None, now_ms: int | None = None) -> dict:
    now_ms = int(time.time() * 1000) if now_ms is None else int(now_ms)
    if start_ms is None:
        start_ms = exchange_fee_ledger.derive_start_ms(user_dir)
    if start_ms is None:
        return {
            "complete": False, "last_error": "margin_coverage_start_unknown",
            "start_ms": None, "last_refresh_ms": None, "order_count": 0,
            "anomaly_count": 0, "periods": {"daily": {}, "monthly": {}, "yearly": {}},
        }

    with process_lock.locked(user_dir, LOCK_KEY):
        state = _read(user_dir)
        if state.get("start_ms") != int(start_ms):
            state = _new_state(start_ms)

        orders = _order_map(state.get("orders") or [])
        error = None
        try:
            contract_sizes = _market_contract_sizes(exchange)
            if not state.get("backfill_complete"):
                raw_rows = _fetch_pages(
                    exchange.private_get_trade_orders_history_archive,
                    start_ms=int(start_ms),
                )
                state["backfill_complete"] = True
            else:
                last_refresh = int(state.get("last_refresh_ms") or 0)
                method = (
                    exchange.private_get_trade_orders_history_archive
                    if not last_refresh or now_ms - last_refresh > ARCHIVE_CATCHUP_GAP_MS
                    else exchange.private_get_trade_orders_history
                )
                raw_rows = _fetch_pages(
                    method,
                    start_ms=int(start_ms),
                    stop_on_known=set(orders),
                )

            for raw in raw_rows:
                order = normalize_order(raw, contract_sizes)
                if order is not None:
                    orders[order["order_id"]] = order
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"[:500]

        state["orders"] = sorted(
            orders.values(), key=lambda r: (int(r["ts_ms"]), r["order_id"])
        )
        state["last_refresh_ms"] = now_ms
        state["last_error"] = error
        _save(user_dir, state)

        periods, anomalies = period_returns_from_orders(state["orders"])
        complete = bool(state.get("backfill_complete")) and error is None and not anomalies
        return {
            "schema_version": SCHEMA_VERSION,
            "complete": complete,
            "last_error": error,
            "start_ms": int(start_ms),
            "last_refresh_ms": now_ms,
            "order_count": len(state["orders"]),
            "anomaly_count": len(anomalies),
            "anomalies": anomalies[:20],
            "periods": periods,
        }


def cached_summary(user_dir: str) -> dict:
    with process_lock.locked(user_dir, LOCK_KEY):
        state = _read(user_dir)
        start_ms = state.get("start_ms")
        if start_ms is None:
            return {
                "schema_version": SCHEMA_VERSION,
                "complete": False,
                "last_error": "margin_ledger_not_initialized",
                "start_ms": None,
                "last_refresh_ms": None,
                "order_count": 0,
                "anomaly_count": 0,
                "periods": {"daily": {}, "monthly": {}, "yearly": {}},
            }
        periods, anomalies = period_returns_from_orders(state.get("orders") or [])
        complete = bool(state.get("backfill_complete")) and not state.get("last_error") and not anomalies
        return {
            "schema_version": SCHEMA_VERSION,
            "complete": complete,
            "last_error": state.get("last_error"),
            "start_ms": int(start_ms),
            "last_refresh_ms": state.get("last_refresh_ms"),
            "order_count": len(state.get("orders") or []),
            "anomaly_count": len(anomalies),
            "anomalies": anomalies[:20],
            "periods": periods,
        }


def load_state(user_dir: str) -> dict:
    with process_lock.locked(user_dir, LOCK_KEY):
        return _read(user_dir)


def _inst_to_ccxt_symbol(inst_id: str) -> str:
    if inst_id.endswith("-USDT-SWAP"):
        return inst_id[:-10] + "/USDT:USDT"
    return inst_id


def _record_time_candidates_ms(value: str | None) -> list[int]:
    """Epoch candidates for historical mixed naive timestamps.

    Old records were created while the VM timezone changed over the project's
    lifetime.  Naive timestamps therefore exist in both KST and UTC semantics.
    We never guess one globally: matching considers both interpretations and
    only accepts a nearby OKX event.
    """
    if not value:
        return []
    try:
        dt = datetime.datetime.fromisoformat(value)
    except ValueError:
        return []
    if dt.tzinfo is not None:
        return [int(dt.astimezone(KST).timestamp() * 1000)]
    kst_ms = int(dt.replace(tzinfo=KST).timestamp() * 1000)
    utc_ms = int(dt.replace(tzinfo=datetime.timezone.utc).timestamp() * 1000)
    return list(dict.fromkeys((kst_ms, utc_ms)))


def _record_event_pair_score(record: dict, event: dict):
    if record.get("history_source") == "core_unified":
        # Durable identity is stronger than a nearby timestamp/quantity. Never
        # attach a different trade's ROI when the order ledger is still stale.
        if not record.get("exchange_order_id") or record["exchange_order_id"] != event.get("order_id"):
            return None
        return (-1, 0.0, 0.0)
    if record.get("symbol") != _inst_to_ccxt_symbol(str(event.get("inst_id") or "")):
        return None
    rec_side = str(record.get("side") or "").lower()
    event_side = str(event.get("position_side") or "").lower()
    if rec_side in ("long", "short") and event_side and rec_side != event_side:
        return None

    time_candidates = _record_time_candidates_ms(record.get("time"))
    if not time_candidates:
        return None
    event_ms = int(event.get("ts_ms") or 0)
    dt_ms = min(abs(event_ms - t) for t in time_candidates)
    # A delayed external-close detector can write the local record minutes
    # after OKX filled. Beyond 45 minutes the association is too ambiguous.
    if dt_ms > 45 * 60 * 1000:
        return None

    rec_qty = _finite(record.get("amount"))
    event_qty = _finite(event.get("closed_qty"))
    if rec_qty is None or rec_qty <= 0 or event_qty is None or event_qty <= 0:
        qty_rel = 0.0
    else:
        qty_rel = abs(rec_qty - event_qty) / max(abs(rec_qty), abs(event_qty), 1e-12)
        # Historical REDUCE_50 records can contain intended quantity while OKX
        # reports the actual position delta after lot-size quantization. Large
        # discrepancies are rejected; small quantization differences are fine.
        # When timestamps are essentially identical, trust the
        # exchange fill even if an old REDUCE record stored an intended size
        # that differs materially from the actual filled delta.
        max_qty_rel = 0.75 if dt_ms <= 2_000 else 0.25
        if qty_rel > max_qty_rel:
            return None

    rec_pnl = _finite(record.get("pnl"))
    event_pnl = _finite(event.get("gross_pnl_usdt"))
    if rec_pnl is None or event_pnl is None:
        pnl_gap = 0.0
    else:
        pnl_gap = abs(rec_pnl - event_pnl)

    # Time is the strongest identity signal; quantity then PnL only break ties.
    return (dt_ms, qty_rel, pnl_gap)


def enrich_trade_records(user_dir: str, records: list[dict]) -> tuple[list[dict], dict]:
    """Attach OKX actual entry margin + per-realization return to trade rows.

    Matching is one-to-one and conservative. Unmatched rows remain visible with
    null ROI rather than receiving an invented percentage.
    """
    state = load_state(user_dir)
    events, anomalies = reconstruct_realized_events(state.get("orders") or [])

    pairs = []
    for ri, record in enumerate(records):
        for ei, event in enumerate(events):
            score = _record_event_pair_score(record, event)
            if score is not None:
                pairs.append((score, ri, ei))

    pairs.sort(key=lambda x: x[0])
    used_records = set()
    used_events = set()
    matched = {}
    for score, ri, ei in pairs:
        if ri in used_records or ei in used_events:
            continue
        used_records.add(ri)
        used_events.add(ei)
        matched[ri] = (events[ei], score)

    out = []
    for ri, record in enumerate(records):
        item = dict(record)
        match = matched.get(ri)
        if item.get("history_source") == "core_unified":
            # The fill projection owns this lifecycle's entry basis, including
            # adds/reduces. Keep it even before the cached close order arrives.
            item["okx_margin_order_id"] = item.get("exchange_order_id")
            item["okx_margin_match_delta_ms"] = 0 if match else None
            out.append(item)
            continue
        if match is None:
            item.update({
                "invested_margin_usdt": None,
                "invested_return_pct": None,
                "fixed_margin_usdt": None,
                "fixed_return_pct": None,
                "okx_realized_net_for_return": None,
                "okx_margin_order_id": None,
                "okx_margin_match_delta_ms": None,
            })
        else:
            event, score = match
            fixed_margin = _finite(event.get("fixed_margin_usdt"))
            record_pnl = _finite(item.get("pnl"))
            fixed_return = (
                record_pnl / fixed_margin * 100.0
                if record_pnl is not None and fixed_margin is not None and fixed_margin > 0
                else None
            )
            item.update({
                "invested_margin_usdt": event.get("invested_margin_usdt"),
                "invested_return_pct": event.get("invested_return_pct"),
                "fixed_margin_usdt": fixed_margin,
                "fixed_return_pct": fixed_return,
                "okx_realized_net_for_return": event.get("realized_net_usdt"),
                "okx_margin_order_id": event.get("order_id"),
                "okx_margin_match_delta_ms": score[0],
            })
        out.append(item)

    known = sum(r.get("fixed_margin_usdt") is not None for r in out)
    summary = {
        "complete": bool(state.get("backfill_complete")) and not state.get("last_error") and not anomalies,
        "record_count": len(records),
        "matched_count": known,
        "unmatched_count": len(records) - known,
        "event_count": len(events),
        "unused_event_count": len(events) - len(used_events),
        "anomaly_count": len(anomalies),
    }
    return out, summary
