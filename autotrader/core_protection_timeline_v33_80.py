"""v33-80: passive, append-only evidence of verified exchange protection.

A pure local writer; no exchange dependency, HTTP, trading, order, or sizing APIs.
It is called only after the pre-existing dashboard refresh has fetched the
exchange-verified protection state. It never modifies that state.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path
import threading

_FILE = "core_protection_timeline_v33_80.jsonl"
_LOCK = threading.Lock()
_LAST: dict[tuple[str, str], str] = {}
_ALLOWED = {
    "VERIFIED", "NOT_REQUIRED_FLAT", "UNKNOWN",
    "MISSING_CONFIRMED", "MISMATCH_CONFIRMED", "RESIZE_IN_PROGRESS",
}


def _positive(x):
    if isinstance(x, bool) or not isinstance(x, (float, int)):
        return None
    return float(x) if math.isfinite(x) and x > 0 else None


def _text(value, maximum=128):
    if not isinstance(value, (str, int)):
        return None
    result = str(value)
    return result[:maximum] if result else None


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        return None
    return float(value) if math.isfinite(value) else None


def normalize(symbol, position, observation, *, now=None):
    """Produce a fail-closed, nonauthoritative order-state observation."""
    if not isinstance(symbol, str) or not symbol or len(symbol) > 80:
        raise ValueError("invalid_symbol")
    if not isinstance(observation, dict):
        raise ValueError("invalid_observation")
    if position is not None and not isinstance(position, dict):
        raise ValueError("invalid_position")

    ts = now or dt.datetime.now(dt.timezone.utc)
    if not isinstance(ts, dt.datetime) or ts.tzinfo is None:
        raise ValueError("time_must_be_aware")
    ts = ts.astimezone(dt.timezone.utc)

    status = observation.get("status")
    if status not in _ALLOWED:
        status = "UNKNOWN"
    p = position or {}
    side = p.get("side") if p.get("side") in ("long", "short") else None
    expected = _positive(p.get("contracts"))
    size = _positive(observation.get("sz"))
    sl = _positive(observation.get("sl_price"))
    tp = _positive(observation.get("tp_price"))
    algo = _text(observation.get("algo_id"))
    identity = _text(p.get("position_id"))
    entry = _positive(p.get("entry_price"))

    if status == "VERIFIED":
        if (not side or expected is None or size is None
                or sl is None or tp is None or algo is None
                or abs(expected - size) > max(1e-8, expected*1e-8)):
            status = "UNKNOWN"
            reason = "invalid_verified_snapshot"
        else:
            reason = None
    elif status == "NOT_REQUIRED_FLAT":
        if position is not None:
            status = "UNKNOWN"
            reason = "flat_claim_with_position"
        else:
            reason = None
    else:
        reason = _text(observation.get("reason"), 100) or "unverified_protection"

    # Deliberately omit raw account details, credentials, AI text, and balances.
    # Full algo ID / position ID are kept only in the private mode-0600 journal.
    result = {
        "version": "v33-80",
        "observed_at_utc": ts.isoformat(timespec="seconds"),
        "source": "already_fetched_exchange_dashboard_protection",
        "authority": "observation_only",
        "symbol": symbol,
        "status": status,
        "reason": reason,
        "side": side,
        "position_id": identity,
        "entry_price": entry,
        "expected_contracts": expected,
        "algo_id": algo if status == "VERIFIED" else None,
        "live_sl": sl if status == "VERIFIED" else None,
        "live_tp": tp if status == "VERIFIED" else None,
        "live_contracts": size if status == "VERIFIED" else None,
    }
    return result


def _fingerprint(event):
    without_timestamp = {k: v for k, v in event.items() if k != "observed_at_utc"}
    return hashlib.sha256(json.dumps(
        without_timestamp, sort_keys=True, ensure_ascii=False,
        separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def record_observation(user_dir, symbol, position, observation, *, now=None):
    """Append only on meaningful state changes, including unknown/flat transitions.

    One event on process restart is intentional. Disk errors are raised to the
    caller: the surrounding display loop must log and ignore these errors.
    """
    root = Path(user_dir)
    if not root.is_dir():
        raise ValueError("user_dir_missing")
    event = normalize(symbol, position, observation, now=now)
    fingerprint = _fingerprint(event)
    cache_key = (str(root.resolve()), symbol)
    with _LOCK:
        if _LAST.get(cache_key) == fingerprint:
            return False
        payload = json.dumps(event, sort_keys=True, ensure_ascii=False, allow_nan=False)
        target = root / _FILE
        # Never traverse a symlink to an uncontrolled file.
        if target.is_symlink():
            raise ValueError("timeline_symlink_disallowed")
        flags = os.O_CREAT | os.O_APPEND | os.O_WRONLY
        flags |= getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(target, flags, 0o600)
        try:
            with os.fdopen(fd, "a", encoding="utf-8") as handle:
                handle.write(payload + "\n")
                handle.flush()
                os.fsync(handle.fileno())
        except Exception:
            raise
        _LAST[cache_key] = fingerprint
        return True
