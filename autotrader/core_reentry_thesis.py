"""Durable same-side thesis lock after authoritative CORE AI closes."""
from __future__ import annotations

import datetime as dt
import json
import math
import os

import jsonl_cache
import process_lock

STATE_FILE = "core_reentry_thesis.json"


def _path(user_dir: str) -> str:
    return os.path.join(user_dir, STATE_FILE)


def _load(user_dir: str) -> dict:
    try:
        with open(_path(user_dir), encoding="utf-8") as stream:
            data = json.load(stream)
    except FileNotFoundError:
        return {}
    return data if isinstance(data, dict) else {}


def _iso(value: dt.datetime) -> str:
    return value.isoformat(timespec="seconds")


def _parse(value) -> dt.datetime | None:
    if isinstance(value, dt.datetime):
        return value
    try:
        return dt.datetime.fromisoformat(str(value)) if value else None
    except (TypeError, ValueError):
        return None


def _update(user_dir: str, symbol: str, change):
    path = _path(user_dir)
    with jsonl_cache.get_path_lock(path):
        data = _load(user_dir)
        record = change(data.get(symbol))
        if record is None:
            data.pop(symbol, None)
        else:
            data[symbol] = record
        process_lock.save_json_atomic(path, data)
        return dict(record) if isinstance(record, dict) else None


def get(user_dir: str, symbol: str) -> dict | None:
    path = _path(user_dir)
    with jsonl_cache.get_path_lock(path):
        record = _load(user_dir).get(symbol)
        return dict(record) if isinstance(record, dict) else None


def record_ai_close(user_dir: str, symbol: str, side: str, close_time, assessment,
                    minimum_minutes: int = 30) -> dict:
    close_dt = _parse(close_time)
    if close_dt is None:
        raise ValueError("invalid_close_time")
    if side not in ("long", "short"):
        raise ValueError("invalid_closed_side")
    minimum_until = close_dt + dt.timedelta(minutes=int(minimum_minutes))
    record = {
        "symbol": symbol,
        "closed_side": side,
        "close_time": _iso(close_dt),
        "assessment": assessment,
        "minimum_until": _iso(minimum_until),
        "status": "blocked",
        "recovery_evidence": None,
        "cleared_at": None,
    }
    return _update(user_dir, symbol, lambda _old: record)


def _row(frame):
    if frame is None or len(frame) == 0:
        return None
    return frame.iloc[-1]


def _one_h_pass(side: str, frame) -> bool:
    row = _row(frame)
    if row is None:
        return False
    try:
        close = float(row["close"]); ema20 = float(row["ema_20"]); ema50 = float(row["ema_50"])
    except (KeyError, TypeError, ValueError):
        return False
    if side == "long":
        return close > ema20 > ema50
    if side == "short":
        return close < ema20 < ema50
    return False


EVIDENCE_VERSION = "post_close_utc_v1"
_JOURNAL_TZ = dt.timezone(dt.timedelta(hours=9))


def _utc_time(value, naive_timezone=_JOURNAL_TZ):
    """Journal/engine wall clocks are KST; exchange candle clocks are UTC."""
    parsed = _parse(value)
    if parsed is None:
        return None
    try:
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=naive_timezone)
        parsed = parsed.astimezone(dt.timezone.utc)
        return parsed if math.isfinite(parsed.timestamp()) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _current_closed_frame(frame, minutes, now_utc):
    row = _row(frame)
    if row is None or "timestamp" not in row:
        return False
    opened = _utc_time(row["timestamp"], dt.timezone.utc)
    if opened is None:
        return False
    span = dt.timedelta(minutes=minutes)
    age = now_utc - (opened + span)
    return dt.timedelta(0) <= age < span


def _five_m_recovery_count(side: str, frame, *, after, now) -> int:
    if frame is None or len(frame) == 0:
        return 0
    count = 0
    previous_open = None
    for _, row in frame.iloc[::-1].iterrows():
        try:
            opened = _utc_time(row["timestamp"], dt.timezone.utc)
            close = float(row["close"]); ema20 = float(row["ema_20"])
        except (KeyError, TypeError, ValueError):
            break
        if opened is None or opened < after or opened + dt.timedelta(minutes=5) > now:
            break
        if previous_open is not None and previous_open - opened != dt.timedelta(minutes=5):
            break
        passed = close > ema20 if side == "long" else close < ema20 if side == "short" else False
        if not passed:
            break
        count += 1
        previous_open = opened
    return count


def evaluate_same_side(record: dict | None, side: str, now, confirmed_1h, confirmed_5m) -> dict:
    proof = (record or {}).get("recovery_evidence") or {}
    if not record or (record.get("status") == "recovered"
                      and proof.get("evidence_version") == EVIDENCE_VERSION):
        return {"blocked": False, "recovered": True, "reason": "already_recovered",
                "one_h_pass": None, "confirmed_5m_recovery_count": 0}
    if side != record.get("closed_side"):
        return {"blocked": False, "recovered": False, "reason": "opposite_side",
                "one_h_pass": None, "confirmed_5m_recovery_count": 0}
    now_dt = _utc_time(now)
    minimum_until = _utc_time(record.get("minimum_until"))
    closed_at = _utc_time(record.get("close_time"))
    if now_dt is None or minimum_until is None or closed_at is None:
        return {"blocked": True, "recovered": False, "reason": "invalid_state",
                "one_h_pass": False, "confirmed_5m_recovery_count": 0}
    if now_dt < minimum_until:
        return {"blocked": True, "recovered": False, "reason": "minimum_lock",
                "one_h_pass": None, "confirmed_5m_recovery_count": 0}
    if confirmed_1h is None or len(confirmed_1h) == 0 or confirmed_5m is None or len(confirmed_5m) == 0:
        return {"blocked": True, "recovered": False, "reason": "recovery_evidence_missing",
                "one_h_pass": False, "confirmed_5m_recovery_count": 0}
    if not (_current_closed_frame(confirmed_1h, 60, now_dt)
            and _current_closed_frame(confirmed_5m, 5, now_dt)):
        return {"blocked": True, "recovered": False, "reason": "recovery_evidence_not_current",
                "one_h_pass": False, "confirmed_5m_recovery_count": 0}
    one_h_pass = _one_h_pass(side, confirmed_1h)
    five_count = _five_m_recovery_count(side, confirmed_5m, after=closed_at, now=now_dt)
    recovered = bool(one_h_pass and five_count >= 2)
    return {
        "blocked": not recovered,
        "recovered": recovered,
        "reason": "recovered" if recovered else "thesis_not_recovered",
        "one_h_pass": one_h_pass,
        "confirmed_5m_recovery_count": five_count,
        "evidence_version": EVIDENCE_VERSION,
        "confirmed_5m_last_open_at": _utc_time(confirmed_5m.iloc[-1]["timestamp"], dt.timezone.utc).isoformat(),
        "recovery_after": closed_at.isoformat(),
    }


def clear_recovered(user_dir: str, symbol: str, evidence: dict, now) -> dict:
    now_dt = _parse(now)
    if now_dt is None:
        raise ValueError("invalid_clear_time")

    def change(old):
        if not isinstance(old, dict):
            raise ValueError("missing_thesis_record")
        return dict(old, status="recovered", recovery_evidence=dict(evidence or {}), cleared_at=_iso(now_dt))

    return _update(user_dir, symbol, change)
