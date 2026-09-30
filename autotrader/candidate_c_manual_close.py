"""Durable Candidate C per-symbol manual-close request and re-entry cooldown."""
from __future__ import annotations

import json
import os
import time
import uuid

import jsonl_cache
import process_lock


def _path(user_dir: str) -> str:
    return os.path.join(user_dir, "candidate_c_manual_close.json")


def _load(user_dir: str) -> dict:
    try:
        with open(_path(user_dir), encoding="utf-8") as stream:
            data = json.load(stream)
    except FileNotFoundError:
        return {}
    if not isinstance(data, dict):
        raise ValueError("candidate manual close state must be an object")
    return data


def _update(user_dir: str, symbol: str, change):
    path = _path(user_dir)
    with jsonl_cache.get_path_lock(path):
        data = _load(user_dir)
        record = change(data.get(symbol))
        data[symbol] = record
        process_lock.save_json_atomic(path, data)
        return dict(record)


def get(user_dir: str, symbol: str) -> dict | None:
    path = _path(user_dir)
    with jsonl_cache.get_path_lock(path):
        record = _load(user_dir).get(symbol)
        return dict(record) if isinstance(record, dict) else None


def block_reason(record: dict | None, *, now: float | None = None) -> str | None:
    if not record or record.get("status") in ("failed", "completed"):
        return None
    now = time.time() if now is None else float(now)
    if record.get("status") != "confirmed" or record.get("confirmed_at") is None:
        return "candidate_manual_close_unresolved"
    release_at = record.get("release_at")
    if release_at is None:
        return "candidate_manual_close_unresolved"
    if now < float(release_at):
        return "candidate_manual_close_cooldown"
    return None


def reserve(user_dir: str, symbol: str, *, position_epoch: str, now: float | None = None) -> dict:
    if not isinstance(position_epoch, str) or not position_epoch:
        raise ValueError("position_epoch required")
    now = time.time() if now is None else float(now)

    def change(old):
        if isinstance(old, dict) and block_reason(old, now=now) is not None:
            if old.get("position_epoch") != position_epoch:
                raise ValueError("candidate manual close already active for different position")
            return old
        return {
            "close_id": "ccm" + uuid.uuid4().hex[:27],
            "position_epoch": position_epoch,
            "started_at": now,
            "status": "reserved",
            "confirmed_at": None,
            "release_at": None,
            "error": None,
            "last_result": None,
        }

    return _update(user_dir, symbol, change)


def patch(user_dir: str, symbol: str, close_id: str, **fields) -> dict:
    def change(old):
        if not isinstance(old, dict) or old.get("close_id") != close_id:
            raise ValueError("candidate manual close identity changed")
        return dict(old, **fields)
    return _update(user_dir, symbol, change)


def confirm(
    user_dir: str, symbol: str, close_id: str, *,
    cooldown_seconds: int = 900, now: float | None = None,
) -> dict:
    now = time.time() if now is None else float(now)
    if cooldown_seconds < 0:
        raise ValueError("cooldown_seconds must be non-negative")

    def change(old):
        if not isinstance(old, dict) or old.get("close_id") != close_id:
            raise ValueError("candidate manual close identity changed")
        if old.get("confirmed_at") is not None:
            return old
        return dict(
            old,
            status="confirmed",
            confirmed_at=now,
            release_at=now + cooldown_seconds,
            error=None,
        )
    return _update(user_dir, symbol, change)


def fail(
    user_dir: str, symbol: str, close_id: str, error: str, *,
    now: float | None = None,
) -> dict:
    now = time.time() if now is None else float(now)

    def change(old):
        if not isinstance(old, dict) or old.get("close_id") != close_id:
            raise ValueError("candidate manual close identity changed")
        return dict(old, status="failed", failed_at=now, error=str(error)[:500])
    return _update(user_dir, symbol, change)


def status(user_dir: str, symbol: str, *, now: float | None = None) -> dict:
    now = time.time() if now is None else float(now)
    record = get(user_dir, symbol)
    reason = block_reason(record, now=now)
    release_at = record.get("release_at") if record else None
    remaining = max(0.0, float(release_at) - now) if release_at is not None else None
    return {
        "record": record,
        "block_reason": reason,
        "blocked": reason is not None,
        "release_at": release_at,
        "remaining_seconds": remaining,
    }
