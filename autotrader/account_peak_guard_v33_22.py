"""v33-22: account-wide intraday equity peak protection for NEW risk only.

Does not place, cancel, reduce or modify live positions/orders.
Uses verified cash flows to normalize equity; when flow verification is not
available, conservatively uses raw equity (which can block after withdrawals).
The guard does not replace the existing daily-start or group loss guards.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from pathlib import Path
import json
import math
import time

import daily_equity_flow_adjustment as flow_adjustment

KST = timezone(timedelta(hours=9))


def _number(value):
    if isinstance(value, bool):
        return None
    try:
        v = float(value)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError, OverflowError):
        return None


def evaluate(user_dir, baseline, current_equity, limit_pct, *, now_ms=None):
    """Return account peak drawdown decision, conservatively blocked on bad evidence.

    Inputs are trading-account equity (including unrealized PnL), not
    completed-lifecycle PnL. Local KST date is always the same risk session.
    """
    now_ms = int(time.time() * 1000) if now_ms is None else int(now_ms)
    limit = _number(limit_pct)
    equity = _number(current_equity)
    start = _number(baseline.get("start_equity") if isinstance(baseline, dict) else None)
    if limit is None or limit <= 0:
        return dict(blocked=False, status="DISABLED", drawdown_pct=0.0, peak_equity=None)
    bad = dict(blocked=True, status="UNKNOWN_FAIL_CLOSED", drawdown_pct=None,
               peak_equity=None, limit_pct=limit)
    if start is None or start <= 0 or equity is None or equity < 0:
        return dict(bad, reason="invalid_equity")
    try:
        day_text = str(baseline["trading_date"])
        day = datetime.strptime(day_text, "%Y-%m-%d").date()
        current_date = datetime.fromtimestamp(now_ms/1000, KST).date()
        if current_date != day:
            return dict(bad, reason="date_mismatch")
        day_ms = int(datetime.combine(day, datetime.min.time(), tzinfo=KST).timestamp()*1000)
        folder = Path(user_dir)
        snapshot_path = folder/"capital_flow_equity_snapshots.jsonl"
        flow_state = json.loads((folder/"capital_flow_state.json").read_text())
        flow_check = flow_adjustment.compute(start, equity, baseline, flow_state, now_ms=now_ms)
        verified = flow_check.get("status") == "KNOWN"
        flows = []
        if verified:
            for event in flow_state["events"]:
                ts = int(event["ts_ms"])
                if day_ms <= ts <= now_ms:
                    flows.append((ts, float(event["value_usdt"])))
            flows.sort()
        adjusted_current = equity - sum(v for _, v in flows)
        peak = start
        max_intraday_drawdown = 0.0
        count = 0
        flow_index = 0
        flow_so_far = 0.0
        last_ts = -1
        with snapshot_path.open(encoding="utf-8") as handle:
            for line in handle:
                try:
                    item = json.loads(line)
                    ts = item.get("ts_ms")
                    val = _number(item.get("equity"))
                except (ValueError, TypeError, AttributeError):
                    continue
                if type(ts) is not int or val is None or val < 0:
                    continue
                if not day_ms <= ts <= now_ms:
                    continue
                # Append-only observation ledger must not change historic order.
                if ts < last_ts:
                    return dict(bad, reason="snapshot_clock_out_of_order")
                last_ts = ts
                if verified:
                    while flow_index < len(flows) and flows[flow_index][0] <= ts:
                        flow_so_far += flows[flow_index][1]
                        flow_index += 1
                normalized = val - flow_so_far
                if not math.isfinite(normalized) or normalized < 0:
                    return dict(bad, reason="invalid_normalized_equity")
                peak = max(peak, normalized)
                if peak > 0:
                    max_intraday_drawdown = max(
                        max_intraday_drawdown, max(0., (peak-normalized)/peak*100.)
                    )
                count += 1
        if count == 0:
            return dict(bad, reason="today_snapshots_missing")
        peak = max(peak, adjusted_current)
        if not (math.isfinite(peak) and peak > 0 and math.isfinite(adjusted_current)):
            return dict(bad, reason="peak_nonfinite")
        current_drawdown_pct = max(0., (peak - adjusted_current)/peak*100.)
        # Once breached, today's append-only equity history keeps the gate
        # closed even if equity temporarily rebounds. No restart-reset state.
        max_intraday_drawdown = max(max_intraday_drawdown, current_drawdown_pct)
        return dict(blocked=max_intraday_drawdown >= limit,
                    status="KNOWN" if verified else "RAW_CONSERVATIVE",
                    reason=None if verified else str(flow_check.get("reason")),
                    drawdown_pct=max_intraday_drawdown,
                    current_drawdown_pct=current_drawdown_pct,
                    peak_equity=peak, limit_pct=limit,
                    current_equity=adjusted_current, snapshot_count=count)
    except (OSError, ValueError, KeyError, TypeError, OverflowError, json.JSONDecodeError):
        return dict(bad, reason="equity_history_unavailable")
