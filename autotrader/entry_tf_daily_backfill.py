"""Causal 1D entry-state backfill for trade-learning coverage; read-only/shadow only."""
from __future__ import annotations

import datetime as dt
import json
import os

import jsonl_cache
import low_follow_through_shadow

JOURNAL = "entry_tf_daily_backfill.jsonl"
KST = dt.timezone(dt.timedelta(hours=9))
VALID_STATES = {"bullish", "bearish", "mixed", "neutral"}


def _wall(value):
    parsed = low_follow_through_shadow._time(value)
    if parsed is None:
        return None
    return parsed if parsed.tzinfo is None else parsed.astimezone(KST).replace(tzinfo=None)


def evaluate_trade(life: dict, state_1d: str | None) -> dict:
    state = str(state_1d or "unknown").lower()
    resolved = state in VALID_STATES
    return {
        "trade_id": life.get("trade_id"), "symbol": life.get("symbol"),
        "entry_time": life.get("entry_time"), "state_1d": state,
        "resolved": resolved, "reason": "resolved" if resolved else "daily_state_unavailable",
        "mode": "shadow_only", "live_authority": False,
    }

def _path(user_dir: str) -> str:
    return os.path.join(user_dir, JOURNAL)


def load_latest(user_dir: str) -> dict[str, dict]:
    latest = {}
    path = _path(user_dir)
    if not os.path.exists(path):
        return latest
    with open(path, encoding="utf-8") as stream:
        for line in stream:
            try:
                row = json.loads(line)
            except (json.JSONDecodeError, TypeError):
                continue
            if isinstance(row, dict) and row.get("trade_id"):
                latest[str(row["trade_id"])] = row
    return latest


def _append(user_dir: str, row: dict) -> None:
    path = _path(user_dir)
    os.makedirs(user_dir, exist_ok=True)
    with jsonl_cache.get_path_lock(path):
        with open(path, "a", encoding="utf-8") as stream:
            stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n")
            stream.flush(); os.fsync(stream.fileno())


def refresh(user_dir: str, lifecycles: list[dict], fetcher, limit: int = 150) -> int:
    existing = load_latest(user_dir)
    rows = sorted(list(lifecycles or []), key=lambda r: _wall(r.get("entry_time")) or dt.datetime.min)
    rows = rows[-max(0, int(limit)):]
    created = 0
    for life in rows:
        tid = str(life.get("trade_id") or "")
        entry = _wall(life.get("entry_time"))
        if not tid or tid in existing or entry is None or not life.get("symbol"):
            continue
        try:
            state = fetcher(life["symbol"], entry)
        except Exception:
            continue
        row = evaluate_trade(life, state)
        row["observed_at"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        _append(user_dir, row)
        existing[tid] = row
        created += 1
    return created


def make_okx_fetcher(cfg):
    import okx_client
    clients = {}; cache = {}

    def fetch(symbol, entry_time):
        entry = _wall(entry_time)
        if entry is None:
            return "unknown"
        batch = cache.get(symbol)
        if not batch or not (batch["start"] <= entry - dt.timedelta(days=70) and batch["end"] >= entry):
            client = clients.setdefault(symbol, okx_client.OkxClient(symbol, cfg))
            since_time = entry - dt.timedelta(days=250)
            since = int(since_time.replace(tzinfo=KST).timestamp() * 1000)
            raw = client.exchange.fetch_ohlcv(symbol, "1d", since=since, limit=300)
            rows = []
            for item in raw or []:
                if not isinstance(item, (list, tuple)) or len(item) < 6:
                    continue
                opened = dt.datetime.fromtimestamp(float(item[0]) / 1000.0, tz=dt.timezone.utc).astimezone(KST).replace(tzinfo=None)
                rows.append({
                    "time": opened.isoformat(), "close_time": (opened + dt.timedelta(days=1)).isoformat(),
                    "open": float(item[1]), "high": float(item[2]), "low": float(item[3]),
                    "close": float(item[4]), "volume": float(item[5]),
                })
            if not rows:
                return "unknown"
            batch = {"start": _wall(rows[0]["time"]), "end": _wall(rows[-1]["close_time"]), "rows": rows}
            cache[symbol] = batch
        frame = low_follow_through_shadow.causal_indicator_frame(batch["rows"], entry)
        return low_follow_through_shadow.classify_state(frame)

    return fetch
