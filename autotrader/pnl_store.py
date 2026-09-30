import datetime
import json
import os

import process_lock


KST = datetime.timezone(datetime.timedelta(hours=9))
SCHEMA_VERSION = 3


def _path(user_dir: str) -> str:
    return os.path.join(user_dir, "pnl_state.json")


def _load_record(user_dir: str) -> dict:
    path = _path(user_dir)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def load_baseline(user_dir: str):
    return _load_record(user_dir).get("baseline_equity")


def load_baseline_set_at(user_dir: str):
    return _load_record(user_dir).get("baseline_set_at")


def load_baseline_metadata(user_dir: str) -> dict:
    rec = dict(_load_record(user_dir))
    # Old schema files may contain baseline_set_at_utc from a later migration.
    # Never treat it as the original reset instant unless reset_event explicitly
    # marks it trustworthy.
    if rec.get("baseline_time_source") != "reset_event":
        rec.pop("baseline_set_at_ms", None)
    return rec


def save_baseline(user_dir: str, equity: float, now: datetime.datetime | None = None) -> None:
    os.makedirs(user_dir, exist_ok=True)
    now = now or datetime.datetime.now(datetime.timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=datetime.timezone.utc)
    utc_now = now.astimezone(datetime.timezone.utc)
    local_now = now.astimezone(KST)

    rec = _load_record(user_dir)
    rec.update({
        "schema_version": max(int(rec.get("schema_version") or 0), SCHEMA_VERSION),
        "baseline_equity": float(equity),
        "baseline_set_at": local_now.date().isoformat(),
        "baseline_set_at_utc": utc_now.isoformat().replace("+00:00", "Z"),
        "baseline_set_at_ms": int(utc_now.timestamp() * 1000),
        "baseline_time_source": "reset_event",
    })
    process_lock.save_json_atomic(_path(user_dir), rec)
