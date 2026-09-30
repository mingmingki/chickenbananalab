from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass

from adaptive_exit_engine import AdaptiveExitContext, AdaptiveExitEngine


@dataclass(frozen=True)
class ReplayResult:
    trade_id: str
    resolved: bool
    plan_hash: str | None = None
    configured_notional: float | None = None
    effective_notional: float | None = None
    planned_loss_usdt: float | None = None
    risk_violation: bool = False
    stop_loosen_violation: bool = False
    legacy_net: float | None = None
    adaptive_net: float | None = None
    legacy_fee: float = 0.0
    adaptive_fee: float = 0.0
    legacy_actions: int = 0
    adaptive_actions: int = 0
    mae_r: float | None = None
    mfe_r: float | None = None
    mfe_giveback_pct: float | None = None
    window_id: str | None = None
    reason: str | None = None


def _parse(value):
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _cutoff_rows(candles, cutoff):
    rows=[]
    for row in candles or []:
        stamp=_parse(row.get("time") or row.get("close_time"))
        if stamp is not None and stamp <= cutoff:
            rows.append((stamp,row))
    rows.sort(key=lambda pair:pair[0])
    return [row for _,row in rows]


def _finite(value):
    try: x=float(value)
    except (TypeError,ValueError): return None
    return x if math.isfinite(x) else None


def replay_lifecycle(user_dir, lifecycle: dict, policy: dict) -> ReplayResult:
    trade_id=str(lifecycle.get("trade_id") or "unknown")
    entry_time=_parse(lifecycle.get("entry_time"))
    side=lifecycle.get("side")
    entry=_finite(lifecycle.get("entry_price"))
    if entry_time is None or side not in ("long","short") or not entry or entry <= 0:
        return ReplayResult(trade_id=trade_id,resolved=False,reason="missing_entry_identity")
    history=_cutoff_rows(lifecycle.get("candles") or [],entry_time)
    if not history:
        return ReplayResult(trade_id=trade_id,resolved=False,reason="missing_pre_entry_candles")
    atr=None
    for row in reversed(history):
        for key in ("atr","atr_14","atr14"):
            atr=_finite(row.get(key))
            if atr and atr>0: break
        if atr and atr>0: break
    lows=[_finite(r.get("low")) for r in history]
    highs=[_finite(r.get("high")) for r in history]
    lows=[x for x in lows if x is not None]
    highs=[x for x in highs if x is not None]
    if not atr or not lows or not highs:
        return ReplayResult(trade_id=trade_id,resolved=False,reason="missing_pre_entry_structure")
    equity=_finite(lifecycle.get("equity_usdt")) or 0.0
    risk_budget=_finite(lifecycle.get("risk_budget_usdt")) or 0.0
    leverage=_finite(lifecycle.get("leverage")) or 1.0
    margin=_finite(lifecycle.get("configured_margin_usdt"))
    order_cap=_finite(lifecycle.get("order_cap_notional"))
    legacy_notional=_finite(lifecycle.get("legacy_notional")) or 0.0
    ctx=AdaptiveExitContext(
        symbol=str(lifecycle.get("symbol") or "UNKNOWN"),side=side,entry_price=entry,
        current_quantity=(legacy_notional/entry if legacy_notional>0 else 1.0),current_stop=None,
        equity_usdt=equity,trade_risk_budget_usdt=risk_budget,atr=atr,
        sizing_mode=str(lifecycle.get("sizing_mode") or "VARIABLE_RISK"),
        configured_margin_usdt=margin,leverage=leverage,order_cap_notional=order_cap,
        structural_support=min(lows) if side=="long" else None,
        structural_resistance=max(highs) if side=="short" else None,
        near_resistance=min([h for h in highs if h>entry],default=None) if side=="long" else None,
        near_support=max([l for l in lows if l<entry],default=None) if side=="short" else None,
        continuation_resistance=max(highs) if side=="long" else None,
        continuation_support=min(lows) if side=="short" else None,
        mode="REPLAY",decision_timestamp=int(entry_time.timestamp()*1000),
        input_snapshot_hash=str(lifecycle.get("input_snapshot_hash") or trade_id),
        source_candle_timestamps=tuple(int(_parse(r.get("time")).timestamp()*1000) for r in history if _parse(r.get("time")) is not None),
    )
    plan=AdaptiveExitEngine(policy).plan(ctx)
    legacy_net=_finite(lifecycle.get("lifecycle_net"))
    legacy_fee=abs(_finite(lifecycle.get("fee")) or 0.0)
    # Counterfactual v1 is conservative: rejected adaptive entries realize 0; otherwise
    # retain the observed lifecycle net until a full intrabar simulator resolves targets/stops.
    adaptive_net=0.0 if not plan.entry_allowed else legacy_net
    scale=(plan.effective_notional/legacy_notional) if legacy_notional>0 and plan.effective_notional is not None else 1.0
    if adaptive_net is not None and plan.entry_allowed and legacy_notional>0:
        adaptive_net*=scale
    adaptive_fee=legacy_fee*max(0.0,min(1.0,scale))
    budget=max(risk_budget,0.0)
    violation=bool(plan.planned_loss_usdt > budget + 1e-8) if budget>0 else False
    return ReplayResult(
        trade_id=trade_id,resolved=True,plan_hash=plan.plan_hash,
        configured_notional=plan.configured_notional,effective_notional=plan.effective_notional,
        planned_loss_usdt=plan.planned_loss_usdt,risk_violation=violation,
        stop_loosen_violation=False,legacy_net=legacy_net,adaptive_net=adaptive_net,
        legacy_fee=legacy_fee,adaptive_fee=adaptive_fee,
        legacy_actions=int(lifecycle.get("partial_reduction_count") or 0)+1,
        adaptive_actions=0 if not plan.entry_allowed else 1,
        mae_r=_finite(lifecycle.get("mae_r")),mfe_r=_finite(lifecycle.get("mfe_r")),
        mfe_giveback_pct=_finite(lifecycle.get("mfe_giveback_pct")),
        window_id=str(lifecycle.get("window_id") or entry_time.date()),
        reason=plan.reason_code,
    )
