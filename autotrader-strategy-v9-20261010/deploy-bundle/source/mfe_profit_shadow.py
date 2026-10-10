"""Persist MFE/giveback evidence and emit a deterministic live candidate.

This module deliberately has zero exchange/order authority.  It only stores peak/giveback
state, records shadow evidence, and emits a candidate dict for one stricter policy.  The
trading layer owns every live safety check, account lock, sizing rule, fill reconciliation,
and protection resize.
"""
import datetime
import json
import math
import os

import jsonl_cache
import process_lock

STATE_FILE = "mfe_profit_shadow_state.json"
LOG_FILE = "mfe_profit_shadow_log.jsonl"
LIVE_POLICY_NAME = "arm_0.50_giveback_0.25"
POLICIES = (
    {"name": "arm_0.40_giveback_0.20", "arm_r": 0.40, "giveback_r": 0.20, "reduce_fraction": 0.25},
    {"name": LIVE_POLICY_NAME, "arm_r": 0.50, "giveback_r": 0.25, "reduce_fraction": 0.25},
)
_EPS = 1e-9


def _state_path(user_dir):
    return os.path.join(user_dir, STATE_FILE)


def _log_path(user_dir):
    return os.path.join(user_dir, LOG_FILE)


def _load(user_dir):
    path = _state_path(user_dir)
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, dict) else {}


def _identity(position):
    lifecycle = position.get("lifecycle_id")
    if lifecycle:
        return "journal:" + str(lifecycle)
    pid = position.get("position_id")
    if pid:
        return str(pid)
    return "%s|%.12g|%s" % (
        position.get("side"), float(position.get("entry_price") or 0),
        position.get("entry_timestamp_ms") or position.get("entry_time") or "unknown",
    )


def _append_log(user_dir, record):
    os.makedirs(user_dir, exist_ok=True)
    path = _log_path(user_dir)
    line = json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
    with jsonl_cache.get_path_lock(path):
        with open(path, "a", encoding="utf-8") as f:
            f.write(line)


def _policy(name):
    for item in POLICIES:
        if item["name"] == name:
            return item
    return None


def observe(user_dir, symbol, position, *, sl_price, price, bar_time, favorable_price=None):
    """Observe one closed-price point and return shadow state plus optional live candidate.

    Shadow trigger latches are one-shot per policy.  The live candidate is intentionally
    retryable on a *new* closed 1m bar until the trading layer marks it resolved.  That makes
    transient safety blocks fail closed without permanently losing a later safe chance.
    """
    side = position.get("side")
    if side not in ("long", "short"):
        raise ValueError("side")
    entry = float(position.get("entry_price") or 0)
    stop = float(sl_price or 0)
    current = float(price or 0)
    if not all(math.isfinite(v) and v > 0 for v in (entry, stop, current)):
        raise ValueError("price")
    favorable = current if favorable_price is None else float(favorable_price)
    if not math.isfinite(favorable) or favorable <= 0:
        raise ValueError("favorable_price")
    if (side == "long" and favorable < current) or (side == "short" and favorable > current):
        raise ValueError("favorable_price")
    initial_r = abs(entry - stop)
    identity = _identity(position)
    sign = 1.0 if side == "long" else -1.0
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    bar_key = str(bar_time)
    new_triggers = []
    live_candidate = None
    state_path = _state_path(user_dir)
    os.makedirs(user_dir, exist_ok=True)

    with jsonl_cache.get_path_lock(state_path):
        all_state = _load(user_dir)
        s = all_state.get(symbol)
        if (not isinstance(s, dict) or s.get("position_identity") != identity
                or (s.get("entry_timestamp_ms") is not None and position.get("entry_timestamp_ms") is not None
                    and s.get("entry_timestamp_ms") != position.get("entry_timestamp_ms"))):
            s = {
                "position_identity": identity,
                "side": side,
                "entry_price": entry,
                "sl_price_at_observation": stop,
                "initial_r": initial_r,
                "entry_timestamp_ms": position.get("entry_timestamp_ms"),
                "first_observed_at": now,
                "initial_observed_contracts": float(position.get("contracts") or 0),
                "mfe_price": entry,
                "policies": {p["name"]: {"triggered": False} for p in POLICIES},
            }
        # R belongs to this lifecycle, not the currently amended protective stop.
        initial_r = float(s.get("initial_r") or 0)
        if not math.isfinite(initial_r) or initial_r <= 0:
            raise ValueError("initial_r")
        old_best = float(s.get("mfe_price") or entry)
        best = max(old_best, favorable) if side == "long" else min(old_best, favorable)
        mfe_r = max(0.0, sign * (best - entry) / initial_r)
        current_r = sign * (current - entry) / initial_r
        giveback_r = max(0.0, mfe_r - current_r)
        s.update(
            mfe_price=best,
            mfe_r=mfe_r,
            current_r=current_r,
            giveback_r=giveback_r,
            latest_price=current,
            latest_contracts=float(position.get("contracts") or 0),
            last_bar_time=bar_key,
            last_observed_at=now,
        )
        policy_state = s.setdefault("policies", {})
        for policy in POLICIES:
            ps = policy_state.setdefault(policy["name"], {"triggered": False})
            if (not ps.get("triggered") and mfe_r + _EPS >= policy["arm_r"]
                    and giveback_r + _EPS >= policy["giveback_r"]):
                trigger = {
                    "policy": policy["name"],
                    "arm_r": policy["arm_r"],
                    "giveback_threshold_r": policy["giveback_r"],
                    "counterfactual_reduce_fraction": policy["reduce_fraction"],
                    "trigger_price": current,
                    "mfe_price": best,
                    "mfe_r": mfe_r,
                    "current_r": current_r,
                    "giveback_r": giveback_r,
                    "bar_time": bar_key,
                }
                ps.update(triggered=True, **trigger)
                new_triggers.append(dict(trigger))

        live_policy = _policy(LIVE_POLICY_NAME)
        live_ps = policy_state.setdefault(LIVE_POLICY_NAME, {"triggered": False})
        live_ready = bool(
            live_policy
            and mfe_r + _EPS >= live_policy["arm_r"]
            and giveback_r + _EPS >= live_policy["giveback_r"]
            and not live_ps.get("live_resolved")
        )
        if live_ready and live_ps.get("last_live_attempt_bar_time") != bar_key:
            live_candidate = {
                "policy": LIVE_POLICY_NAME,
                "arm_r": live_policy["arm_r"],
                "giveback_threshold_r": live_policy["giveback_r"],
                "counterfactual_reduce_fraction": live_policy["reduce_fraction"],
                "trigger_price": current,
                "mfe_price": best,
                "mfe_r": mfe_r,
                "current_r": current_r,
                "giveback_r": giveback_r,
                "bar_time": bar_key,
                "initial_r": initial_r,
                "entry_price": entry,
                "position_identity": identity,
            }
            live_ps["last_live_attempt_bar_time"] = bar_key
            live_ps["last_live_candidate_at"] = now
        all_state[symbol] = s
        process_lock.save_json_atomic(state_path, all_state)

    for trigger in new_triggers:
        _append_log(user_dir, {
            "event_type": "mfe_profit_shadow_trigger",
            "symbol": symbol,
            "position_identity": identity,
            "side": side,
            "entry_price": entry,
            "initial_r": initial_r,
            "observed_at": now,
            **trigger,
        })

    result = dict(s)
    result["new_triggers"] = new_triggers
    result["live_candidate"] = live_candidate
    for key in ("mfe_r", "current_r", "giveback_r"):
        result[key] = round(float(result[key]), 6)
    return result


def mark_live_resolved(user_dir, symbol, policy, *, status, reason, position_identity=None, **metadata):
    """Durably stop future live attempts for this policy/lifecycle after a terminal outcome."""
    if policy != LIVE_POLICY_NAME or status not in ("executed", "skipped"):
        raise ValueError("live_resolution")
    path = _state_path(user_dir)
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    with jsonl_cache.get_path_lock(path):
        data = _load(user_dir)
        s = data.get(symbol)
        if not isinstance(s, dict):
            return False
        if position_identity is not None and s.get("position_identity") != position_identity:
            return False
        ps = s.setdefault("policies", {}).setdefault(policy, {"triggered": False})
        ps.update(
            live_resolved=True,
            live_status=status,
            live_reason=str(reason),
            live_resolved_at=now,
        )
        if metadata:
            ps["live_metadata"] = metadata
        data[symbol] = s
        process_lock.save_json_atomic(path, data)
    _append_log(user_dir, {
        "event_type": "mfe_profit_live_resolution",
        "symbol": symbol,
        "position_identity": s.get("position_identity"),
        "policy": policy,
        "status": status,
        "reason": str(reason),
        "resolved_at": now,
        **metadata,
    })
    return True


def get_state(user_dir, symbol=None):
    data = _load(user_dir)
    if symbol is None:
        return data
    value = data.get(symbol)
    return dict(value) if isinstance(value, dict) else None


def recent(user_dir, limit=100):
    path = _log_path(user_dir)
    if not os.path.exists(path) or limit <= 0:
        return []
    with jsonl_cache.get_path_lock(path):
        rows = jsonl_cache.tail_jsonl(path, limit)
    return rows[-limit:]
