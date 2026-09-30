"""Candidate C 1H-weakening early-exit three-way shadow comparison.

No live authority. Counterfactual ranking is gross-only because fees/funding differ
by hypothetical holding duration and are not reconstructed from guessed data.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
from collections import Counter

import jsonl_cache

JOURNAL = "candidate_c_early_exit_shadow.jsonl"
FOLLOW_THROUGH_MFE60_MAX_R = 0.25
HALF_REDUCE_MIN = 0.45
HALF_REDUCE_MAX = 0.55


def _safe_symbol(symbol: str) -> str:
    return str(symbol).replace("/", "_").replace(":", "_")


def _journal_path(user_dir: str) -> str:
    return os.path.join(user_dir, JOURNAL)


def _shadow_id(life: dict) -> str:
    raw = f"{life.get('trade_id')}|{life.get('symbol')}|{life.get('entry_time')}".encode()
    return hashlib.sha256(raw).hexdigest()[:24]


def _read_latest(user_dir: str) -> dict[str, dict]:
    latest = {}
    path = _journal_path(user_dir)
    if not os.path.exists(path):
        return latest
    with open(path, encoding="utf-8") as stream:
        for line in stream:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict) and row.get("shadow_id"):
                latest[row["shadow_id"]] = row
    return latest


def _append(user_dir: str, row: dict) -> None:
    path = _journal_path(user_dir)
    os.makedirs(user_dir, exist_ok=True)
    with jsonl_cache.get_path_lock(path):
        with open(path, "a", encoding="utf-8") as stream:
            stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n")
            stream.flush(); os.fsync(stream.fileno())


def _contract_size(user_dir: str, symbol: str, execution_id: str | None):
    if not execution_id:
        return None
    path = os.path.join(user_dir, f"candidate_c_intent_ledger_{_safe_symbol(symbol)}.jsonl")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as stream:
        for line in stream:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("intent_id") == execution_id and row.get("contract_size") is not None:
                try:
                    return float(row["contract_size"])
                except (TypeError, ValueError):
                    return None
    return None


def _entry_cf_index(user_dir: str) -> dict[str, dict]:
    path = os.path.join(user_dir, "entry_counterfactual_shadow.jsonl")
    out = {}
    if not os.path.exists(path):
        return out
    with open(path, encoding="utf-8") as stream:
        for line in stream:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("trade_id"):
                out[row["trade_id"]] = row
    return out


def _price(event: dict):
    value = event.get("close_price", event.get("price"))
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _base(life: dict, reason: str) -> dict:
    return {
        "shadow_id": _shadow_id(life), "trade_id": life.get("trade_id"),
        "symbol": life.get("symbol"), "side": life.get("side"),
        "entry_time": life.get("entry_time"), "exit_time": life.get("exit_time"),
        "actual_lifecycle_net": life.get("net_pnl", life.get("lifecycle_net")),
        "eligible": False, "resolved": False, "reason": reason,
        "mode": "shadow_only", "live_authority": False,
        "gross_only_counterfactual": True,
    }


def evaluate_lifecycle(user_dir: str, life: dict, entry_cf: dict | None) -> dict:
    if life.get("strategy_group") != "candidate_c":
        return _base(life, "not_candidate_c")
    if life.get("final_close_reason") != "4h_direction_invalidated":
        return _base(life, "not_4h_direction_invalidated")
    if not entry_cf or entry_cf.get("trade_id") != life.get("trade_id"):
        return _base(life, "entry_counterfactual_missing")
    try:
        mfe60 = float(((entry_cf.get("horizons") or {}).get("60") or {}).get("mfe_r"))
    except (TypeError, ValueError):
        return _base(life, "mfe60_missing")
    if mfe60 >= FOLLOW_THROUGH_MFE60_MAX_R:
        return _base(life, "follow_through_not_failed")

    events = list(life.get("events") or [])
    op = next((e for e in events if e.get("type") == "open"), None)
    closes = [e for e in events if e.get("type") == "close"]
    if not op or not closes:
        return _base(life, "open_or_close_missing")
    try:
        original = float(op.get("amount")); entry = float(op.get("price", life.get("entry_price")))
    except (TypeError, ValueError):
        return _base(life, "entry_contract_missing")
    if original <= 0:
        return _base(life, "entry_contract_invalid")
    structural = []
    for event in events:
        if event.get("type") != "reduce":
            continue
        try:
            qty = float(event.get("amount"))
        except (TypeError, ValueError):
            continue
        ratio = qty / original
        if HALF_REDUCE_MIN <= ratio <= HALF_REDUCE_MAX:
            structural.append((event, qty, ratio))
    if len(structural) != 1:
        return _base(life, "structural_half_reduce_not_exactly_one")
    reduce_event, reduce_qty, reduce_ratio = structural[0]
    remaining = original - reduce_qty
    if remaining <= 0:
        return _base(life, "remaining_quantity_invalid")

    reduce_price = _price(reduce_event)
    final_event = closes[-1]
    final_price = _price(final_event)
    contract_size = _contract_size(user_dir, life.get("symbol"), op.get("execution_id"))
    if reduce_price is None or final_price is None or contract_size is None or contract_size <= 0:
        return _base(life, "price_or_contract_size_missing")

    side = life.get("side")
    if side not in ("long", "short"):
        return _base(life, "invalid_side")
    sign = 1.0 if side == "long" else -1.0
    def pnl(qty, price):
        return sign * (float(price) - entry) * float(qty) * contract_size

    current = pnl(reduce_qty, reduce_price) + pnl(remaining, final_price)
    hold = pnl(original, final_price)
    early = pnl(original, reduce_price)
    values = {"CURRENT_POLICY": current, "HOLD_FULL": hold, "EARLY_FULL_EXIT": early}
    best = max(values.values())
    winners = [name for name, value in values.items() if abs(value - best) <= 1e-9]
    winner = winners[0] if len(winners) == 1 else "TIE"
    return {
        **_base(life, "resolved"),
        "eligible": True, "resolved": True, "reason": "resolved",
        "follow_through_failed_60m": True, "mfe60_r": mfe60,
        "structural_reduce_ratio": reduce_ratio,
        "structural_reduce_time": reduce_event.get("time"),
        "structural_reduce_price": reduce_price,
        "final_exit_price": final_price, "contract_size": contract_size,
        "original_contracts": original, "reduced_contracts": reduce_qty,
        "remaining_contracts": remaining,
        "current_policy_gross": current,
        "hold_full_gross": hold,
        "early_full_exit_gross": early,
        "winner": winner,
        "early_vs_current_gross_improvement": early - current,
        "hold_vs_current_gross_improvement": hold - current,
    }


def refresh(user_dir: str, lifecycles: list[dict], entry_cf_rows: list[dict] | None = None) -> int:
    cf = ({row.get("trade_id"): row for row in entry_cf_rows or [] if row.get("trade_id")}
          if entry_cf_rows is not None else _entry_cf_index(user_dir))
    latest = _read_latest(user_dir)
    created = 0
    for life in lifecycles or []:
        if life.get("strategy_group") != "candidate_c":
            continue
        sid = _shadow_id(life)
        if sid in latest:
            continue
        row = evaluate_lifecycle(user_dir, life, cf.get(life.get("trade_id")))
        row["observed_at"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        _append(user_dir, row); latest[sid] = row; created += 1
    return created


def summary(user_dir: str) -> dict:
    rows = list(_read_latest(user_dir).values())
    eligible = [r for r in rows if r.get("eligible")]
    resolved = [r for r in eligible if r.get("resolved")]
    winners = Counter(r.get("winner") or "UNKNOWN" for r in resolved)
    return {
        "mode": "shadow_only", "live_authority": False,
        "basis": "gross_only; hypothetical fee/funding excluded",
        "sample_count": len(rows), "eligible_count": len(eligible),
        "resolved_count": len(resolved), "unresolved_count": len(eligible) - len(resolved),
        "winner_counts": {k: winners.get(k, 0) for k in ("HOLD_FULL", "CURRENT_POLICY", "EARLY_FULL_EXIT", "TIE")},
        "current_policy_gross_sum": sum(float(r.get("current_policy_gross") or 0.0) for r in resolved),
        "hold_full_gross_sum": sum(float(r.get("hold_full_gross") or 0.0) for r in resolved),
        "early_full_exit_gross_sum": sum(float(r.get("early_full_exit_gross") or 0.0) for r in resolved),
        "early_vs_current_gross_improvement": sum(float(r.get("early_vs_current_gross_improvement") or 0.0) for r in resolved),
        "hold_vs_current_gross_improvement": sum(float(r.get("hold_vs_current_gross_improvement") or 0.0) for r in resolved),
        "recent": sorted(resolved, key=lambda r: str(r.get("entry_time") or ""), reverse=True)[:10],
    }
