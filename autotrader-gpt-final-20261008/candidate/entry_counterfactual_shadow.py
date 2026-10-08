"""Read-only entry-quality counterfactual shadow analysis.

Completed lifecycles are replayed for the first 30/60/120 minutes after entry.
This module never changes live trading state or order authority.
"""
from __future__ import annotations

import datetime as dt
import glob
import hashlib
import json
import math
import os
from collections import Counter

import jsonl_cache

JOURNAL = "entry_counterfactual_shadow.jsonl"
HORIZONS = (30, 60, 120)
KST = dt.timezone(dt.timedelta(hours=9))


def _time(value):
    if isinstance(value, dt.datetime):
        return value
    try:
        return dt.datetime.fromisoformat(str(value)) if value else None
    except (TypeError, ValueError):
        return None


def _wall(value):
    parsed = _time(value)
    if parsed is None:
        return None
    return parsed if parsed.tzinfo is None else parsed.astimezone(KST).replace(tzinfo=None)


def _open_event(life):
    return next((e for e in (life.get("events") or []) if e.get("type") == "open"), {})


def _entry_id(life):
    raw = f"{life.get('trade_id')}|{life.get('symbol')}|{life.get('side')}|{life.get('entry_time')}".encode()
    return hashlib.sha256(raw).hexdigest()[:24]


def _normalize_bars(rows):
    out = []
    for raw in rows or []:
        if not isinstance(raw, dict):
            continue
        when = _wall(raw.get("time") or raw.get("datetime") or raw.get("timestamp"))
        if when is None and isinstance(raw.get("timestamp"), (int, float)):
            when = dt.datetime.fromtimestamp(float(raw["timestamp"]) / 1000.0, tz=dt.timezone.utc).astimezone(KST).replace(tzinfo=None)
        try:
            high = float(raw["high"]); low = float(raw["low"]); close = float(raw["close"])
        except (KeyError, TypeError, ValueError):
            continue
        out.append({"time": when, "high": high, "low": low, "close": close})
    return sorted(out, key=lambda r: r["time"])


def _excursion(side, entry, high, low):
    if side == "long":
        return max(0.0, high - entry), max(0.0, entry - low)
    return max(0.0, entry - low), max(0.0, high - entry)


def _barrier_hits(side, sl, tp, high, low):
    sl_hit = False if sl is None else (low <= sl if side == "long" else high >= sl)
    tp_hit = False if tp is None else (high >= tp if side == "long" else low <= tp)
    return sl_hit, tp_hit


def _candidate_context(life, open_event):
    regime = open_event.get("market_regime")
    alignment = open_event.get("trade_alignment")
    strategy = life.get("strategy_group") or open_event.get("strategy_group") or "legacy"
    direction_4h = str(life.get("entry_4h_direction") or "") or (life.get("side") or "").upper() if strategy == "candidate_c" else None
    return regime, alignment, strategy, direction_4h


def evaluate_lifecycle(life: dict, bars: list[dict]) -> dict:
    open_event = _open_event(life)
    side = life.get("side")
    entry_time = _wall(life.get("entry_time"))
    try:
        entry = float(life.get("entry_price") if life.get("entry_price") is not None else open_event.get("price"))
    except (TypeError, ValueError):
        entry = None
    try:
        sl = float(open_event.get("sl_price")) if open_event.get("sl_price") is not None else None
    except (TypeError, ValueError):
        sl = None
    try:
        tp = float(open_event.get("tp_price")) if open_event.get("tp_price") is not None else None
    except (TypeError, ValueError):
        tp = None
    if side not in ("long", "short") or entry_time is None or entry is None or entry <= 0:
        return {"entry_id": _entry_id(life), "resolved": False, "reason": "invalid_entry"}
    risk = abs(entry - sl) if sl is not None and sl != entry else None
    path = [b for b in _normalize_bars(bars) if entry_time < b["time"] <= entry_time + dt.timedelta(minutes=125)]
    latest = path[-1]["time"] if path else None
    resolved = bool(latest is not None and latest >= entry_time + dt.timedelta(minutes=120))
    maxima = {m: {"mfe": 0.0, "mae": 0.0} for m in HORIZONS}
    barrier_outcome = None
    barrier_time = None
    barrier_resolved = False
    for bar in path:
        minutes = (bar["time"] - entry_time).total_seconds() / 60.0
        fav, adv = _excursion(side, entry, bar["high"], bar["low"])
        for horizon in HORIZONS:
            if minutes <= horizon + 5.0 + 1e-9:
                maxima[horizon]["mfe"] = max(maxima[horizon]["mfe"], fav)
                maxima[horizon]["mae"] = max(maxima[horizon]["mae"], adv)
        if barrier_outcome is None:
            sl_hit, tp_hit = _barrier_hits(side, sl, tp, bar["high"], bar["low"])
            if sl_hit and tp_hit:
                barrier_outcome = "ambiguous_same_bar"; barrier_time = bar["time"].isoformat(); barrier_resolved = False
            elif sl_hit:
                barrier_outcome = "sl_first"; barrier_time = bar["time"].isoformat(); barrier_resolved = True
            elif tp_hit:
                barrier_outcome = "tp_first"; barrier_time = bar["time"].isoformat(); barrier_resolved = True
    if barrier_outcome is None and resolved:
        barrier_outcome = "none_120m"; barrier_resolved = True
    horizons = {}
    for horizon in HORIZONS:
        mfe = maxima[horizon]["mfe"]; mae = maxima[horizon]["mae"]
        horizons[str(horizon)] = {
            "mfe_pct": (mfe / entry * 100.0) if entry else None,
            "mae_pct": (mae / entry * 100.0) if entry else None,
            "mfe_r": (mfe / risk) if risk else None,
            "mae_r": (mae / risk) if risk else None,
        }
    h30 = horizons["30"]
    immediate_adverse = bool(
        h30.get("mae_r") is not None and h30.get("mfe_r") is not None
        and h30["mae_r"] >= 0.5 and h30["mfe_r"] < 0.25
    )
    clean_follow_through = bool(
        h30.get("mfe_r") is not None and h30.get("mae_r") is not None
        and h30["mfe_r"] >= 0.5 and h30["mae_r"] < 0.25
    )
    regime, alignment, strategy, direction_4h = _candidate_context(life, open_event)
    breakout_reference = life.get("candidate_c_breakout_reference")
    breakout_extension_r = None
    try:
        ref = float(breakout_reference)
        directional_extension = (entry - ref) if side == "long" else (ref - entry)
        breakout_extension_r = directional_extension / risk if risk else None
    except (TypeError, ValueError):
        pass
    late_entry_signature = bool(
        strategy == "candidate_c"
        and (immediate_adverse or (breakout_extension_r is not None and breakout_extension_r >= 0.25))
    )
    return {
        "entry_id": _entry_id(life), "trade_id": life.get("trade_id"), "symbol": life.get("symbol"),
        "side": side, "strategy_group": strategy, "entry_time": life.get("entry_time"),
        "entry_price": entry, "sl": sl, "tp": tp, "initial_risk": risk,
        "net_pnl": life.get("net_pnl"), "market_regime": regime, "trade_alignment": alignment,
        "entry_4h_direction": direction_4h, "candidate_c_breakout_reference": breakout_reference,
        "breakout_extension_r": breakout_extension_r, "resolved": resolved,
        "barrier_resolved": barrier_resolved, "barrier_outcome": barrier_outcome or "unresolved",
        "barrier_time": barrier_time, "horizons": horizons,
        "horizon_basis": "confirmed_5m_close_max_5m_slop",
        "immediate_adverse": immediate_adverse, "clean_follow_through": clean_follow_through,
        "late_entry_signature": late_entry_signature,
        "mode": "shadow_only", "live_authority": False,
    }


def summarize_evaluations(rows: list[dict]) -> dict:
    rows = [r for r in (rows or []) if isinstance(r, dict)]
    resolved = [r for r in rows if r.get("resolved")]
    barrier_counts = Counter(r.get("barrier_outcome") or "unresolved" for r in rows)
    horizon_summary = {}
    for horizon in HORIZONS:
        vals = [r.get("horizons", {}).get(str(horizon), {}) for r in rows]
        mfe = [float(v["mfe_r"]) for v in vals if v.get("mfe_r") is not None]
        mae = [float(v["mae_r"]) for v in vals if v.get("mae_r") is not None]
        horizon_summary[str(horizon)] = {
            "avg_mfe_r": sum(mfe) / len(mfe) if mfe else None,
            "avg_mae_r": sum(mae) / len(mae) if mae else None,
        }
    groups = []
    for dimension in ("symbol", "side", "strategy_group", "market_regime", "trade_alignment"):
        values = sorted({str(r.get(dimension)) for r in rows if r.get(dimension) not in (None, "")})
        for value in values:
            subset = [r for r in rows if str(r.get(dimension)) == value]
            h60 = [r.get("horizons", {}).get("60", {}) for r in subset]
            mfe = [float(v["mfe_r"]) for v in h60 if v.get("mfe_r") is not None]
            mae = [float(v["mae_r"]) for v in h60 if v.get("mae_r") is not None]
            groups.append({
                "dimension": dimension, "value": value, "count": len(subset),
                "resolved_count": sum(bool(r.get("resolved")) for r in subset),
                "net_pnl": sum(float(r.get("net_pnl") or 0.0) for r in subset),
                "avg_mfe_60_r": sum(mfe) / len(mfe) if mfe else None,
                "avg_mae_60_r": sum(mae) / len(mae) if mae else None,
                "late_entry_signature_count": sum(bool(r.get("late_entry_signature")) for r in subset),
            })
    groups.sort(key=lambda r: (float(r.get("net_pnl") or 0.0), -int(r.get("count") or 0)))
    return {
        "mode": "shadow_only", "live_authority": False,
        "sample_count": len(rows), "resolved_count": len(resolved),
        "unresolved_count": len(rows) - len(resolved), "barrier_counts": dict(barrier_counts),
        "late_entry_signature_count": sum(bool(r.get("late_entry_signature")) for r in rows),
        "immediate_adverse_count": sum(bool(r.get("immediate_adverse")) for r in rows),
        "clean_follow_through_count": sum(bool(r.get("clean_follow_through")) for r in rows),
        "horizon_summary": horizon_summary, "groups": groups,
    }


def _path(user_dir):
    return os.path.join(user_dir, JOURNAL)


def _read_latest(user_dir):
    latest = {}
    path = _path(user_dir)
    if not os.path.exists(path):
        return latest
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict) and row.get("entry_id"):
                latest[row["entry_id"]] = row
    return latest


def _append(user_dir, row):
    path = _path(user_dir); os.makedirs(user_dir, exist_ok=True)
    with jsonl_cache.get_path_lock(path):
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n")
            f.flush(); os.fsync(f.fileno())


def _candidate_c_refs(user_dir):
    provenance = {}
    p = os.path.join(user_dir, "candidate_c_breakout_setup_provenance.jsonl")
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            for line in f:
                try: row = json.loads(line)
                except json.JSONDecodeError: continue
                if row.get("setup_id") and row.get("provenance_exact") and row.get("breakout_reference") is not None:
                    provenance[row["setup_id"]] = row["breakout_reference"]
    execution_to_setup = {}
    for p in glob.glob(os.path.join(user_dir, "candidate_c_intent_ledger_*.jsonl")):
        with open(p, encoding="utf-8") as f:
            for line in f:
                try: row = json.loads(line)
                except json.JSONDecodeError: continue
                if row.get("event") == "created" and row.get("kind") == "EntryIntent" and row.get("intent_id"):
                    execution_to_setup[row["intent_id"]] = row.get("setup_id")
    return execution_to_setup, provenance


def refresh(user_dir: str, lifecycles: list[dict], fetcher, limit: int = 150) -> int:
    latest = _read_latest(user_dir)
    exec_to_setup, provenance = _candidate_c_refs(user_dir)
    rows = sorted(list(lifecycles or []), key=lambda r: _wall(r.get("entry_time")) or dt.datetime.min)[-max(0, int(limit)):]
    created = 0
    for life in rows:
        eid = _entry_id(life)
        if eid in latest:
            continue
        start = _wall(life.get("entry_time"))
        if start is None or not life.get("symbol"):
            continue
        enriched = dict(life)
        if enriched.get("strategy_group") == "candidate_c":
            op = _open_event(enriched)
            setup_id = exec_to_setup.get(op.get("execution_id"))
            if setup_id in provenance:
                enriched["candidate_c_breakout_reference"] = provenance[setup_id]
        try:
            fetched = fetcher(enriched["symbol"], start, start + dt.timedelta(minutes=120)) or []
        except Exception:
            continue
        result = evaluate_lifecycle(enriched, fetched)
        if not result.get("resolved"):
            continue
        result["observed_at"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        _append(user_dir, result); latest[eid] = result; created += 1
    return created


def summary(user_dir: str, limit: int = 150) -> dict:
    rows = list(_read_latest(user_dir).values())
    rows.sort(key=lambda r: _wall(r.get("entry_time")) or dt.datetime.min, reverse=True)
    return summarize_evaluations(rows[:max(0, int(limit))])


def make_okx_fetcher(cfg):
    import okx_client
    clients = {}
    cache = {}
    def fetch(symbol, start, end):
        start = _wall(start); end = _wall(end)
        if start is None or end is None:
            return []
        existing = cache.get(symbol)
        if existing and existing["start"] <= start and existing["end"] >= end + dt.timedelta(minutes=5):
            return [r for r in existing["bars"] if start < r["time"] <= end + dt.timedelta(minutes=5)]
        client = clients.setdefault(symbol, okx_client.OkxClient(symbol, cfg))
        since = int(start.replace(tzinfo=KST).timestamp() * 1000)
        raw = client.exchange.fetch_ohlcv(symbol, "5m", since=since, limit=300)
        bars = []
        for item in raw or []:
            if not isinstance(item, (list, tuple)) or len(item) < 5:
                continue
            when = (dt.datetime.fromtimestamp(float(item[0]) / 1000.0, tz=dt.timezone.utc).astimezone(KST) + dt.timedelta(minutes=5)).replace(tzinfo=None)
            bars.append({"time": when, "high": float(item[2]), "low": float(item[3]), "close": float(item[4])})
        cache[symbol] = {"start": start, "end": bars[-1]["time"] if bars else start, "bars": bars}
        return [r for r in bars if start < r["time"] <= end + dt.timedelta(minutes=5)]
    return fetch
