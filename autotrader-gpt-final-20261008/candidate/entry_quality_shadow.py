"""Read-only post-deploy entry-quality shadow analysis.

This module has no exchange client and no live trading authority. It evaluates
completed lifecycles after the 2026-10-01 observability deployment and keeps
entry-time inputs separate from post-entry outcome labels.
"""
from __future__ import annotations

import datetime as dt
import math

import entry_counterfactual_shadow
import trade_learning_features

KST = dt.timezone(dt.timedelta(hours=9))
COHORT_START_KST = dt.datetime(2026, 10, 1, 14, 12, 57)
SAMPLE_TARGET_MIN = 20
RULES = {
    "strict_4h1h_5m3m": ("tf_4h_aligned", "tf_1h_aligned", "tf_5m_aligned", "tf_3m_aligned",
                           "not_counter_regime", "gpt_approved"),
    "relaxed_4h1h_5m": ("tf_4h_aligned", "tf_1h_aligned", "tf_5m_aligned",
                         "not_counter_regime", "gpt_approved"),
    "higher_tf_4h1h": ("tf_4h_aligned", "tf_1h_aligned", "not_counter_regime", "gpt_approved"),
}


def _time(value):
    if isinstance(value, dt.datetime):
        parsed = value
    else:
        try:
            parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00")) if value else None
        except (TypeError, ValueError):
            return None
    if parsed is None:
        return None
    return parsed if parsed.tzinfo is None else parsed.astimezone(KST).replace(tzinfo=None)


def select_post_deploy_cohort(rows: list[dict]) -> list[dict]:
    return [r for r in (rows or []) if (_time(r.get("entry_time")) or dt.datetime.min) >= COHORT_START_KST]


def _open_event(row: dict) -> dict:
    return next((e for e in (row.get("events") or []) if e.get("type") == "open"), {})


def classify_provenance(row: dict) -> str:
    group = str(row.get("strategy_group") or "legacy")
    if group == "candidate_c":
        return "candidate_c_rule"
    if group != "core":
        return group
    features = row.get("features") or {}
    if str(features.get("gpt_decision") or "").lower() == "approve_now":
        return "auto_core"
    opened = _open_event(row)
    if opened.get("market_regime") in (None, "") and opened.get("trade_alignment") in (None, ""):
        return "manual_core"
    return "core_unknown"


def _tf_state(row: dict, tf: str) -> str:
    return str((((row.get("features") or {}).get("tf") or {}).get(tf) or {}).get("state") or "unknown").lower()


def _aligned(state: str, side: str):
    target = "bullish" if side == "long" else "bearish" if side == "short" else None
    if target is None or state in ("", "unknown", "none"):
        return None
    return state == target


def _combine(inputs: dict, names: tuple[str, ...]):
    values = [inputs.get(name) for name in names]
    if any(v is None for v in values):
        return None
    return all(bool(v) for v in values)


def _number(value):
    try:
        out = float(value)
        return out if math.isfinite(out) else None
    except (TypeError, ValueError):
        return None


def _future_labels(entry_shadow: dict | None) -> dict:
    row = entry_shadow or {}
    horizons = row.get("horizons") or {}
    labels = {"barrier_outcome": row.get("barrier_outcome"), "resolved": bool(row.get("resolved"))}
    for horizon in (30, 60, 120):
        values = horizons.get(str(horizon)) or {}
        labels[f"mfe{horizon}_r"] = _number(values.get("mfe_r"))
        labels[f"mae{horizon}_r"] = _number(values.get("mae_r"))
    return labels


def evaluate_trade(row: dict, entry_shadow: dict | None = None) -> dict:
    side = str(row.get("side") or "").lower()
    features = row.get("features") or {}
    opened = _open_event(row)
    alignment = opened.get("trade_alignment")
    if alignment in (None, ""):
        alignment = features.get("trade_alignment")
    gpt_decision = str(features.get("gpt_decision") or "unknown").lower()
    chase = features.get("entry_overextension")
    chase_ok = None
    if isinstance(chase, dict):
        chase_ok = chase.get("allowed") if isinstance(chase.get("allowed"), bool) else None
    thesis = features.get("thesis_lock_state") or features.get("reentry_thesis_state") or "unknown"
    thesis_clear = None if thesis == "unknown" else thesis in ("clear", "recovered", "none", "not_blocked")
    gate_inputs = {
        "tf_1d_aligned": _aligned(_tf_state(row, "1d"), side),
        "tf_4h_aligned": _aligned(_tf_state(row, "4h"), side),
        "tf_1h_aligned": _aligned(_tf_state(row, "1h"), side),
        "tf_5m_aligned": _aligned(_tf_state(row, "5m"), side),
        "tf_3m_aligned": _aligned(_tf_state(row, "3m"), side),
        "not_counter_regime": None if alignment in (None, "", "unknown") else alignment != "counter_regime",
        "gpt_approved": gpt_decision == "approve_now",
        "gemini_confidence": _number(features.get("gemini_confidence")),
        "gpt_confidence": _number(features.get("gpt_confidence")),
        "chase_not_extreme": chase_ok,
        "thesis_lock_clear": thesis_clear,
        "thesis_lock_state": thesis,
    }
    rules = {name: _combine(gate_inputs, fields) for name, fields in RULES.items()}
    return {
        "trade_id": row.get("trade_id"), "symbol": row.get("symbol"), "side": row.get("side"),
        "entry_time": row.get("entry_time"), "provenance": classify_provenance(row),
        "actual_net": _number(row.get("lifecycle_net", row.get("net_pnl"))) or 0.0,
        "gate_inputs": gate_inputs, "candidate_rules": rules,
        "labels": _future_labels(entry_shadow), "mode": "shadow_only", "live_authority": False,
    }


def _pf(rows: list[dict]):
    nets = [float(r.get("actual_net") or 0.0) for r in rows]
    wins = sum(v for v in nets if v > 0)
    losses = sum(v for v in nets if v < 0)
    return wins / abs(losses) if wins > 0 and losses < 0 else None


def _metrics(rows: list[dict]) -> dict:
    nets = [float(r.get("actual_net") or 0.0) for r in rows]
    wins = sum(1 for v in nets if v > 0)
    mfe = [r.get("labels", {}).get("mfe60_r") for r in rows]
    mae = [r.get("labels", {}).get("mae60_r") for r in rows]
    mfe = [float(v) for v in mfe if v is not None]
    mae = [float(v) for v in mae if v is not None]
    return {
        "count": len(rows), "actual_net": sum(nets), "profit_factor": _pf(rows),
        "win_rate": (wins / len(rows) * 100.0) if rows else None,
        "avg_mfe60_r": (sum(mfe) / len(mfe)) if mfe else None,
        "avg_mae60_r": (sum(mae) / len(mae)) if mae else None,
    }


def _rule_metrics(rows: list[dict], rule: str, baseline_net: float) -> dict:
    resolved = [r for r in rows if r.get("candidate_rules", {}).get(rule) is not None]
    passed = [r for r in resolved if r["candidate_rules"][rule] is True]
    blocked = [r for r in resolved if r["candidate_rules"][rule] is False]
    passed_net = sum(float(r.get("actual_net") or 0.0) for r in passed)
    return {
        "passed_count": len(passed), "blocked_count": len(blocked),
        "unresolved_count": len(rows) - len(resolved), "filtered_actual_net": passed_net,
        "blocked_actual_net": sum(float(r.get("actual_net") or 0.0) for r in blocked),
        "net_improvement_if_blocked": passed_net - baseline_net, "profit_factor": _pf(passed),
    }


def _condition_metrics(rows: list[dict], key: str) -> dict:
    resolved = [r for r in rows if r.get("gate_inputs", {}).get(key) in (True, False)]
    passed = [r for r in resolved if r["gate_inputs"][key] is True]
    blocked = [r for r in resolved if r["gate_inputs"][key] is False]
    baseline = sum(float(r.get("actual_net") or 0.0) for r in rows)
    passed_net = sum(float(r.get("actual_net") or 0.0) for r in passed)
    return {
        "passed_count": len(passed), "blocked_count": len(blocked),
        "unresolved_count": len(rows) - len(resolved), "filtered_actual_net": passed_net,
        "blocked_actual_net": sum(float(r.get("actual_net") or 0.0) for r in blocked),
        "net_improvement_if_blocked": passed_net - baseline, "profit_factor": _pf(passed),
    }


def summarize_rows(rows: list[dict], entry_shadows: dict[str, dict] | None = None) -> dict:
    entry_shadows = entry_shadows or {}
    cohort = select_post_deploy_cohort(rows)
    evaluated = [evaluate_trade(r, entry_shadows.get(str(r.get("trade_id")))) for r in cohort]
    names = ("auto_core", "candidate_c_rule", "manual_core", "core_unknown")
    cohorts = {name: _metrics([r for r in evaluated if r.get("provenance") == name]) for name in names}
    auto = [r for r in evaluated if r.get("provenance") == "auto_core"]
    baseline_net = sum(float(r.get("actual_net") or 0.0) for r in auto)
    condition_keys = ("tf_1d_aligned", "tf_4h_aligned", "tf_1h_aligned", "tf_5m_aligned",
                      "tf_3m_aligned", "not_counter_regime", "gpt_approved",
                      "chase_not_extreme", "thesis_lock_clear")
    return {
        "mode": "shadow_only", "live_authority": False,
        "cohort_start_kst": COHORT_START_KST.isoformat(), "sample_target_min": SAMPLE_TARGET_MIN,
        "sample_status": "ready" if len(auto) >= SAMPLE_TARGET_MIN else "insufficient",
        "total_count": len(evaluated), "cohorts": cohorts,
        "rules": {name: _rule_metrics(auto, name, baseline_net) for name in RULES},
        "conditions": {key: _condition_metrics(auto, key) for key in condition_keys},
    }


def summary(user_dir: str) -> dict:
    rows = trade_learning_features.build_featured_trades(user_dir)
    raw_shadows = entry_counterfactual_shadow._read_latest(user_dir)
    shadows = {str(r.get("trade_id")): r for r in raw_shadows.values()
               if isinstance(r, dict) and r.get("trade_id")}
    return summarize_rows(rows, shadows)
