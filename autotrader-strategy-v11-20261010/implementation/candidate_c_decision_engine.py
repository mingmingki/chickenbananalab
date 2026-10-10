"""Phase 3.6 Part C - 단일 Candidate C 결정 엔진.

backtest_engine.py, portfolio_mtm_engine.py, 실거래 trader.py adapter가 전략
판단을 각자 다시 구현하지 않도록, "무엇을 할지"를 결정하는 로직 전체를 이
모듈 하나로 통일한다. 이 모듈은 주문을 내지 않는다 - typed Intent만
반환하며, 그 Intent를 실제로 체결하는 방법(백테스트 시뮬레이션 vs 실제 OKX
주문)은 각 adapter의 책임이다. Task 3~6(candidate_c_timeframe_contract.py/
candidate_c_setup_tracker.py/candidate_c_exit_management.py/
candidate_c_reversal_state_machine.py)을 그대로 재사용하고 로직을 새로
만들지 않는다.

ATR/EMA는 strategy_indicators.py(기존, 변경 없음)를 그대로 재사용해 실제
confirmed 4H OHLC로 계산한다 - 합성 검증에서 썼던 ATR=1.0 proxy는 여기서
쓰지 않는다(Part A에서 지적된 한계를 여기서 닫는다)."""
from __future__ import annotations

import datetime
import hashlib
import json
import math

import pandas as pd
import market_structure
from dataclasses import dataclass, field, replace

import candidate_c_exit_management as cem
import entry_overextension_guard
import unified_trade_guard
import adaptive_reduction
import candidate_c_reversal_state_machine as rsm
import candidate_c_setup_tracker as st
import candidate_c_timeframe_contract as tfc
import strategy_indicators as si
import candidate_c_strategy_policy as strategy_policy_contract
import adaptive_exit_log
from adaptive_exit_engine import AdaptiveExitContext, AdaptiveExitEngine, compute_structural_stop, apply_monotonic_stop
from adaptive_exit_policy import policy_sha256 as adaptive_policy_sha256, validate_adaptive_exit_policy

# Existing Candidate accounting assumptions: two 5bp fees and two 3bp
# spread/slippage legs, shared by bounded RR and the wide-stop sizing fallback.
CANDIDATE_C_ROUNDTRIP_COST_RATE = 2 * (0.0005 + (3.0 + 3.0) / 10000.0)

INTENT_NO_ACTION = "NoAction"
INTENT_ENTRY = "EntryIntent"
INTENT_REDUCE = "ReduceIntent"
INTENT_EXIT = "ExitIntent"
INTENT_REVERSAL = "ReversalIntent"
INTENT_STOP_UPDATE = "StopUpdateIntent"  # 사용자 지시의 "예시 책임" 목록을 넘어선
# 확장 - trailing/profit-lock stop 갱신은 "아무것도 안 함"이 아니라 실제로
# 거래소 보호주문을 갱신해야 하는 실행 가능한 결정이므로 별도 타입이 필요하다.


def compute_confirmed_4h_indicators(bars_4h_confirmed_up_to_asof: list[dict]) -> dict:
    """as-of 시점까지 확정된 4H 캔들 전체(오름차순, confirm=1만)로 EMA20/EMA50/
    ATR14를 실제로 계산한다(strategy_indicators.py 재사용, 워밍업<14면 전부
    None - 지어내지 않음)."""
    augmented = si.augment_with_indicators(bars_4h_confirmed_up_to_asof, donchian_n=20)
    last = augmented[-1]
    return {"ema_20": last["ema_20"], "ema_50": last["ema_50"], "atr_14": last["atr_14"], "close": last["close"]}


def direction_from_4h_indicators(*, ema_20, ema_50, close) -> str:
    """candidate_c_preregistration_v2.json direction_4h 규칙 그대로."""
    if ema_20 is None or ema_50 is None or close is None:
        return "NONE"
    if close > ema_20 > ema_50:
        return "LONG"
    if close < ema_20 < ema_50:
        return "SHORT"
    return "NONE"


def weakening_from_1h(*, side: str, close_1h: float, ema_20_1h: float | None) -> bool:
    """candidate_c_preregistration_v2.json structural_weakening_1h 규칙 그대로."""
    if ema_20_1h is None:
        return False
    if side == "long":
        return close_1h < ema_20_1h
    return close_1h > ema_20_1h


def _candidate_structure_snapshot(bars: list[dict]) -> dict:
    if not bars:
        return {}
    try:
        df = pd.DataFrame(bars).copy()
        if "volume" not in df.columns:
            df["volume"] = 0.0
        return market_structure.compute(df)
    except Exception:
        return {}


def _candidate_tf_mixed(*directions: str) -> bool:
    known = [d for d in directions if d in ("LONG", "SHORT")]
    return len(set(known)) > 1


def profit_lock_cost_metadata_ready(position: dict) -> bool:
    """Incomplete live accounting must skip profit-lock instead of crashing management."""
    required=("effective_entry_price","entry_fee_usdt","contract_size",
              "fee_rate","spread_bps","slippage_bps")
    for key in required:
        value=position.get(key)
        if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(float(value)):
            return False
        if key in ("effective_entry_price","contract_size") and float(value)<=0:
            return False
        if key not in ("effective_entry_price","contract_size") and float(value)<0:
            return False
    return True


def lower_tf_entry_directions(*, bars_1h, bars_5m, as_of_ms, indicator_fn):
    directions = []
    for bars in (bars_1h, bars_5m):
        visible = [b for b in bars if b.get("confirm") == 1
                   and b.get("close_time_ms", 0) <= as_of_ms]
        try:
            row = indicator_fn(visible, donchian_n=20)[-1]
            directions.append(direction_from_4h_indicators(
                ema_20=row.get("ema_20"), ema_50=row.get("ema_50"), close=row.get("close")))
        except (ValueError, TypeError, KeyError, IndexError):
            directions.append("NONE")
    return tuple(directions)


def entry_direction_eligible(side, *, direction_4h, direction_1h, direction_5m, atr_4h):
    wanted = "LONG" if side == "long" else "SHORT"
    return (atr_4h is not None and atr_4h > 0 and (
        direction_4h == wanted or unified_trade_guard.entry_size_fraction(
            side=side, primary_direction=direction_4h,
            direction_1h=direction_1h, direction_5m=direction_5m)
        == unified_trade_guard.PILOT_ENTRY_FRACTION))


def observe_entry_setups(tracker, symbol, snap, *, bars_4h, bars_1h, bars_5m, indicator_fn):
    """One shared qualification contract for live and historical adapters."""
    row = indicator_fn(bars_4h, donchian_n=20)[-1]
    direction4 = direction_from_4h_indicators(
        ema_20=row.get("ema_20"), ema_50=row.get("ema_50"), close=row.get("close"))
    direction1, direction5 = lower_tf_entry_directions(
        bars_1h=bars_1h, bars_5m=bars_5m,
        as_of_ms=snap.bar_5m["close_time_ms"], indicator_fn=indicator_fn)
    for side in ("long", "short"):
        tracker.observe(symbol, side, snap.bar_10m_current["open_time_ms"],
            tfc.donchian_setup_condition(snap, side),
            entry_eligible=entry_direction_eligible(side, direction_4h=direction4,
                direction_1h=direction1, direction_5m=direction5, atr_4h=row.get("atr_14")))


def _candidate_entry_overextension_gate(
    *, side: str, entry_price: float, bars_1h: list[dict], atr14_4h,
    as_of_ms: int, indicator_fn, bars_5m: list[dict] | None = None,
) -> dict:
    try:
        one_h = indicator_fn(bars_1h, donchian_n=20)
        ema20_1h = one_h[-1]["ema_20"] if one_h else None
        cutoff = as_of_ms - 24 * 60 * 60 * 1000
        refs = [b for b in bars_1h if b.get("confirm") == 1 and b["close_time_ms"] <= cutoff]
        reference_24h_price = refs[-1]["close"] if refs else None
    except Exception:
        ema20_1h = reference_24h_price = None
    result = entry_overextension_guard.evaluate(
        side=side, entry_price=entry_price, ema20_1h=ema20_1h,
        atr14_4h=atr14_4h, reference_24h_price=reference_24h_price,
    )
    if not result.get("allowed") or not bars_5m:
        return result
    try:
        confirmed = [b for b in bars_5m if b.get("confirm") == 1 and b.get("close_time_ms", 0) <= as_of_ms]
        confirmed = sorted(confirmed, key=lambda b: b["close_time_ms"])[-200:]
        if len(confirmed) < 7:
            return {**result, "allowed": False, "reason": "recent_entry_data_unavailable"}
        if any(b["close_time_ms"] - a["close_time_ms"] != 5 * 60 * 1000
               for a, b in zip(confirmed, confirmed[1:])):
            return {**result, "allowed": False, "reason": "recent_entry_data_unavailable"}
        indicators_5m = indicator_fn(confirmed, donchian_n=20)
        atr_5m = indicators_5m[-1].get("atr_14") if indicators_5m else None
        metrics = unified_trade_guard.recent_entry_metrics(
            side=side, current_price=float(entry_price),
            closes=[float(b["close"]) for b in confirmed], atr=float(atr_5m),
        )
        recent = unified_trade_guard.evaluate_recent_entry(
            side=side, current_price=float(entry_price),
            reference_30m_price=metrics.get("reference_30m_price"),
            move_30m_atr=metrics.get("move_30m_atr"),
            pullback_atr=metrics.get("pullback_atr"),
        )
        result.update(metrics)
        result["recent_entry_guard"] = recent
        if not recent["allowed"]:
            result["allowed"] = False
            result["reason"] = recent["reason"]
    except (KeyError, TypeError, ValueError, IndexError, AttributeError):
        return {**result, "allowed": False, "reason": "recent_entry_data_unavailable"}
    return result


def sha256_of(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def make_idempotency_key(*, account_id: str, symbol: str, strategy_id: str, key_id: str, decision_timestamp: int, config_hash: str) -> str:
    """key_id는 setup_id(신규진입) 또는 position_epoch(기존 포지션 관리)다 -
    같은 (계좌, 심볼, 전략, setup/epoch, 시각, config)이면 항상 같은 키."""
    return sha256_of({
        "account_id": account_id, "symbol": symbol, "strategy_id": strategy_id,
        "key_id": key_id, "decision_timestamp": decision_timestamp, "config_hash": config_hash,
    })


@dataclass(frozen=True)
class DecisionContext:
    account_id: str
    symbol: str
    strategy_id: str
    config_version_id: str
    config_hash: str
    # 2026-09-03 STOP-SHIP 수정(Phase 3.10.2 Item 5) - 이전에는 이 필드
    # 자체가 없어서 decide()가 EntryIntent를 만들 때 risk_per_trade_pct를
    # 하드코딩된 0.25로 채웠다 - 사용자가 activate한 실제 config 값(예: 1.0)이
    # 사이징에 전혀 반영되지 않는 STOP-SHIP 결함이었다. None이면(활성 config
    # 자체가 없거나 그 config에 risk_per_trade_pct가 없을 때) 값을 지어내지
    # 않고 신규진입 자체를 막는다(아래 decide()의 entry 분기 참고).
    risk_per_trade_pct: float | None
    sizing_mode: str = "VARIABLE_RISK"
    strategy_policy: dict | None = None
    adaptive_exit_mode: str = "OFF"
    adaptive_exit_policy: dict | None = None
    adaptive_audit_user_dir: str | None = None
    adaptive_equity_usdt: float | None = None
    adaptive_fixed_margin_usdt: float | None = None
    adaptive_leverage: float = 1.0
    adaptive_order_cap_notional: float | None = None
    adaptive_approved_policy_hash: str = ""
    risk_adaptive_partials: bool = False


def _validated_policy(ctx: DecisionContext) -> dict:
    if ctx.strategy_policy is None:
        raise strategy_policy_contract.StrategyPolicyError(
            "active immutable strategy_policy is required"
        )
    return strategy_policy_contract.validate_strategy_policy(ctx.strategy_policy)


def _risk_requirement_satisfied(ctx: DecisionContext) -> bool:
    try:
        required = strategy_policy_contract.requires_risk_per_trade_pct(
            ctx.sizing_mode,
        )
    except strategy_policy_contract.StrategyPolicyError:
        return False
    return not required or ctx.risk_per_trade_pct is not None


@dataclass(frozen=True)
class Intent:
    kind: str
    account_id: str
    symbol: str
    strategy_id: str
    setup_id: str | None
    position_epoch: str | None
    config_version_id: str
    config_hash: str
    decision_timestamp: int
    source_candle_close_timestamp: int
    side: str | None
    idempotency_key: str
    reason_code: str
    input_snapshot_hash: str
    raw_stop_price: float | None = None
    raw_target_price: float | None = None
    requested_risk_pct: float | None = None
    reduce_quantity: float | None = None
    target_residual: float | None = None
    target_side: str | None = None
    strategy_policy: dict | None = None
    # 1H structural state at the moment this entry decision was made.
    # A newly opened position must inherit this baseline so an already-weak
    # market is not misread as a fresh False->True weakening edge 5m later.
    entry_weakening_baseline: bool | None = None
    adaptive_mode: str | None = None
    adaptive_policy_hash: str | None = None
    entry_size_fraction: float = 1.0
    pilot_entry: bool = False
    entry_attempt: bool = False
    entry_validation_values: dict | None = None
    reduction_plan: dict | None = None
    reduction_policy_hash: str | None = None
    # LIVE_BOUNDED wide-stop handling for intents produced by the shared
    # Candidate C decision engine. "risk_cap" is allowed only on the first
    # fresh setup edge; subsequent rechecks stay blocked. "legacy" preserves
    # older/manual callers that do not participate in this policy.
    adaptive_wide_stop_policy: str = "legacy"
    # Remember admitted wide geometry across order-price refreshes, so a
    # small move towards SL cannot restore full-margin sizing.
    adaptive_wide_stop_risk_sizing: bool = False


def _current_position_context(current_position: dict | None) -> tuple[str | None, str | None]:
    if current_position is None:
        return None, None
    return current_position["side"], current_position["position_id"]


def _decide_legacy(
    ctx: DecisionContext,
    *,
    as_of_ms: int,
    bars_4h_confirmed_up_to_asof: list[dict],
    bars_1h_confirmed_up_to_asof: list[dict],
    bars_1d_confirmed_up_to_asof: list[dict],
    bars_5m_for_10m_up_to_asof: list[dict],
    setup_tracker: st.SetupTracker,
    epoch_store: cem.PositionEpochStore,
    reversal_machine: rsm.SymbolReversalMachine,
    current_position: dict | None,
    weakening_prev: bool,
    lot_step: float,
    min_size: float,
    indicator_fn=None,
) -> Intent:
    """호출 시점마다 정확히 하나의 Intent를 반환한다(NoAction 포함). 이 함수는
    상태를 직접 변경하지 않는다(setup_tracker.observe/record_attempt_outcome,
    reversal_machine의 전이, epoch_store.save 등은 전부 호출부가 이 Intent를
    "실행"하기로 확정한 뒤 별도로 호출해야 한다) - 결정과 실행/영속화를
    분리해서, 실패한 실행이 결정 상태를 오염시키지 않게 한다."""
    action_tick_ms = as_of_ms
    action_bar = next((b for b in bars_5m_for_10m_up_to_asof
                       if b["open_time_ms"] == action_tick_ms and b.get("confirm") == 1), None)
    if action_bar is not None:
        as_of_ms = action_bar["close_time_ms"]
    bars_4h_confirmed_up_to_asof = [b for b in bars_4h_confirmed_up_to_asof
                                  if b.get("confirm") == 1 and b["close_time_ms"] <= as_of_ms]
    bars_1h_confirmed_up_to_asof = [b for b in bars_1h_confirmed_up_to_asof
                                  if b.get("confirm") == 1 and b["close_time_ms"] <= as_of_ms]
    indicator_fn = indicator_fn or si.augment_with_indicators
    input_snapshot_hash = sha256_of({
        "as_of_ms": as_of_ms,
        "bars_4h_tail": bars_4h_confirmed_up_to_asof[-5:] if bars_4h_confirmed_up_to_asof else [],
        "bars_1h_tail": bars_1h_confirmed_up_to_asof[-5:] if bars_1h_confirmed_up_to_asof else [],
        "bars_5m_tail": bars_5m_for_10m_up_to_asof[-5:] if bars_5m_for_10m_up_to_asof else [],
    })
    try:
        policy = _validated_policy(ctx)
    except strategy_policy_contract.StrategyPolicyError:
        policy = None

    def _no_action(reason: str, source_close_ts: int | None = None) -> Intent:
        cur_side, cur_epoch = _current_position_context(current_position)
        return Intent(
            kind=INTENT_NO_ACTION, account_id=ctx.account_id, symbol=ctx.symbol, strategy_id=ctx.strategy_id,
            setup_id=None, position_epoch=cur_epoch, config_version_id=ctx.config_version_id, config_hash=ctx.config_hash,
            decision_timestamp=as_of_ms, source_candle_close_timestamp=source_close_ts or as_of_ms,
            side=cur_side, idempotency_key=make_idempotency_key(
                account_id=ctx.account_id, symbol=ctx.symbol, strategy_id=ctx.strategy_id,
                key_id=cur_epoch or "no_position", decision_timestamp=as_of_ms, config_hash=ctx.config_hash,
            ), reason_code=reason, input_snapshot_hash=input_snapshot_hash,
        )

    # 반전/진입/청산이 이미 진행 중(pending)이면 새 결정을 내지 않는다 - 그
    # 상태가 해소될 때까지 대기(중복 제출/경쟁 방지).
    if reversal_machine.state == rsm.State.SAFE_HALT:
        return _no_action("safe_halt")
    if reversal_machine.state in (
        rsm.State.ENTRY_PENDING, rsm.State.EXIT_PENDING,
        rsm.State.REVERSAL_EXIT_PENDING, rsm.State.REVERSAL_WAIT_FLAT,
    ):
        return _no_action(f"awaiting_pending_transition:{reversal_machine.state.value}")

    try:
        snap = tfc.build_as_of_snapshot(
            action_tick_ms, bars_4h=bars_4h_confirmed_up_to_asof, bars_1h=bars_1h_confirmed_up_to_asof,
            bars_1d=bars_1d_confirmed_up_to_asof, bars_5m_for_10m=bars_5m_for_10m_up_to_asof,
        )
    except Exception as exc:
        # 데이터 누락/미확정/미래 timestamp - 신규진입은 차단하지만, 이미 열린
        # 포지션의 보호 청산 판단(stop 자체)은 여기서 막지 않는다(Part C2 요구).
        # 이 결정 엔진은 stop 발동 체결 자체를 다루지 않으므로(거래소/엔진의
        # 상시 보호주문 몫), 여기서는 신규 판단만 차단하고 사유를 명시한다.
        return _no_action(f"stale_or_missing_data:{type(exc).__name__}")

    if bars_4h_confirmed_up_to_asof:
        indicators_4h = indicator_fn(bars_4h_confirmed_up_to_asof, donchian_n=20)[-1]
    else:
        indicators_4h = compute_confirmed_4h_indicators([])
    direction_4h = direction_from_4h_indicators(
        ema_20=indicators_4h["ema_20"], ema_50=indicators_4h["ema_50"], close=indicators_4h["close"],
    )
    atr_4h = indicators_4h["atr_14"]

    def _entry_weakening_baseline(side: str) -> bool:
        ema_20_1h = indicator_fn(
            bars_1h_confirmed_up_to_asof, donchian_n=20,
        )[-1]["ema_20"]
        return weakening_from_1h(
            side=side, close_1h=snap.bar_1h["close"], ema_20_1h=ema_20_1h,
        )

    if reversal_machine.state == rsm.State.REVERSAL_ENTRY_PENDING:
        side = reversal_machine.pending_side
        if side is None:
            return _no_action(
                f"awaiting_pending_transition:{reversal_machine.state.value}",
                snap.bar_5m["close_time_ms"],
            )
        condition_true = bool(side) and tfc.donchian_setup_condition(snap, side)
        direction_ok = (
            (side == "long" and direction_4h == "LONG")
            or (side == "short" and direction_4h == "SHORT")
        )
        if (not condition_true or not direction_ok or atr_4h is None
                or policy is None or not _risk_requirement_satisfied(ctx)):
            return _no_action("reversal_target_revalidation_failed", snap.bar_5m["close_time_ms"])
        setup_id = st.make_setup_id(ctx.symbol, side, snap.bar_10m_current["open_time_ms"])
        return Intent(
            kind=INTENT_ENTRY, account_id=ctx.account_id, symbol=ctx.symbol,
            strategy_id=ctx.strategy_id, setup_id=setup_id, position_epoch=None,
            config_version_id=ctx.config_version_id, config_hash=ctx.config_hash,
            decision_timestamp=as_of_ms,
            source_candle_close_timestamp=snap.bar_5m["close_time_ms"], side=side,
            idempotency_key=make_idempotency_key(
                account_id=ctx.account_id, symbol=ctx.symbol, strategy_id=ctx.strategy_id,
                key_id=setup_id, decision_timestamp=as_of_ms, config_hash=ctx.config_hash,
            ), reason_code="reversal_entry_revalidated", input_snapshot_hash=input_snapshot_hash,
            raw_stop_price=cem.compute_initial_stop_price(
                side=side, entry_price=snap.bar_5m["close"], atr_4h=atr_4h,
                atr_multiplier=float(policy["initial_stop_atr_multiplier"]),
            ), requested_risk_pct=ctx.risk_per_trade_pct,
            strategy_policy=policy,
            entry_weakening_baseline=_entry_weakening_baseline(side),
        )

    if current_position is not None:
        side = current_position["side"]
        epoch = epoch_store.get(current_position["position_id"])

        ema_20_1h = indicator_fn(bars_1h_confirmed_up_to_asof, donchian_n=20)[-1]["ema_20"]
        weakening_now = weakening_from_1h(side=side, close_1h=snap.bar_1h["close"], ema_20_1h=ema_20_1h)

        invalidated = cem.is_4h_direction_invalidated(side=side, current_4h_direction=direction_4h)
        if epoch.pilot_entry and not epoch.pilot_4h_confirmed and direction_4h == "NONE":
            row1 = indicator_fn(bars_1h_confirmed_up_to_asof, donchian_n=20)[-1]
            direction1 = direction_from_4h_indicators(
                ema_20=row1.get("ema_20"), ema_50=row1.get("ema_50"), close=row1.get("close"))
            invalidated = direction1 != ("LONG" if side == "long" else "SHORT")
        derisk_decision = cem.evaluate_1h_structural_derisk(
            current_quantity=current_position["contracts"], lot_step=lot_step, min_size=min_size,
            weakening_prev=weakening_prev, weakening_now=weakening_now, epoch=epoch,
            derisk_pct=float(policy["derisk_pct"]),
        )
        price_r = cem.price_R(current_position["raw_entry_price"], current_position["initial_stop_price"])
        sign = 1 if side == "long" else -1
        prior_water = current_position.get("high_water")
        if prior_water is None:
            prior_water = current_position["raw_entry_price"]
        if side == "long":
            new_hw = max(float(prior_water), float(snap.bar_5m["high"]))
        else:
            new_hw = min(float(prior_water), float(snap.bar_5m["low"]))
        signed_profit_r = None
        mfe_r = None
        if price_r > 0:
            signed_move = (snap.bar_5m["close"] - current_position["raw_entry_price"]) * sign
            signed_profit_r = signed_move / price_r
            mfe_r = max(0.0, (new_hw - current_position["raw_entry_price"]) * sign / price_r)
        hard_loss_overlay = unified_trade_guard.hard_loss_eligible(
            max(0.0, -signed_profit_r) if signed_profit_r is not None else None,
            invalidated=weakening_now,
        )
        partial_tp = cem.DeriskDecision(action="none", reason="partial_take_profit_policy_disabled")
        if "partial_take_profit_activation_r" in policy:
            partial_tp = cem.evaluate_partial_take_profit(
                side=side, current_quantity=current_position["contracts"],
                original_quantity=epoch.original_contracts,
                lot_step=lot_step, min_size=min_size,
                raw_entry_price=current_position["raw_entry_price"], price_r=price_r,
                confirmed_5m_close=snap.bar_5m["close"], epoch=epoch,
                activation_r=float(policy["partial_take_profit_activation_r"]),
                take_profit_pct=float(policy["partial_take_profit_pct"]),
            )
        profit_lock_stop = None
        if (not epoch.profit_lock_active and profit_lock_cost_metadata_ready(current_position)
                and unified_trade_guard.profit_floor_eligible(
                    mfe_r, current_r=signed_profit_r,
                    risk_reduced=bool(epoch.original_contracts and epoch.original_contracts > 0
                                      and float(current_position["contracts"])
                                      <= float(epoch.original_contracts) * 0.75 + 1e-9))):
            profit_lock_stop = cem.compute_profit_lock_stop_price(
                side=side, effective_entry_price=current_position["effective_entry_price"],
                entry_fee_usdt=current_position["entry_fee_usdt"], contracts=current_position["contracts"],
                contract_size=current_position["contract_size"], fee_rate=current_position["fee_rate"],
                spread_bps=current_position["spread_bps"], slippage_bps=current_position["slippage_bps"],
            )
        activate_profit_lock = bool(
            profit_lock_stop is not None
            and sign * (float(snap.bar_5m["close"]) - float(profit_lock_stop)) > 0
        )
        structure_reduce = cem.DeriskDecision(action="none", reason="structure_profit_not_triggered")
        bars_5m_confirmed = [
            b for b in bars_5m_for_10m_up_to_asof
            if b.get("confirm") == 1 and b.get("close_time_ms", 0) <= as_of_ms
        ]
        current_5m_structure = _candidate_structure_snapshot(bars_5m_confirmed)
        previous_5m_structure = _candidate_structure_snapshot(bars_5m_confirmed[:-1])
        one_h_structure = _candidate_structure_snapshot(bars_1h_confirmed_up_to_asof)
        structure_guard = unified_trade_guard.profit_structure_protection(
            side=side, current_r=signed_profit_r, current_5m=current_5m_structure,
            previous_5m=previous_5m_structure, one_h=one_h_structure,
            already_reduced=bool(epoch.derisk_done),
        )
        if structure_guard.get("action") == "reduce_25" and epoch.original_contracts and epoch.original_contracts > 0:
            original = float(epoch.original_contracts)
            current = float(current_position["contracts"])
            already_ratio = max(0.0, min(1.0, (original-current)/original))
            if already_ratio < 0.50 - 1e-9:
                target_ratio = 0.25 if already_ratio < 0.25 - 1e-9 else 0.50
                target_residual = cem.floor_to_lot_step(original * (1.0-target_ratio), lot_step)
                reduce_quantity = current - target_residual
                if target_residual >= min_size and reduce_quantity >= min_size and reduce_quantity > 1e-12:
                    structure_reduce = cem.DeriskDecision(
                        action="partial_reduce", target_residual=target_residual,
                        reduce_quantity=reduce_quantity, reason="unified_structure_profit_break",
                    )

        mfe_reduce = cem.DeriskDecision(action="none", reason="mfe_profit_not_triggered")
        if (
            not epoch.mfe_profit_reduce_done
            and unified_trade_guard.mfe_giveback_eligible(mfe_r, signed_profit_r)
            and epoch.original_contracts
            and epoch.original_contracts > 0
        ):
            original = float(epoch.original_contracts)
            current = float(current_position["contracts"])
            already_ratio = max(0.0, min(1.0, (original - current) / original))
            if already_ratio < 0.50 - 1e-9:
                target_ratio = 0.25 if already_ratio < 0.25 - 1e-9 else 0.50
                target_residual = cem.floor_to_lot_step(original * (1.0 - target_ratio), lot_step)
                reduce_quantity = current - target_residual
                if target_residual >= min_size and reduce_quantity >= min_size and reduce_quantity > 1e-12:
                    mfe_reduce = cem.DeriskDecision(
                        action="partial_reduce", target_residual=target_residual,
                        reduce_quantity=reduce_quantity, reason="unified_mfe_profit_giveback",
                    )

        reduction_plans = {}
        if ctx.risk_adaptive_partials:
            adverse_key = 'swing_low_broken' if side == 'long' else 'swing_high_broken'
            risk_args = dict(current_r=signed_profit_r, mfe_r=mfe_r,
                atr_r=atr_4h/price_r if atr_4h and price_r > 0 else None,
                weakening_1h=weakening_now or bool(one_h_structure.get(adverse_key)),
                adverse_5m=bool(current_5m_structure.get(adverse_key)))
            def resize(decision, event, triggered, cap=None):
                # An ON-mode trigger must not inherit fixed25/50 lot or dust gates.
                # Only confirmed signal/lifecycle eligibility is reused.
                decision = cem.DeriskDecision(action='partial_reduce' if triggered else 'none', reason=event)
                plan = adaptive_reduction.chart_fraction(event, **risk_args)
                resized = cem.risk_adaptive_quantity(decision, plan=plan,
                    current_quantity=float(current_position['contracts']),
                    original_quantity=epoch.original_contracts, lot_step=lot_step,
                    min_size=min_size, cumulative_cap=cap)
                if resized.action != 'none' and plan is not None:
                    plan = dict(plan, requested_fraction=plan['fraction'],
                        fraction=resized.reduce_quantity/(float(current_position['contracts'])
                            if plan['basis']=='remaining' else float(epoch.original_contracts)))
                reduction_plans[event] = plan
                return resized
            derisk_decision = resize(derisk_decision, 'structural_derisk',
                not epoch.derisk_done and weakening_now and not weakening_prev)
            partial_tp = resize(partial_tp, 'partial_take_profit_2r',
                not epoch.partial_take_profit_done and epoch.original_contracts is not None
                and epoch.original_contracts > 0 and signed_profit_r is not None
                and 'partial_take_profit_activation_r' in policy
                and signed_profit_r >= float(policy['partial_take_profit_activation_r']))
            structure_reduce = resize(structure_reduce, 'unified_structure_profit_break',
                structure_guard.get('action') == 'reduce_25', .5)
            mfe_reduce = resize(mfe_reduce, 'unified_mfe_profit_giveback',
                not epoch.mfe_profit_reduce_done and unified_trade_guard.mfe_giveback_eligible(mfe_r, signed_profit_r), .5)

        action = cem.decide_action_priority({
            "4h_full_exit": invalidated,
            "structure_profit_break": structure_reduce.action != "none",
            "1h_structural_derisk": derisk_decision.action != "none",
            "mfe_profit_giveback": mfe_reduce.action != "none",
            "partial_take_profit_2r": partial_tp.action != "none",
            "profit_lock_tightening": activate_profit_lock,
        })

        cur_epoch_id = current_position["position_id"]
        idem = make_idempotency_key(
            account_id=ctx.account_id, symbol=ctx.symbol, strategy_id=ctx.strategy_id,
            key_id=cur_epoch_id, decision_timestamp=as_of_ms, config_hash=ctx.config_hash,
        )
        common = dict(
            account_id=ctx.account_id, symbol=ctx.symbol, strategy_id=ctx.strategy_id, setup_id=None,
            position_epoch=cur_epoch_id, config_version_id=ctx.config_version_id, config_hash=ctx.config_hash,
            decision_timestamp=as_of_ms, source_candle_close_timestamp=snap.bar_5m["close_time_ms"],
            side=side, idempotency_key=idem, input_snapshot_hash=input_snapshot_hash,
            strategy_policy=policy,
        )

        if action == "4h_full_exit":
            opposite = "short" if side == "long" else "long"
            return Intent(kind=INTENT_REVERSAL, reason_code="4h_direction_invalidated", target_side=opposite, **common)
        if hard_loss_overlay:
            return Intent(kind=INTENT_EXIT, reason_code="unified_hard_loss_075r", **common)
        if action == "structure_profit_break":
            return Intent(
                kind=INTENT_REDUCE, reason_code=structure_reduce.reason,
                reduction_plan=reduction_plans.get('unified_structure_profit_break'),
                reduce_quantity=structure_reduce.reduce_quantity, target_residual=structure_reduce.target_residual,
                **common,
            )
        if action == "1h_structural_derisk":
            return Intent(
                kind=INTENT_REDUCE, reason_code=derisk_decision.reason,
                reduction_plan=reduction_plans.get('structural_derisk'),
                reduce_quantity=derisk_decision.reduce_quantity, target_residual=derisk_decision.target_residual,
                **common,
            )
        if action == "mfe_profit_giveback":
            return Intent(
                kind=INTENT_REDUCE, reason_code=mfe_reduce.reason,
                reduction_plan=reduction_plans.get('unified_mfe_profit_giveback'),
                reduce_quantity=mfe_reduce.reduce_quantity,
                target_residual=mfe_reduce.target_residual, **common,
            )
        if action == "partial_take_profit_2r":
            return Intent(
                kind=INTENT_REDUCE, reason_code=partial_tp.reason,
                reduction_plan=reduction_plans.get('partial_take_profit_2r'),
                reduce_quantity=partial_tp.reduce_quantity,
                target_residual=partial_tp.target_residual, **common,
            )
        if action == "profit_lock_tightening":
            return Intent(kind=INTENT_STOP_UPDATE, reason_code="profit_lock_activated", raw_stop_price=profit_lock_stop, **common)

        # trailing 갱신(항상 평가 - Phase 3.4 계약: 생존 시 high/low water 갱신)
        new_stop = cem.compute_trailing_stop_price(
            side=side, high_or_low_water=new_hw, atr_4h=atr_4h,
            atr_multiplier=float(policy["trailing_atr_multiplier"]),
        ) if atr_4h else None
        if new_stop is not None:
            return Intent(kind=INTENT_STOP_UPDATE, reason_code="trailing_update", raw_stop_price=new_stop, **common)
        return _no_action("holding_no_change", snap.bar_5m["close_time_ms"])

    # --- 포지션 없음: setup 평가 ---
    direction_1h, direction_5m = lower_tf_entry_directions(
        bars_1h=bars_1h_confirmed_up_to_asof, bars_5m=bars_5m_for_10m_up_to_asof,
        as_of_ms=as_of_ms, indicator_fn=indicator_fn)
    preferred = direction_4h
    if preferred == "NONE" and direction_1h == direction_5m:
        preferred = direction_1h
    sides = ("short", "long") if preferred == "SHORT" else ("long", "short")
    for side in sides:
        condition_true = tfc.donchian_setup_condition(snap, side)
        current_bar_open_ms = snap.bar_10m_current["open_time_ms"]
        would_be_id = st.make_setup_id(ctx.symbol, side, current_bar_open_ms)
        transitioned = setup_tracker.current_state(ctx.symbol, side) != condition_true
        if not condition_true:
            continue
        retry_setup_id = setup_tracker.pending_timeout_retry_for_bar(
            ctx.symbol, side, current_bar_open_ms,
        )
        # 같은 확정 10분봉은 setup_id로 정확히 한 번만 처리한다. 반대로 조건이
        # 계속 True여도 다음 확정 10분봉은 새로운 setup_id이므로 다시 평가한다.
        # timeout 1회 재검토는 기존 setup_id를 우선 사용해 원래 계약을 보존한다.
        if retry_setup_id is None and setup_tracker.setup_metadata(would_be_id) is not None:
            continue
        effective_setup_id = retry_setup_id or would_be_id
        reason = (
            "timeout_retry_edge_triggered" if retry_setup_id
            else "setup_edge_triggered" if transitioned
            else "setup_bar_recheck"
        )
        direction_ok = (side == "long" and direction_4h == "LONG") or (side == "short" and direction_4h == "SHORT")
        pilot_fraction = unified_trade_guard.entry_size_fraction(
            side=side, primary_direction=direction_4h, direction_1h=direction_1h, direction_5m=direction_5m,
        )
        pilot_entry = pilot_fraction == unified_trade_guard.PILOT_ENTRY_FRACTION
        idem = make_idempotency_key(
            account_id=ctx.account_id, symbol=ctx.symbol, strategy_id=ctx.strategy_id,
            key_id=effective_setup_id, decision_timestamp=as_of_ms, config_hash=ctx.config_hash,
        )
        common = dict(
            account_id=ctx.account_id, symbol=ctx.symbol, strategy_id=ctx.strategy_id,
            setup_id=effective_setup_id,
            position_epoch=None, config_version_id=ctx.config_version_id, config_hash=ctx.config_hash,
            decision_timestamp=as_of_ms, source_candle_close_timestamp=snap.bar_5m["close_time_ms"],
            side=side, idempotency_key=idem, input_snapshot_hash=input_snapshot_hash,
        )

        common.update(entry_attempt=True,entry_validation_values={'entry_price':snap.bar_5m['close'],'direction_4h':direction_4h,'direction_1h':direction_1h,'direction_5m':direction_5m})
        # A continuous Donchian condition is an opportunity window, not an
        # evergreen permission to enter.  Keep rechecking through 30 minutes
        # (0/10/20/30), then require the setup to turn False and form again.
        if (not direction_ok and not pilot_entry) or atr_4h is None:
            return Intent(kind=INTENT_NO_ACTION, reason_code="setup_direction_mismatch_or_no_atr", **common)
        active_since = (setup_tracker.continuous_entry_eligible_started_ms(ctx.symbol, side)
                        if setup_tracker.entry_eligible_state(ctx.symbol, side) else None)
        if active_since is None:
            active_since = current_bar_open_ms
        setup_age_ms = max(0, current_bar_open_ms - int(active_since))
        if setup_age_ms > SETUP_RECHECK_MAX_AGE_MS:
            return Intent(kind=INTENT_NO_ACTION, reason_code="setup_stale_after_30m", **common)

        common.update(entry_attempt=True,entry_validation_values=dict(
            entry_price=snap.bar_5m['close'],setup_age_ms=setup_age_ms,max_setup_age_ms=SETUP_RECHECK_MAX_AGE_MS,
            direction_4h=direction_4h,direction_1h=direction_1h,direction_5m=direction_5m))
        if policy is None:
            return Intent(kind=INTENT_NO_ACTION, reason_code="strategy_policy_missing_or_invalid", **common)
        if not _risk_requirement_satisfied(ctx):
            # 2026-09-03 STOP-SHIP 수정(Phase 3.10.2 Item 5) - risk_per_trade_pct를
            # 알 수 없으면(활성 config 없음/필드 누락) 절대 임의 값으로 진입
            # 사이징을 하지 않는다 - 신규진입 자체를 막는다(기존 포지션
            # 관리는 이 분기와 무관하므로 계속 정상 작동한다).
            return Intent(kind=INTENT_NO_ACTION, reason_code="no_risk_per_trade_pct_configured", **common)

        hour_kst = datetime.datetime.fromtimestamp(
            as_of_ms / 1000.0, tz=datetime.timezone.utc,
        ).astimezone(datetime.timezone(datetime.timedelta(hours=9))).hour
        risk_score = unified_trade_guard.entry_risk_score(
            symbol=ctx.symbol, side=side, hour_kst=hour_kst,
            tf_mixed=_candidate_tf_mixed(direction_4h, direction_1h, direction_5m),
        )
        risk_policy = unified_trade_guard.entry_risk_policy(risk_score["score"])
        common['entry_validation_values'].update(risk_score=risk_score,risk_policy=risk_policy)
        if risk_policy["blocked"]:
            return Intent(kind=INTENT_NO_ACTION, reason_code="entry_risk_score_blocked", **common)
        wanted = "LONG" if side == "long" else "SHORT"
        strong_confirmation = direction_4h == wanted and direction_1h == wanted and direction_5m == wanted
        if risk_policy["require_strong_confirmation"] and not strong_confirmation:
            return Intent(kind=INTENT_NO_ACTION, reason_code="entry_risk_strong_confirmation_required", **common)

        overextension = _candidate_entry_overextension_gate(
            side=side, entry_price=snap.bar_5m["close"],
            bars_1h=bars_1h_confirmed_up_to_asof, bars_5m=bars_5m_for_10m_up_to_asof,
            atr14_4h=atr_4h, as_of_ms=as_of_ms, indicator_fn=indicator_fn,
        )
        common['entry_validation_values']['freshness']=overextension
        if not overextension["allowed"]:
            return Intent(
                kind=INTENT_NO_ACTION, reason_code=overextension["reason"], **common,
            )

        # 재검토는 이전의 늦은 응답을 재사용하지 않는다 - 이 바로 이 확정봉의
        # 새로운 가격/ATR로 stop을 다시 계산한다(사용자 지시 - "새로운 시장
        # 입력으로 새 판단").
        initial_stop = cem.compute_initial_stop_price(
            side=side, entry_price=snap.bar_5m["close"], atr_4h=atr_4h,
            atr_multiplier=float(policy["initial_stop_atr_multiplier"]),
        )
        return Intent(
            kind=INTENT_ENTRY,
            reason_code=(reason if retry_setup_id else "setup_pilot_4h_none_lower_tf_aligned" if pilot_entry else reason),
            raw_stop_price=initial_stop, requested_risk_pct=ctx.risk_per_trade_pct,
            strategy_policy=policy, entry_weakening_baseline=_entry_weakening_baseline(side),
            pilot_entry=pilot_entry,
            # Raw Donchian transition can precede direction eligibility. The
            # existing eligible clock, not the reason label, defines freshness.
            adaptive_wide_stop_policy=("risk_cap" if setup_age_ms == 0 and not retry_setup_id else "block"),
            entry_size_fraction=(pilot_fraction if pilot_entry else 1.0) * float(risk_policy["size_fraction"]), **common,
        )

    return _no_action("no_setup", snap.bar_5m["close_time_ms"])



def _apply_live_bounded_entry_geometry(
    ctx: DecisionContext, legacy_intent: Intent, *, entry_price: float, atr: float,
    structural_support: float | None, structural_resistance: float | None,
) -> Intent:
    if legacy_intent.kind != INTENT_ENTRY or str(ctx.adaptive_exit_mode or "OFF").upper() != "LIVE_BOUNDED":
        return legacy_intent
    if ctx.adaptive_exit_policy is None:
        return legacy_intent
    try:
        policy = validate_adaptive_exit_policy(ctx.adaptive_exit_policy)
        current_hash = adaptive_policy_sha256(policy)
    except Exception:
        return legacy_intent
    if not ctx.adaptive_approved_policy_hash or ctx.adaptive_approved_policy_hash != current_hash:
        return legacy_intent
    a_ctx = AdaptiveExitContext(
        symbol=ctx.symbol, side=legacy_intent.side or "", entry_price=float(entry_price),
        current_quantity=1.0, current_stop=None, equity_usdt=1.0,
        trade_risk_budget_usdt=1.0, atr=float(atr), sizing_mode=ctx.sizing_mode,
        structural_support=structural_support, structural_resistance=structural_resistance,
        leverage=float(ctx.adaptive_leverage or 1.0),
        mode="LIVE_BOUNDED", decision_timestamp=legacy_intent.decision_timestamp,
        input_snapshot_hash=legacy_intent.input_snapshot_hash,
        allow_wide_stop_with_risk_sizing=(
            str(getattr(legacy_intent, "adaptive_wide_stop_policy", "legacy")) == "risk_cap"
        ),
    )
    try:
        geometry = compute_structural_stop(a_ctx, policy)
    except ValueError as exc:
        if (str(exc) == "stop_distance_above_leverage_cap"
                and getattr(legacy_intent, "adaptive_wide_stop_policy", "legacy") == "legacy"):
            leverage = float(ctx.adaptive_leverage or 1.0)
            if leverage <= 0 or legacy_intent.side not in ("long", "short"):
                return replace(
                    legacy_intent, kind=INTENT_NO_ACTION, raw_stop_price=None, raw_target_price=None,
                    reason_code=f"adaptive_entry_blocked:{exc}",
                    adaptive_mode="LIVE_BOUNDED", adaptive_policy_hash=current_hash,
                )
            max_stop_fraction = float(policy["max_leveraged_stop_loss_pct"]) / 100.0 / leverage
            bounded_stop = float(entry_price) * (1.0 - max_stop_fraction if legacy_intent.side == "long" else 1.0 + max_stop_fraction)
            risk_distance = abs(float(entry_price) - bounded_stop)
            tp2_cap = float(entry_price) * float(policy["max_leveraged_tp2_gain_pct"]) / 100.0 / leverage
            target_distance = min(float(policy["tp2_r_prior"]) * risk_distance, tp2_cap)
            sign = 1.0 if legacy_intent.side == "long" else -1.0
            return replace(
                legacy_intent, raw_stop_price=bounded_stop,
                raw_target_price=float(entry_price) + sign * target_distance,
                adaptive_mode="LIVE_BOUNDED", adaptive_policy_hash=current_hash,
            )
        return replace(
            legacy_intent, kind=INTENT_NO_ACTION, raw_stop_price=None, raw_target_price=None,
            reason_code=f"adaptive_entry_blocked:{exc}",
            adaptive_mode="LIVE_BOUNDED", adaptive_policy_hash=current_hash,
        )
    if not geometry.valid or geometry.stop_price is None:
        return replace(
            legacy_intent, kind=INTENT_NO_ACTION, raw_stop_price=None, raw_target_price=None,
            reason_code=f"adaptive_entry_blocked:{geometry.reason_code}",
            adaptive_mode="LIVE_BOUNDED", adaptive_policy_hash=current_hash,
        )
    risk_distance = abs(float(entry_price) - float(geometry.stop_price))
    sign = 1.0 if legacy_intent.side == "long" else -1.0
    tp2_cap = float(entry_price) * float(policy["max_leveraged_tp2_gain_pct"]) / 100.0 / a_ctx.leverage
    target_distance = min(float(policy["tp2_r_prior"]) * risk_distance, tp2_cap)
    cost_distance = float(entry_price) * CANDIDATE_C_ROUNDTRIP_COST_RATE
    post_cost_rr = (target_distance - cost_distance) / (risk_distance + cost_distance)
    validation_values=dict(legacy_intent.entry_validation_values or {},entry_price=entry_price,stop=geometry.stop_price,target=float(entry_price)+sign*target_distance,post_cost_rr=post_cost_rr,min_post_cost_rr=policy['min_post_cost_rr'])
    if post_cost_rr < float(policy["min_post_cost_rr"]):
        return replace(
            legacy_intent, kind=INTENT_NO_ACTION, raw_stop_price=None, raw_target_price=None,
            reason_code="adaptive_entry_blocked:post_cost_rr_below_minimum",entry_validation_values=validation_values,
            adaptive_mode="LIVE_BOUNDED", adaptive_policy_hash=current_hash,
        )
    adaptive_target = float(entry_price) + sign * target_distance
    return replace(
        legacy_intent, raw_stop_price=geometry.stop_price, raw_target_price=adaptive_target,
        adaptive_mode="LIVE_BOUNDED", adaptive_policy_hash=current_hash,
        adaptive_wide_stop_risk_sizing=(
            getattr(legacy_intent, "adaptive_wide_stop_policy", "legacy") == "risk_cap"
            and geometry.distance_fraction > float(policy["max_leveraged_stop_loss_pct"]) / 100.0 / a_ctx.leverage + 1e-12
        ),
    )


def _apply_live_bounded_held_stop(ctx, legacy_intent, current_position, *, atr: float):
    if str(ctx.adaptive_exit_mode or "OFF").upper() != "LIVE_BOUNDED":
        return legacy_intent
    if current_position is None or legacy_intent.kind not in (INTENT_NO_ACTION, INTENT_STOP_UPDATE):
        return legacy_intent
    if ctx.adaptive_exit_policy is None:
        return legacy_intent
    policy = validate_adaptive_exit_policy(ctx.adaptive_exit_policy)
    current_hash = adaptive_policy_sha256(policy)
    if not ctx.adaptive_approved_policy_hash or ctx.adaptive_approved_policy_hash != current_hash:
        return legacy_intent
    side = str(current_position.get("side") or legacy_intent.side or "")
    water = current_position.get("high_water") or current_position.get("mark_price") or current_position.get("raw_entry_price")
    current_stop = current_position.get("current_stop_price")
    if side not in ("long", "short") or water is None or current_stop is None or atr is None or float(atr) <= 0:
        return legacy_intent
    proposed = float(water) - float(policy["trailing_atr_prior"]) * float(atr) if side == "long" else float(water) + float(policy["trailing_atr_prior"]) * float(atr)
    if legacy_intent.kind == INTENT_STOP_UPDATE and legacy_intent.raw_stop_price is not None:
        old = float(legacy_intent.raw_stop_price)
        if (side == "long" and old >= proposed) or (side == "short" and old <= proposed):
            return legacy_intent
    decision = apply_monotonic_stop(side, float(current_stop), proposed)
    if decision.rejected_loosen or decision.effective_stop == float(current_stop):
        return legacy_intent
    return replace(legacy_intent, kind=INTENT_STOP_UPDATE, raw_stop_price=decision.effective_stop,
                   reason_code="adaptive_trailing_update", adaptive_mode="LIVE_BOUNDED",
                   adaptive_policy_hash=current_hash)

def _adaptive_shadow_plan(
    ctx: DecisionContext, legacy_intent: Intent, *, as_of_ms: int,
    bars_4h_confirmed_up_to_asof: list[dict], bars_1h_confirmed_up_to_asof: list[dict],
    bars_5m_for_10m_up_to_asof: list[dict], current_position: dict | None,
    indicator_fn=None,
):
    if str(ctx.adaptive_exit_mode or "OFF").upper() not in ("SHADOW", "ADVISORY"):
        return None
    if ctx.adaptive_exit_policy is None or ctx.adaptive_equity_usdt is None:
        return None
    try:
        policy = validate_adaptive_exit_policy(ctx.adaptive_exit_policy)
    except Exception:
        return None
    indicator_fn = indicator_fn or si.augment_with_indicators
    bars4 = [b for b in bars_4h_confirmed_up_to_asof if b.get("confirm") == 1 and b.get("close_time_ms", 0) <= legacy_intent.decision_timestamp]
    bars1 = [b for b in bars_1h_confirmed_up_to_asof if b.get("confirm") == 1 and b.get("close_time_ms", 0) <= legacy_intent.decision_timestamp]
    bars5 = [b for b in bars_5m_for_10m_up_to_asof if b.get("confirm") == 1 and b.get("close_time_ms", 0) <= legacy_intent.decision_timestamp]
    try:
        indicators4 = indicator_fn(bars4, donchian_n=20)[-1]
        atr = float(indicators4.get("atr_14"))
    except Exception:
        return None
    side = legacy_intent.side or (current_position or {}).get("side")
    if side not in ("long", "short"):
        return None
    entry_price = (current_position or {}).get("raw_entry_price") or (current_position or {}).get("entry_price")
    if not entry_price:
        source = bars5[-1] if bars5 else (bars1[-1] if bars1 else (bars4[-1] if bars4 else None))
        entry_price = (source or {}).get("close")
    if not entry_price:
        return None
    lows = [float(b["low"]) for b in (bars1[-8:] + bars4[-4:]) if b.get("low") is not None]
    highs = [float(b["high"]) for b in (bars1[-8:] + bars4[-4:]) if b.get("high") is not None]
    support = min(lows) if lows else None
    resistance = max(highs) if highs else None
    resolution_high = max(highs) if highs else None
    resolution_low = min(lows) if lows else None
    equity = float(ctx.adaptive_equity_usdt)
    risk_pct = float(ctx.risk_per_trade_pct or 0.0)
    risk_budget = equity * risk_pct / 100.0 if risk_pct > 0 else 0.0
    a_ctx = AdaptiveExitContext(
        symbol=ctx.symbol, side=side, entry_price=float(entry_price),
        current_quantity=float((current_position or {}).get("contracts") or 1.0),
        current_stop=(current_position or {}).get("current_stop_price"),
        equity_usdt=equity, trade_risk_budget_usdt=risk_budget, atr=atr,
        sizing_mode=ctx.sizing_mode, configured_margin_usdt=ctx.adaptive_fixed_margin_usdt,
        leverage=float(ctx.adaptive_leverage or 1.0), order_cap_notional=ctx.adaptive_order_cap_notional,
        structural_support=support if side == "long" else None,
        structural_resistance=resistance if side == "short" else None,
        near_resistance=resolution_high if side == "long" else None,
        near_support=resolution_low if side == "short" else None,
        continuation_resistance=resolution_high if side == "long" else None,
        continuation_support=resolution_low if side == "short" else None,
        mode=str(ctx.adaptive_exit_mode).upper(), decision_timestamp=legacy_intent.decision_timestamp,
        input_snapshot_hash=legacy_intent.input_snapshot_hash,
        source_candle_timestamps=tuple(int(b.get("close_time_ms")) for b in (bars1[-2:] + bars4[-2:]) if b.get("close_time_ms") is not None),
    )
    return AdaptiveExitEngine(policy).plan(a_ctx)


SETUP_RECHECK_MAX_AGE_MS = 30 * 60 * 1000


def decide(
    ctx: DecisionContext,
    *,
    as_of_ms: int,
    bars_4h_confirmed_up_to_asof: list[dict],
    bars_1h_confirmed_up_to_asof: list[dict],
    bars_1d_confirmed_up_to_asof: list[dict],
    bars_5m_for_10m_up_to_asof: list[dict],
    setup_tracker: st.SetupTracker,
    epoch_store: cem.PositionEpochStore,
    reversal_machine: rsm.SymbolReversalMachine,
    current_position: dict | None,
    weakening_prev: bool,
    lot_step: float,
    min_size: float,
    indicator_fn=None,
) -> Intent:
    if ctx.risk_adaptive_partials:
        ctx = replace(ctx, config_hash=adaptive_reduction.management_config_hash(ctx.config_hash))
    legacy = _decide_legacy(
        ctx, as_of_ms=as_of_ms, bars_4h_confirmed_up_to_asof=bars_4h_confirmed_up_to_asof,
        bars_1h_confirmed_up_to_asof=bars_1h_confirmed_up_to_asof,
        bars_1d_confirmed_up_to_asof=bars_1d_confirmed_up_to_asof,
        bars_5m_for_10m_up_to_asof=bars_5m_for_10m_up_to_asof,
        setup_tracker=setup_tracker, epoch_store=epoch_store, reversal_machine=reversal_machine,
        current_position=current_position, weakening_prev=weakening_prev,
        lot_step=lot_step, min_size=min_size, indicator_fn=indicator_fn,
    )
    if ctx.risk_adaptive_partials:
        legacy = replace(legacy, reduction_policy_hash=adaptive_reduction.chart_policy_hash())
    try:
        if legacy.kind == INTENT_ENTRY and str(ctx.adaptive_exit_mode or "OFF").upper() == "LIVE_BOUNDED":
            indicator = indicator_fn or si.augment_with_indicators
            bars4 = [b for b in bars_4h_confirmed_up_to_asof if b.get("confirm") == 1 and b.get("close_time_ms", 0) <= legacy.decision_timestamp]
            bars1 = [b for b in bars_1h_confirmed_up_to_asof if b.get("confirm") == 1 and b.get("close_time_ms", 0) <= legacy.decision_timestamp]
            bars5 = [b for b in bars_5m_for_10m_up_to_asof if b.get("confirm") == 1 and b.get("close_time_ms", 0) <= legacy.decision_timestamp]
            indicators4 = indicator(bars4, donchian_n=20)[-1]
            atr = float(indicators4.get("atr_14"))
            source = bars5[-1] if bars5 else (bars1[-1] if bars1 else bars4[-1])
            entry_price = float(source["close"])
            lows = [float(b["low"]) for b in (bars1[-8:] + bars4[-4:]) if b.get("low") is not None]
            highs = [float(b["high"]) for b in (bars1[-8:] + bars4[-4:]) if b.get("high") is not None]
            legacy = _apply_live_bounded_entry_geometry(
                ctx, legacy, entry_price=entry_price, atr=atr,
                structural_support=min(lows) if lows else None,
                structural_resistance=max(highs) if highs else None,
            )
        elif current_position is not None and str(ctx.adaptive_exit_mode or "OFF").upper() == "LIVE_BOUNDED":
            indicator = indicator_fn or si.augment_with_indicators
            bars4 = [b for b in bars_4h_confirmed_up_to_asof if b.get("confirm") == 1 and b.get("close_time_ms", 0) <= legacy.decision_timestamp]
            if bars4:
                indicators4 = indicator(bars4, donchian_n=20)[-1]
                atr = float(indicators4.get("atr_14"))
                legacy = _apply_live_bounded_held_stop(ctx, legacy, current_position, atr=atr)
        plan = _adaptive_shadow_plan(
            ctx, legacy, as_of_ms=as_of_ms,
            bars_4h_confirmed_up_to_asof=bars_4h_confirmed_up_to_asof,
            bars_1h_confirmed_up_to_asof=bars_1h_confirmed_up_to_asof,
            bars_5m_for_10m_up_to_asof=bars_5m_for_10m_up_to_asof,
            current_position=current_position, indicator_fn=indicator_fn,
        )
        if plan is not None and ctx.adaptive_audit_user_dir:
            adaptive_exit_log.append_plan(ctx.adaptive_audit_user_dir, plan.audit_record())
    except Exception:
        # Shadow must never change or block the legacy Candidate C intent.
        pass
    return legacy
