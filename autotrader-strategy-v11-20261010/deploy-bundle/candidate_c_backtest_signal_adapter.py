"""Phase 3.6 Part C3 - backtest_engine.py/portfolio_mtm_engine.py 쪽 Candidate C
배선. 두 엔진 모두 동일한 signal_fn(symbol, window, position_state) 계약을
쓰므로, 이 모듈 하나가 두 엔진 모두를 위한 어댑터다(코드 중복 없음).

backtest_engine.py/portfolio_mtm_engine.py 본체는 전혀 수정하지 않는다 -
candidate_b.build_candidate_b_signal()과 완전히 같은 패턴(기존 signal_fn
계약에 맞춰 감싸는 클로저)으로 candidate_c_decision_engine.decide()를
호출한다.

Phase 3.7 갱신 - backtest_engine.py에 typed-intent 부분 축소 hook(선택적
raw_signal 필드 "reduce_to_base_quantity")을 최소 변경으로 추가했으므로,
이 어댑터가 backtest_engine.run_backtest()에 물릴 때는 ReduceIntent가 실제로
포지션 수량을 줄인다(reduce_events로 기록됨).

Phase 3.8 Part 6 갱신 - portfolio_mtm_engine.py도 같은 raw_signal 필드
("reduce_to_base_quantity")를 읽어 자신의 fill_ratio 기반 _close_position
경로로 실제 비례 축소(잔여 리스크/노셔널/wallet 포함)를 반영하도록
확장됐다(portfolio_mtm_engine.py의 해당 dispatch 참고) - 이 어댑터가 아래
INTENT_REDUCE 분기에서 반환하는 dict는 코드 변경 없이 이미 그대로
portfolio_mtm_engine 쪽에서도 실제로 소비된다. 이 문단이 이전에 "portfolio_mtm
쪽은 아직 이 필드를 읽지 않는다"고 적었던 것은 Phase 3.8 이후로는 더 이상
사실이 아니다(이 파일 자체는 그때 이후 수정되지 않았고 설명만 낡아 있었음 -
Phase 3.9 Part 5에서 바로잡음)."""
from __future__ import annotations

from dataclasses import dataclass, field
import bisect
import math

import candidate_c_decision_engine as dec
import entry_overextension_guard
import candidate_c_exit_management as cem
import candidate_c_reversal_state_machine as rsm
import candidate_c_setup_tracker as st
import candidate_c_strategy_policy as strategy_policy_contract
import candidate_c_timeframe_contract as tfc
import mtf_asof
import strategy_indicators as si
import candidate_c_indicator_contract as indicator_contract


def build_causal_indicator_lookup(bars_4h, bars_1h):
    """Precompute unchanged causal indicators; reveal only a requested prefix.

    No rolling 200-bar EMA reset: old observations remain in the recursion.
    Short ATR warmup is evaluated directly to retain its existing semantics.
    """
    return indicator_contract.build_replay_lookup(bars_4h, bars_1h)


@dataclass
class CandidateCBacktestAdapterState:
    """이 어댑터 하나의 실행에 걸쳐 유지되는 영속 상태 - 실제 파일로 저장하지
    않는(순수 인메모리) SetupTracker/PositionEpochStore/ReversalStateStore를
    감싼다(백테스트는 단일 프로세스 순차 실행이라 재시작 복구가 필요 없음)."""
    setup_tracker: st.SetupTracker
    epoch_store: cem.PositionEpochStore
    reversal_machines: dict = field(default_factory=dict)
    reduce_intents_seen: list = field(default_factory=list)
    all_intents_seen: list = field(default_factory=list)


def _apply_completed_reduce_epoch_flags(epoch, reason_code):
    if reason_code in ("partial_take_profit_2r", "PARTIAL_TP_2R_DUST_SAFE_FULL_EXIT"):
        epoch.partial_take_profit_done = True
    elif reason_code == "unified_mfe_profit_giveback":
        epoch.mfe_profit_reduce_done = True
    else:
        epoch.derisk_done = True


def build_candidate_c_signal(
    symbol: str, *, bars_4h: list[dict], bars_1h: list[dict], bars_1d: list[dict],
    lot_step: float, min_size: float, account_id: str = "backtest",
    config_version_id: str = "v1", config_hash: str = "v1",
    risk_per_trade_pct: float = 1.0,  # 2026-09-03 - 백테스트는 activated config가 없으므로
    # 호출부(backtest_engine.py/portfolio_mtm_engine.py)가 명시적으로 넘기는
    # 파라미터가 유일한 출처다(config_version_id/config_hash와 동일한 패턴 -
    # decide() 내부에 하드코딩하지 않음, Phase 3.10.2 Item 5).
    strategy_policy: dict | None = None,
    risk_adaptive_partials: bool = False,
    # 항목7(2026-09-13) STOP-SHIP 수정 - 이 파라미터 자체가 아예 없어서 아래
    # DecisionContext가 항상 strategy_policy=None으로 생성되고 있었다.
    # decide()는 policy가 None이면 방향/ATR 조건을 통과한 진입 신호마저 전부
    # "strategy_policy_missing_or_invalid"로 차단한다 - 즉 이 어댑터를 쓰는
    # 백테스트/포트폴리오 시뮬레이션은 구조적으로 단 한 번도 실제 진입을 낼 수
    # 없었다(fixture 문제도, 전략이 너무 엄격해서도 아니었다 - 직접 계측해서
    # 확인: 동일 fixture에서 방향/ATR 조건까지 통과한 edge가 3건 있었는데 전부
    # 이 버그로 막혔다). 넘기지 않으면 candidate_c_strategy_policy.
    # production_strategy_policy()(실제 운영 정책, candidate_c_preregistration_v2.json
    # 기반)를 기본값으로 쓴다 - 지어낸 값이 아니라 이미 검증된 정식 정책이다.
    adapter_state: CandidateCBacktestAdapterState | None = None,
):
    """반환된 signal_fn은 backtest_engine.run_backtest()와
    portfolio_mtm_engine.run_portfolio_mtm_backtest() 양쪽 모두에 그대로 넘길
    수 있다(둘 다 window가 confirmed 5m bar들의 BarWindow라고 가정)."""
    if strategy_policy is None:
        strategy_policy = strategy_policy_contract.production_strategy_policy()
    if adapter_state is None:
        adapter_state = CandidateCBacktestAdapterState(
            setup_tracker=st.SetupTracker.in_memory(),
            epoch_store=cem.PositionEpochStore.in_memory(),
        )
    weakening_prev = {}
    previous_positions = {}
    pending_management = {}
    pending_entry_weakening_baseline = {}
    pending_pilot_entry = {}
    # Candidate C preregistration defines 1D as telemetry-only.  Do not
    # inspect, normalize, filter, hash, or forward caller-provided 1D values
    # into strategy decisions; malformed telemetry must be observationally
    # identical to no telemetry and cannot affect any action.
    strategy_bars_1d = []

    # 200-bar truncation changes recursive EMA seeds. Preserve full causal
    # HTF history, cache its exact indicators, and bisect on CLOSE time.
    # Only 5m Donchian input is bounded (200 exceeds its exact 42-bar need).
    _LIVE_FETCH_LIMIT = 200
    bars_4h = [dict(b, close_time_ms=b['open_time_ms'] + 14_400_000) for b in bars_4h if b.get("confirm") == 1]
    bars_1h = [dict(b, close_time_ms=b['open_time_ms'] + 3_600_000) for b in bars_1h if b.get("confirm") == 1]
    _bars_4h_times = [b["close_time_ms"] for b in bars_4h]
    _bars_1h_times = [b["close_time_ms"] for b in bars_1h]
    indicator_fn = build_causal_indicator_lookup(bars_4h, bars_1h)

    def _bounded_confirmed(bars, times, as_of_ms):
        idx = bisect.bisect_right(times, as_of_ms)
        return bars[max(0, idx - _LIVE_FETCH_LIMIT):idx]

    def signal_fn(sym, window, position_state=None):
        as_of_ms = window.latest()["open_time_ms"]
        # Store rows use inclusive last-ms labels. Decisions are made at the
        # same exclusive confirmed boundary as live, never one millisecond early.
        decision_time_ms = as_of_ms + 300_000
        decision_five = [dict(b, close_time_ms=b['open_time_ms'] + 300_000)
                         for b in window[-min(len(window), _LIVE_FETCH_LIMIT):]]
        machine = adapter_state.reversal_machines.setdefault(sym, rsm.SymbolReversalMachine(sym))
        if position_state is None:
            machine.converge_authoritative_flat(len(window) - 1)
            previous_positions.pop(sym, None)
            pending_management.pop(sym, None)
            weakening_prev[sym] = False
            if machine.state == rsm.State.REVERSAL_WAIT_FLAT:
                try:
                    snap = tfc.build_as_of_snapshot(
                        as_of_ms, bars_4h=_bounded_confirmed(bars_4h, _bars_4h_times, decision_time_ms),
                        bars_1h=_bounded_confirmed(bars_1h, _bars_1h_times, decision_time_ms),
                        bars_1d=[], bars_5m_for_10m=decision_five,
                    )
                except (tfc.ActionTimestampNotConfirmedError, mtf_asof.MissingConfirmedBarError, ValueError, IndexError):
                    snap = None
                if snap is not None:
                    higher = _bounded_confirmed(bars_4h, _bars_4h_times, decision_time_ms)
                    indicators = indicator_fn(higher, donchian_n=20)[-1]
                    machine.revalidate_and_request_reversal_entry(
                        len(window) - 1,
                        current_4h_direction=dec.direction_from_4h_indicators(
                            ema_20=indicators["ema_20"], ema_50=indicators["ema_50"], close=indicators["close"],
                        ), entry_conditions_still_met=tfc.donchian_setup_condition(snap, machine.pending_side),
                    )

        current_position = None
        if position_state is not None:
            contract_size = position_state.get("contract_size")
            if not isinstance(contract_size, (int, float)) or not math.isfinite(float(contract_size)) or contract_size <= 0:
                raise ValueError("contract_size is required for an open Candidate C position")
            position_id = position_state.get("position_id") or f"{sym}#{position_state.get('entry_time_ms', as_of_ms)}"
            previous = previous_positions.get(sym)
            is_new_position = previous is None or previous["position_id"] != position_id
            if is_new_position:
                weakening_prev[sym] = bool(pending_entry_weakening_baseline.pop(sym, False))
                pending_management.pop(sym, None)
            epoch = adapter_state.epoch_store.get(position_id)
            if epoch.original_contracts is None:
                epoch.original_contracts = float(position_state.get("original_contracts")
                                                 or position_state.get("contracts", 0.0))
                epoch.remaining_contracts = float(position_state.get("contracts", 0.0))
                epoch.weakening_prev = weakening_prev.get(sym, False)
                epoch.pilot_entry = pending_pilot_entry.pop(sym, False)
                adapter_state.epoch_store.save(position_id, epoch)
            pending = pending_management.pop(sym, None)
            if pending and previous and previous["position_id"] == position_id:
                if pending.kind == dec.INTENT_REDUCE and position_state["contracts"] < previous["contracts"]:
                    _apply_completed_reduce_epoch_flags(epoch, pending.reason_code)
                if pending.kind == dec.INTENT_STOP_UPDATE and position_state.get("stop_price") == pending.raw_stop_price:
                    if pending.reason_code == "profit_lock_activated":
                        epoch.profit_lock_active = True
                        epoch.profit_lock_stop_price = pending.raw_stop_price
                adapter_state.epoch_store.save(position_id, epoch)
            initial_stop = position_state.get("initial_stop_price")
            if initial_stop is None:
                initial_stop = (previous or {}).get("initial_stop_price", position_state.get("stop_price"))
            current_position = {
                "side": position_state["side"], "position_id": position_id,
                "contracts": position_state.get("contracts", 1.0),
                "raw_entry_price": position_state.get("raw_entry_price") or position_state["entry_price"],
                "initial_stop_price": initial_stop or position_state["entry_price"],
                "high_water": position_state["high_water"],
                "effective_entry_price": position_state["entry_price"],
                "entry_fee_usdt": position_state.get("entry_fee_usdt", 0.0),
                "contract_size": float(contract_size),
                "fee_rate": position_state.get("fee_rate", 0.0005),
                "spread_bps": position_state.get("spread_bps", 2.0),
                "slippage_bps": position_state.get("slippage_bps", 3.0),
            }
            previous_positions[sym] = current_position.copy()
            if machine.state == rsm.State.FLAT:
                machine.request_entry(position_state["side"])
                machine.confirm_entry_filled()
            elif machine.state == rsm.State.REVERSAL_ENTRY_PENDING:
                machine.reconcile_authoritative_position(
                    authoritative_side=position_state["side"], tick_index=len(window) - 1,
                )

        ctx = dec.DecisionContext(
            account_id=account_id, symbol=sym, strategy_id="candidate_c",
            config_version_id=config_version_id, config_hash=config_hash,
            risk_per_trade_pct=risk_per_trade_pct, strategy_policy=strategy_policy,
            risk_adaptive_partials=risk_adaptive_partials,
        )
        intent = dec.decide(
            ctx, as_of_ms=as_of_ms,
            bars_4h_confirmed_up_to_asof=_bounded_confirmed(bars_4h, _bars_4h_times, decision_time_ms),
            bars_1h_confirmed_up_to_asof=_bounded_confirmed(bars_1h, _bars_1h_times, decision_time_ms),
            bars_1d_confirmed_up_to_asof=strategy_bars_1d,
            # window도 4h/1h와 같은 이유로 마지막 200봉만 자른다 - len(window)
            # 전체를 매번 새 리스트로 복사하면 tick이 진행될수록(=백테스트 구간이
            # 길수록) 매 호출 비용이 계속 커진다(위 4h/1h 주석 참고, 동일한 근본
            # 원인). BarWindow는 자체 슬라이스 문법이 look-ahead 방지(_resolve)를
            # 그대로 적용하므로 안전하다.
            bars_5m_for_10m_up_to_asof=decision_five,
            setup_tracker=adapter_state.setup_tracker, epoch_store=adapter_state.epoch_store,
            reversal_machine=machine, current_position=current_position,
            weakening_prev=weakening_prev.get(sym, False), lot_step=lot_step, min_size=min_size,
            indicator_fn=indicator_fn,
        )
        adapter_state.all_intents_seen.append(intent)
        if current_position is not None:
            if intent.reduction_policy_hash:
                epoch.reduction_policy_hash = intent.reduction_policy_hash
            history_1h = _bounded_confirmed(bars_1h, _bars_1h_times, decision_time_ms)
            if history_1h:
                ema = indicator_fn(history_1h, donchian_n=20)[-1]["ema_20"]
                weakening_prev[sym] = dec.weakening_from_1h(
                    side=current_position["side"], close_1h=history_1h[-1]["close"], ema_20_1h=ema,
                )
                row4 = indicator_fn(_bounded_confirmed(bars_4h, _bars_4h_times, decision_time_ms), donchian_n=20)[-1]
                cem.observe_pilot_4h_confirmation(epoch,
                    side=current_position["side"], direction_4h=dec.direction_from_4h_indicators(
                        ema_20=row4.get("ema_20"), ema_50=row4.get("ema_50"), close=row4.get("close")))
                epoch.weakening_prev = weakening_prev[sym]
                adapter_state.epoch_store.save(current_position["position_id"], epoch)

        # decide()는 setup_tracker 상태를 직접 바꾸지 않는다(결정/영속화 분리 -
        # candidate_c_decision_engine.py의 설계 원칙). 포지션이 없을 때는 이번 tick의
        # 실제 long/short 조건을 매번 관측해서 갱신해야, 다음 tick에서 정확한
        # False->True edge를 판정할 수 있다(entry가 실제로 났을 때만 갱신하면 edge
        # 감지가 깨진다 - 이 어댑터에서 처음에 그렇게 짰다가 발견한 버그).
        if current_position is None:
            try:
                snap = tfc.build_as_of_snapshot(
                    as_of_ms,
                    bars_4h=_bounded_confirmed(bars_4h, _bars_4h_times, decision_time_ms),
                    bars_1h=_bounded_confirmed(bars_1h, _bars_1h_times, decision_time_ms),
                    bars_1d=strategy_bars_1d,
                    bars_5m_for_10m=decision_five,
                )
            except (
                tfc.ActionTimestampNotConfirmedError,
                mtf_asof.MissingConfirmedBarError,
                ValueError,
                IndexError,
            ):
                snap = None  # 워밍업 부족 - decide()도 같은 이유로 NoAction
            if snap is not None:
                # Persistence failures are intentionally outside the warmup
                # exception boundary and must never be hidden.
                dec.observe_entry_setups(adapter_state.setup_tracker, sym, snap,
                    bars_4h=_bounded_confirmed(bars_4h, _bars_4h_times, decision_time_ms),
                    bars_1h=_bounded_confirmed(bars_1h, _bars_1h_times, decision_time_ms),
                    bars_5m=decision_five, indicator_fn=indicator_fn)

        if (
            current_position is None
            and intent.kind == dec.INTENT_NO_ACTION
            and intent.setup_id
            and intent.reason_code in (
                entry_overextension_guard.BLOCK_REASON,
                entry_overextension_guard.DATA_REASON,
            )
        ):
            if (
                snap is not None
                and adapter_state.setup_tracker.pending_timeout_retry_for_bar(
                    intent.symbol, intent.side, snap.bar_10m_current["open_time_ms"],
                ) == intent.setup_id
            ):
                adapter_state.setup_tracker.mark_timeout_retry_consumed(intent.setup_id)
            adapter_state.setup_tracker.record_attempt_outcome(
                intent.setup_id, f"rejected:{intent.reason_code}",
            )

        if intent.kind == dec.INTENT_ENTRY:
            pending_entry_weakening_baseline[sym] = bool(getattr(intent, "entry_weakening_baseline", False))
            pending_pilot_entry[sym] = intent.pilot_entry
            adapter_state.setup_tracker.record_attempt_outcome(intent.setup_id, "accepted")
            return {"target_side": intent.side, "stop_price": intent.raw_stop_price, "entry_size_fraction": getattr(intent, "entry_size_fraction", 1.0)}
        if intent.kind == dec.INTENT_REVERSAL:
            machine.request_reversal(len(window) - 1, intent.target_side)
            return "flat"
        if intent.kind == dec.INTENT_EXIT:
            machine.request_exit()
            return "flat"
        if intent.kind == dec.INTENT_STOP_UPDATE:
            pending_management[sym] = intent
            return {"target_side": position_state["side"], "stop_price": intent.raw_stop_price}
        if intent.kind == dec.INTENT_REDUCE:
            pending_management[sym] = intent
            adapter_state.reduce_intents_seen.append(intent)
            if position_state is None:
                return "flat"
            contract_size = position_state.get("contract_size")
            if contract_size is None or contract_size <= 0:
                return {"target_side": position_state["side"], "stop_price": None}
            target_residual_contracts = intent.target_residual
            if target_residual_contracts is None:
                # Dust-safe full reductions intentionally carry no residual.
                # Derive the explicit zero/remaining contract target at this
                # engine boundary instead of attempting arithmetic on None.
                target_residual_contracts = max(
                    float(position_state.get("contracts", 0.0))
                    - float(intent.reduce_quantity or 0.0),
                    0.0,
                )
            return {
                "target_side": position_state["side"], "stop_price": None,
                # Candidate C intents are contracts; both simulation engines'
                # existing reduce hook is explicitly base-coin quantity.
                "reduce_to_base_quantity": target_residual_contracts * contract_size,
            }
        return "flat" if position_state is None else {"target_side": position_state["side"], "stop_price": None}

    return signal_fn
