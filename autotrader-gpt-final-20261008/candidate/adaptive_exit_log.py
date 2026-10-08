from __future__ import annotations

import json
import os
import threading
from pathlib import Path

_LOCK = threading.Lock()
_FILENAME = "adaptive_exit_plans.jsonl"


def _path(user_dir) -> Path:
    return Path(user_dir) / _FILENAME


def _audit_key(record: dict) -> tuple:
    return (
        record.get("symbol"), record.get("decision_timestamp"),
        record.get("policy_hash"), record.get("input_snapshot_hash"),
        record.get("mode"),
    )


def _read_all(user_dir) -> tuple[list[dict], dict]:
    path = _path(user_dir)
    rows = []
    corrupt = 0
    if path.exists():
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                text = line.strip()
                if not text:
                    continue
                try:
                    value = json.loads(text)
                except (json.JSONDecodeError, TypeError):
                    corrupt += 1
                    continue
                if isinstance(value, dict):
                    rows.append(value)
                else:
                    corrupt += 1
    return rows, {"corrupt_lines": corrupt, "valid_records": len(rows)}


def append_plan(user_dir, record: dict) -> bool:
    if not isinstance(record, dict):
        raise TypeError("record must be dict")
    path = _path(user_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    key = _audit_key(record)
    if not all((key[0], key[2], key[4])):
        raise ValueError("adaptive exit audit record missing identity")
    with _LOCK:
        rows, _ = _read_all(user_dir)
        if any(_audit_key(row) == key for row in rows):
            return False
        payload = json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
        with path.open("a", encoding="utf-8") as f:
            f.write(payload + "\n")
            f.flush()
            os.fsync(f.fileno())
    return True


def load_recent(user_dir, limit: int) -> list[dict]:
    rows, _ = _read_all(user_dir)
    if limit <= 0:
        return []
    return rows[-int(limit):]


def load_diagnostics(user_dir) -> dict:
    _, diagnostics = _read_all(user_dir)
    return diagnostics
