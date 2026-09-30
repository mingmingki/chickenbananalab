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

import hashlib
import json
import math
from dataclasses import dataclass, field, replace

import candidate_c_exit_management as cem
import entry_overextension_guard
import candidate_c_reversal_state_machine as rsm
import candidate_c_setup_tracker as st
import candidate_c_timeframe_contract as tfc
import strategy_indicators as si
import candidate_c_strategy_policy as strategy_policy_contract
import adaptive_exit_log
from adaptive_exit_engine import AdaptiveExitContext, AdaptiveExitEngine, compute_structural_stop, apply_monotonic_stop
from adaptive_exit_policy import policy_sha256 as adaptive_policy_sha256, validate_adaptive_exit_policy

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


def _candidate_entry_overextension_gate(
    *, side: str, entry_price: float, bars_1h: list[dict], atr14_4h,
    as_of_ms: int, indicator_fn,
) -> dict:
    try:
        one_h = indicator_fn(bars_1h, donchian_n=20)
        ema20_1h = one_h[-1]["ema_20"] if one_h else None
        cutoff = as_of_ms - 24 * 60 * 60 * 1000
        refs = [b for b in bars_1h if b.get("confirm") == 1 and b["close_time_ms"] <= cutoff]
        reference_24h_price = refs[-1]["close"] if refs else None
    except Exception:
        ema20_1h = reference_24h_price = None
    return entry_overextension_guard.evaluate(
        side=side, entry_price=entry_price, ema20_1h=ema20_1h,
        atr14_4h=atr14_4h, reference_24h_price=reference_24h_price,
    )


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
        derisk_decision = cem.evaluate_1h_structural_derisk(
            current_quantity=current_position["contracts"], lot_step=lot_step, min_size=min_size,
            weakening_prev=weakening_prev, weakening_now=weakening_now, epoch=epoch,
            derisk_pct=float(policy["derisk_pct"]),
        )
        price_r = cem.price_R(current_position["raw_entry_price"], current_position["initial_stop_price"])
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
        activate_profit_lock = (
            (not epoch.profit_lock_active)
            and profit_lock_cost_metadata_ready(current_position)
            and cem.profit_lock_activation_check(
                side=side, raw_entry_price=current_position["raw_entry_price"], price_r=price_r,
                confirmed_5m_close=snap.bar_5m["close"],
                activation_r=float(policy["profit_lock_activation_r"]),
            )
        )

        action = cem.decide_action_priority({
            "4h_full_exit": invalidated,
            "1h_structural_derisk": derisk_decision.action != "none",
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
        if action == "1h_structural_derisk":
            return Intent(
                kind=INTENT_REDUCE, reason_code=derisk_decision.reason,
                reduce_quantity=derisk_decision.reduce_quantity, target_residual=derisk_decision.target_residual,
                **common,
            )
        if action == "partial_take_profit_2r":
            return Intent(
                kind=INTENT_REDUCE, reason_code=partial_tp.reason,
                reduce_quantity=partial_tp.reduce_quantity,
                target_residual=partial_tp.target_residual, **common,
            )
        if action == "profit_lock_tightening":
            stop = cem.compute_profit_lock_stop_price(
                side=side, effective_entry_price=current_position["effective_entry_price"],
                entry_fee_usdt=current_position["entry_fee_usdt"], contracts=current_position["contracts"],
                contract_size=current_position["contract_size"], fee_rate=current_position["fee_rate"],
                spread_bps=current_position["spread_bps"], slippage_bps=current_position["slippage_bps"],
            )
            return Intent(kind=INTENT_STOP_UPDATE, reason_code="profit_lock_activated", raw_stop_price=stop, **common)

        # trailing 갱신(항상 평가 - Phase 3.4 계약: 생존 시 high/low water 갱신)
        if side == "long":
            new_hw = max(current_position["high_water"], snap.bar_5m["high"])
        else:
            new_hw = min(current_position["high_water"], snap.bar_5m["low"])
        new_stop = cem.compute_trailing_stop_price(
            side=side, high_or_low_water=new_hw, atr_4h=atr_4h,
            atr_multiplier=float(policy["trailing_atr_multiplier"]),
        ) if atr_4h else None
        if new_stop is not None:
            return Intent(kind=INTENT_STOP_UPDATE, reason_code="trailing_update", raw_stop_price=new_stop, **common)
        return _no_action("holding_no_change", snap.bar_5m["close_time_ms"])

    # --- 포지션 없음: setup 평가 ---
    for side in ("long", "short"):
        condition_true = tfc.donchian_setup_condition(snap, side)
        current_bar_open_ms = snap.bar_10m_current["open_time_ms"]
        would_be_id = st.make_setup_id(ctx.symbol, side, current_bar_open_ms)
        transitioned = setup_tracker.current_state(ctx.symbol, side) != condition_true
        retry_setup_id = None
        if not transitioned:
            if not condition_true:
                continue  # 조건 자체가 꺼져 있고 전이도 없음 - 재검토 대상도 아님
            # [2026-09-16, 사용자 직접 지시 - timeout 1회 재검토 정책, 기본 OFF]
            # 전이는 없지만(조건이 계속 True) *바로 이 확정 10분봉*이 이전에
            # timeout으로 arm된 재검토 대상 봉과 정확히 일치하면, 원 setup_id로
            # 딱 한 번 다시 판단한다. wait/reject는 여기 오지 않는다(오케스트
            # 레이터가 그 경우엔 애초에 arm하지 않음 - 이 함수는 그저 확인만
            # 한다). 이 대체 경로가 없으면(정책 OFF 포함, arm된 적 없음 포함)
            # 기존 동작과 100% 동일하게 continue한다.
            retry_setup_id = setup_tracker.pending_timeout_retry_for_bar(
                ctx.symbol, side, current_bar_open_ms,
            )
            if retry_setup_id is None:
                continue
        elif not condition_true:
            continue  # True->False 전이는 setup을 만들지 않음(기존 동작 그대로)
        effective_setup_id = retry_setup_id or would_be_id
        reason = "timeout_retry_edge_triggered" if retry_setup_id else "setup_edge_triggered"
        direction_ok = (side == "long" and direction_4h == "LONG") or (side == "short" and direction_4h == "SHORT")
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
        if not direction_ok or atr_4h is None:
            return Intent(kind=INTENT_NO_ACTION, reason_code="setup_direction_mismatch_or_no_atr", **common)
        if policy is None:
            return Intent(kind=INTENT_NO_ACTION, reason_code="strategy_policy_missing_or_invalid", **common)
        if not _risk_requirement_satisfied(ctx):
            # 2026-09-03 STOP-SHIP 수정(Phase 3.10.2 Item 5) - risk_per_trade_pct를
            # 알 수 없으면(활성 config 없음/필드 누락) 절대 임의 값으로 진입
            # 사이징을 하지 않는다 - 신규진입 자체를 막는다(기존 포지션
            # 관리는 이 분기와 무관하므로 계속 정상 작동한다).
            return Intent(kind=INTENT_NO_ACTION, reason_code="no_risk_per_trade_pct_configured", **common)

        overextension = _candidate_entry_overextension_gate(
            side=side, entry_price=snap.bar_5m["close"],
            bars_1h=bars_1h_confirmed_up_to_asof, atr14_4h=atr_4h,
            as_of_ms=as_of_ms, indicator_fn=indicator_fn,
        )
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
            kind=INTENT_ENTRY, reason_code=reason, raw_stop_price=initial_stop,
            requested_risk_pct=ctx.risk_per_trade_pct, strategy_policy=policy,
            entry_weakening_baseline=_entry_weakening_baseline(side), **common,
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
        mode="LIVE_BOUNDED", decision_timestamp=legacy_intent.decision_timestamp,
        input_snapshot_hash=legacy_intent.input_snapshot_hash,
    )
    geometry = compute_structural_stop(a_ctx, policy)
    if not geometry.valid or geometry.stop_price is None:
        return replace(
            legacy_intent, kind=INTENT_NO_ACTION, raw_stop_price=None, raw_target_price=None,
            reason_code=f"adaptive_entry_blocked:{geometry.reason_code}",
            adaptive_mode="LIVE_BOUNDED", adaptive_policy_hash=current_hash,
        )
    risk_distance = abs(float(entry_price) - float(geometry.stop_price))
    sign = 1.0 if legacy_intent.side == "long" else -1.0
    adaptive_target = float(entry_price) + sign * float(policy["tp2_r_prior"]) * risk_distance
    return replace(
        legacy_intent, raw_stop_price=geometry.stop_price, raw_target_price=adaptive_target,
        adaptive_mode="LIVE_BOUNDED", adaptive_policy_hash=current_hash,
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
    legacy = _decide_legacy(
        ctx, as_of_ms=as_of_ms, bars_4h_confirmed_up_to_asof=bars_4h_confirmed_up_to_asof,
        bars_1h_confirmed_up_to_asof=bars_1h_confirmed_up_to_asof,
        bars_1d_confirmed_up_to_asof=bars_1d_confirmed_up_to_asof,
        bars_5m_for_10m_up_to_asof=bars_5m_for_10m_up_to_asof,
        setup_tracker=setup_tracker, epoch_store=epoch_store, reversal_machine=reversal_machine,
        current_position=current_position, weakening_prev=weakening_prev,
        lot_step=lot_step, min_size=min_size, indicator_fn=indicator_fn,
    )
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
