"""Fee-aware entry gate counterfactual; observation only, never live authority."""
from __future__ import annotations

import json
import math
import os
from collections import Counter

CORE_TAKER_FEE_RATE = 0.0005
THRESHOLDS = (1, 2, 3, 5)


def _open_event(life):
    return next((e for e in (life.get("events") or []) if e.get("type") == "open"), {})


def _finite(value, positive=False):
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(n) or (positive and n <= 0):
        return None
    return n


def _actual_net(life):
    value = life.get("lifecycle_net", life.get("net_pnl"))
    return _finite(value) or 0.0


def _candidate_epoch_meta(user_dir, execution_id):
    if not execution_id:
        return None
    path = os.path.join(user_dir, "candidate_c_epoch_store.jsonl")
    if not os.path.exists(path):
        return None
    found = None
    with open(path, encoding="utf-8") as stream:
        for line in stream:
            try:
                row = json.loads(line)
            except (json.JSONDecodeError, TypeError):
                continue
            if row.get("position_id") != execution_id or row.get("event") == "discarded":
                continue
            found = row
    if not found:
        return None
    required = ("contract_size", "fee_rate", "spread_bps", "slippage_bps")
    values = {key: _finite(found.get(key)) for key in required}
    if any(values[key] is None for key in required) or values["contract_size"] <= 0 or values["fee_rate"] < 0:
        return None
    return values


def _effective_prices(side, entry, tp, spread_bps, slippage_bps):
    k = (spread_bps + slippage_bps) / 10000.0
    if side == "long":
        return entry * (1.0 + k), tp * (1.0 - k)
    if side == "short":
        return entry * (1.0 - k), tp * (1.0 + k)
    return None, None


def evaluate_lifecycle(user_dir: str, life: dict) -> dict:
    op = _open_event(life)
    symbol = life.get("symbol") or op.get("symbol")
    side = str(life.get("side") or op.get("side") or "").lower()
    strategy = life.get("strategy_group") or op.get("strategy_group") or "core"
    base = {"trade_id": life.get("trade_id"), "symbol": symbol, "side": side,
            "strategy_group": strategy, "actual_net": _actual_net(life),
            "mode": "shadow_only", "live_authority": False}
    entry = _finite(op.get("price", life.get("entry_price")), positive=True)
    tp = _finite(op.get("tp_price"), positive=True)
    amount = _finite(op.get("amount"), positive=True)
    if tp is None:
        return {**base, "resolved": False, "reason": "tp_unproven"}
    if entry is None or amount is None or side not in ("long", "short"):
        return {**base, "resolved": False, "reason": "entry_identity_unproven"}

    if strategy == "candidate_c":
        meta = _candidate_epoch_meta(user_dir, op.get("execution_id"))
        if not meta:
            return {**base, "resolved": False, "reason": "candidate_cost_meta_unproven"}
        amount_coin = amount * meta["contract_size"]
        fee_rate = meta["fee_rate"]
        spread_bps = meta["spread_bps"]
        slippage_bps = meta["slippage_bps"]
        cost_basis = "fee_spread_slippage_candidate_c"
        spread_proven = True
    else:
        amount_coin = amount
        fee_rate = CORE_TAKER_FEE_RATE
        spread_bps = slippage_bps = 0.0
        cost_basis = "fee_only_core"
        spread_proven = False

    effective_entry, effective_tp = _effective_prices(side, entry, tp, spread_bps, slippage_bps)
    sign = 1.0 if side == "long" else -1.0
    raw_tp_gross = sign * (tp - entry) * amount_coin
    execution_adjusted_gross = sign * (effective_tp - effective_entry) * amount_coin
    entry_fee = effective_entry * amount_coin * fee_rate
    exit_fee = effective_tp * amount_coin * fee_rate
    expected_net = execution_adjusted_gross - entry_fee - exit_fee
    total_cost = raw_tp_gross - expected_net
    ratio = expected_net / total_cost if total_cost > 0 else None
    return {**base, "resolved": True, "reason": "resolved", "entry_price": entry, "tp_price": tp,
            "amount_coin": amount_coin, "fee_rate": fee_rate, "spread_bps": spread_bps,
            "slippage_bps": slippage_bps, "cost_basis": cost_basis,
            "spread_slippage_proven": spread_proven, "raw_tp_gross_usdt": raw_tp_gross,
            "execution_adjusted_gross_usdt": execution_adjusted_gross,
            "entry_fee_usdt": entry_fee, "exit_fee_usdt": exit_fee,
            "expected_net_at_tp_usdt": expected_net, "total_expected_cost_usdt": total_cost,
            "edge_to_cost_ratio": ratio}


def _pf(values):
    wins = sum(v for v in values if v > 0)
    losses = -sum(v for v in values if v < 0)
    if losses <= 0:
        return None
    return wins / losses


def summarize(user_dir: str, lifecycles: list[dict], limit: int = 150) -> dict:
    rows = list(lifecycles or [])[-max(0, int(limit)):]
    evaluated = [evaluate_lifecycle(user_dir, row) for row in rows]
    resolved = [row for row in evaluated if row.get("resolved")]
    baseline = sum(row["actual_net"] for row in resolved)
    thresholds = {}
    for multiple in THRESHOLDS:
        passed = [r for r in resolved if r.get("edge_to_cost_ratio") is not None and r["edge_to_cost_ratio"] >= multiple]
        blocked = [r for r in resolved if r not in passed]
        filtered = sum(r["actual_net"] for r in passed)
        thresholds[f"{multiple}x"] = {
            "passed_count": len(passed), "blocked_count": len(blocked),
            "baseline_actual_net": baseline, "filtered_actual_net": filtered,
            "net_improvement_if_blocked": filtered - baseline,
            "passed_pf": _pf([r["actual_net"] for r in passed]),
            "blocked_actual_net": sum(r["actual_net"] for r in blocked),
        }

    symbols = []
    for symbol in sorted({r.get("symbol") for r in resolved if r.get("symbol")}):
        subset = [r for r in resolved if r.get("symbol") == symbol]
        symbols.append({"symbol": symbol, "count": len(subset),
                        "actual_net": sum(r["actual_net"] for r in subset),
                        "actual_pf": _pf([r["actual_net"] for r in subset]),
                        "avg_edge_to_cost_ratio": sum(r["edge_to_cost_ratio"] for r in subset if r.get("edge_to_cost_ratio") is not None) / max(1, sum(r.get("edge_to_cost_ratio") is not None for r in subset))})

    return {
        "mode": "shadow_only", "live_authority": False,
        "sample_count": len(evaluated), "resolved_count": len(resolved),
        "unresolved_count": len(evaluated) - len(resolved),
        "unresolved_reasons": dict(Counter(r.get("reason") for r in evaluated if not r.get("resolved"))),
        "cost_basis_counts": dict(Counter(r.get("cost_basis") for r in resolved)),
        "baseline_actual_net": baseline, "thresholds": thresholds,
        "symbols": symbols,
    }
