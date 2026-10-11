import entry_attempt_notifications
"""Candidate C 하이브리드 엔진(DOGE/SOL)의 사이클 오케스트레이터.

두 단계:
1. run_startup_reconciliation(snap) - 이 심볼을 처음 맡거나 재시작했을 때 딱 한 번.
   candidate_c_position_reconciliation.reconcile_symbol_at_startup()을 그대로
   재사용한다 - 조금이라도 불명확하면 신규진입만 차단하고 기존 포지션/주문은
   절대 건드리지 않는다(fail-closed, 자동 정리 없음).
2. run_steady_state_cycle(...) - 매 사이클(확정봉 기준) 신호 계산 -> 실행 ->
   성공했을 때만 상태 영속화.

CORE의 position_ai_review(REDUCE_50)는 이 파일 어디에서도 참조하지 않는다 -
Candidate C 포지션의 중간 관리는 전략 고유 로직(candidate_c_exit_management)과
거래소 부착 SL/TP만으로 이뤄진다."""
import math
import time
from dataclasses import dataclass, field

import candidate_c_decision_engine as dec
import candidate_c_manual_close as candidate_manual_close
import candidate_c_exit_management as cem
import entry_overextension_guard
import candidate_c_hybrid_live_adapter as live
import candidate_c_hybrid_ownership as ownership
import candidate_c_intent_ledger as il
import candidate_c_position_reconciliation as recon
import candidate_c_reversal_state_machine as rsm
import candidate_c_setup_tracker as st
import candidate_c_timeframe_contract as tfc
from adaptive_exit_policy import production_adaptive_exit_policy
import strategy_indicators as si
import order_safety
import candidate_c_cycle_reconciliation as cycle_recon

# [2026-09-16, 사용자 직접 지시 - timeout 1회 재검토 정책, 기본 OFF] 최초
# GPT 게이트 결과가 정상적인 wait/reject가 아니라 기술적 timeout일 때만,
# "원래 setup 판단에 사용한 확정 10분봉의 바로 다음 10분봉"에서 딱 한 번
# 재검토한다(사용자 지시 원문 그대로 - 5분 평가 주기나 다음 폴링이 아니다).
# 이 상수 하나로 "바로 다음"의 정의를 고정한다 - 다른 곳에서 임의로
# 재해석하지 않는다.
_TEN_MINUTES_MS = 10 * 60 * 1000


def _maybe_arm_timeout_retry(cfg, setup_tracker: "st.SetupTracker", intent, result: dict,
                              source_bar_10m_open_time_ms: int | None) -> None:
    """최초(재검토가 아닌) 진입 시도가 기술적 timeout으로 끝났을 때만, 정책이
    켜져 있으면 바로 다음 확정 10분봉에 1회 재검토 기회를 arm한다. wait/reject/
    다른 오류/재검토 자신의 timeout은 전부 여기서 걸러진다(호출부가 미리
    걸러서 넘기지 않아도 이 함수 하나로 항상 안전하다)."""
    if not getattr(cfg, "CANDIDATE_C_TIMEOUT_RETRY_ENABLED", False):
        return
    if intent.reason_code == "timeout_retry_edge_triggered":
        return  # 재검토 자신의 결과로는 또 다른 재검토를 arm하지 않는다.
    if result.get("gate_result") != "blocked_error" or result.get("error_reason") != "timeout":
        return  # wait/reject/그 외 오류는 이 정책의 대상이 아니다.
    if source_bar_10m_open_time_ms is None:
        return  # 원 10분봉을 특정할 수 없으면(snap 계산 실패 등) arm하지 않는다.
    setup_tracker.arm_timeout_retry(
        intent.symbol, intent.side, intent.setup_id,
        retry_bar_open_time_ms=source_bar_10m_open_time_ms + _TEN_MINUTES_MS,
    )


def _intent_observability_fields(intent) -> dict:
    """decide()가 이미 계산한 reason_code/setup_id/시각/세대 식별자를 호출부
    반환 dict에 그대로 노출한다(2026-09-15, 사용자 직접 지시) - 지금까지는
    intent.kind=="NoAction"이면 이 정보 전부가 버려져서(기존 return
    {"executed": False, "intent_kind": intent.kind}), 대시보드가 "왜
    NoAction이었는지"를 전혀 보여줄 수 없었다. 새 계산은 하지 않고, decide()가
    이미 만든 Intent에 있는 값만 그대로 옮긴다."""
    return {
        "reason_code": intent.reason_code,
        "symbol":intent.symbol,"side":intent.side,"kind":intent.kind,
        "entry_attempt":intent.entry_attempt,"entry_validation_values":intent.entry_validation_values,
        "idempotency_key":intent.idempotency_key,
        "raw_stop_price":intent.raw_stop_price,"raw_target_price":intent.raw_target_price,
        "reduction_plan":getattr(intent, 'reduction_plan', None),
        "reduction_policy_hash":getattr(intent, 'reduction_policy_hash', None),
        "reduce_quantity":intent.reduce_quantity,"target_residual":intent.target_residual,
        "pilot_entry": getattr(intent, "pilot_entry", False),
        "entry_size_fraction": getattr(intent, "entry_size_fraction", 1.0),
        "entry_weakening_baseline": getattr(intent, "entry_weakening_baseline", None),
        "setup_id": intent.setup_id,
        "decision_timestamp": intent.decision_timestamp,
        "source_candle_close_timestamp": intent.source_candle_close_timestamp,
        "config_version_id": intent.config_version_id,
        "config_hash": intent.config_hash,
    }


def _safe_record_attempt_outcome(cfg, symbol: str, setup_tracker: "st.SetupTracker", setup_id: str, outcome: str) -> None:
    """setup_tracker에 결과를 기록하다 실패해도(디스크 오류 등) 사이클 전체를
    죽이지 않는다(항목4, tracker-save-failure-after-fill) - 이 함수가 호출되는
    시점엔 reversal_machine이 이미 confirm_entry_filled()/observe_entry_outcome()
    으로 정확히 전이된 뒤이므로, 이 기록이 실패해도 같은 신호로 중복 주문이
    나갈 위험은 없다(decide()도, 이 파일의 FLAT 방어 재확인도 이미 막아준다).
    다만 setup_tracker.pending_setup_ids()의 정확도만 "아직 안 끝남" 쪽으로
    떨어진다(안전한 방향의 오차)."""
    try:
        setup_tracker.record_attempt_outcome(setup_id, outcome)
    except Exception:
        if cfg.logger is not None:
            cfg.logger.exception(
                "[%s] setup_tracker.record_attempt_outcome 실패(setup_id=%s, outcome=%s) - "
                "reversal_machine은 이미 정확히 전이됐으므로 중복 주문 위험은 없음",
                symbol, setup_id, outcome,
            )


def _consume_overextension_no_action(
    cfg, symbol: str, setup_tracker: "st.SetupTracker", intent,
    source_bar_10m_open_time_ms: int | None,
) -> bool:
    """확정적으로 주문 전 NoAction이 된 setup을 완료 처리한다.

    과거에는 overextension만 소비했지만, Candidate C가 조건 True인 새 10분봉마다
    setup_id를 발급하므로 현재 봉의 방향/ATR/정책/리스크 거절도 pending으로 남기면
    재시작 때 stale setup이 불필요하게 누적된다. timeout retry는 기존 원 setup_id
    계약을 그대로 보존한다.
    """
    if not intent.setup_id:
        return False

    current_setup_id = None
    if source_bar_10m_open_time_ms is not None and intent.side:
        current_setup_id = st.make_setup_id(
            intent.symbol, intent.side, source_bar_10m_open_time_ms,
        )
    is_current_bar_setup = intent.setup_id == current_setup_id
    is_overextension = intent.reason_code in (
        entry_overextension_guard.BLOCK_REASON,
        entry_overextension_guard.DATA_REASON,
    )
    is_timeout_retry = (
        source_bar_10m_open_time_ms is not None
        and setup_tracker.pending_timeout_retry_for_bar(
            intent.symbol, intent.side, source_bar_10m_open_time_ms,
        ) == intent.setup_id
    )
    if not is_current_bar_setup and not (is_overextension and is_timeout_retry):
        return False
    if is_timeout_retry:
        setup_tracker.mark_timeout_retry_consumed(intent.setup_id)
    _safe_record_attempt_outcome(
        cfg, symbol, setup_tracker, intent.setup_id,
        f"rejected:{intent.reason_code}",
    )
    return True


def run_startup_reconciliation(snap: recon.SymbolSnapshot) -> dict:
    """반환: {"status":, "reason":, "new_entries_allowed": bool}. 이 함수 자체는
    아무 것도 취소/청산하지 않는다(read-only 판정) - STATUS_LEGACY_BLOCKED나
    STATUS_RECONCILE_REQUIRED가 나오면 운영자가 직접 확인해야 한다."""
    result = recon.reconcile_symbol_at_startup(snap)
    return {
        "status": result["status"], "reason": result["reason"],
        "new_entries_allowed": result["status"] in (recon.STATUS_NO_POSITION, recon.STATUS_ADOPTED),
    }


@dataclass
class HybridEngineState:
    """이 심볼 하나에 대해 사이클 간 유지되는 영속 상태 - 전부 파일 기반(프로세스
    재시작에도 살아남음, candidate_c_exit_management/setup_tracker/
    reversal_state_machine/intent_ledger 자체가 이미 crash-safe하게 설계돼 있음을
    그대로 재사용). intent_ledger가 "지금 이 심볼의 보호된 진입이 어느 position_id
    (=intent_id)인지"를 알려줘야 epoch_store에서 그 상태를 꺼낼 수 있다."""
    setup_tracker: "st.SetupTracker"
    epoch_store: "cem.PositionEpochStore"
    reversal_store: "rsm.ReversalStateStore"
    intent_ledger: "il.IntentLedger"
    # 항목3(2026-09-13) - SAFE_HALT 상태에서 안전청산을 자동으로 몇 번 재시도했는지
    # 심볼별로 센다(프로세스 재시작 시 리셋됨 - 재시작 자체가 별도로 startup
    # reconciliation을 다시 거치므로 안전한 방향의 리셋이다). 무한 재시도로 API를
    # 낭비하거나 예측 불가능하게 반복하지 않도록 상한(SAFE_HALT_MAX_AUTO_RECOVERY_
    # ATTEMPTS)과 함께 쓴다.
    safe_halt_recovery_attempts: dict = field(default_factory=dict)
    shadow_setup_tracker: "st.SetupTracker" = field(default_factory=st.SetupTracker.in_memory)
    # Telegram delivery is performed by the outer symbol loop, after account locks
    # are released. Reconciliation queues only newly journaled lifecycle events.
    notification_events: list = field(default_factory=list)
    notification_store: object | None = None


def _build_entry_still_valid_fn(client, intent: "dec.Intent", snapshot: dict):
    """항목6(2026-09-13) - GPT 승인 전후로 "그 사이 가격이 되돌아가서 돌파 자체가
    무효화되지 않았는지" 실시간으로 재확인한다(이전에는 lambda: True로 항상
    통과시켜서 이 재검증이 실질적으로 없는 것과 같았다).

    가격 출처(항목4, 2026-09-13 명시): client.fetch_last_price()는 OKX 티커의
    "last"(최근 체결가)다 - mark price가 아니다. 이 함수는 snapshot에 담긴 다른
    가격(예: current_price=확정 5분봉 종가)을 절대 섞어쓰지 않는다 - 오직
    fetch_last_price()의 실시간 값 하나만 기준으로 판단한다(출처 혼용 방지).
    okx_client.create_position_with_sl_tp()도 이제 triggerPriceType="last"를
    명시적으로 제출하므로(2026-09-13 수정) staleness 재검증/SL·TP 실제 발동
    기준이 서로 다른 가격 기준을 섞어 쓰지 않는다.

    무효 조건(side별 대칭):
    - 실시간가가 신호와 동일한 Donchian 1% 접근 범위 밖으로 되돌아왔다.
      신호의 접근 허용을 주문 직전 완전 돌파 조건으로 다시 뒤집지 않는다.
    - 실시간가가 이미 최초 stop을 넘어섰다 - 지금 진입해도 곧바로 손실 확정.
    - 실시간가가 이미 최초 target을 넘어섰다 - 뒤늦은 진입(추격).
    가격을 확인할 수 없거나(조회 실패/None) 유한하지 않거나(NaN/inf) 0 이하이면
    보수적으로 무효 처리한다(fail-closed) - "확인 안 됨"을 "유효함"으로 낙관하지
    않는다. NaN은 파이썬 비교 연산이 전부 False를 반환해(NaN < x, NaN >= x 등)
    아래 조건들을 전부 그냥 통과해버리는 함정이 있어 명시적으로 먼저 걸러낸다."""
    side = intent.side
    donchian_upper = snapshot.get("donchian_upper")
    donchian_lower = snapshot.get("donchian_lower")
    stop_price = intent.raw_stop_price
    target_price = intent.raw_target_price
    approval_started_ms = int(time.time()*1000)

    def _validate_price(current_price, *, now_ms=None) -> bool:
        if now_ms is not None:
            try:
                decision_age = int(now_ms)-int(intent.decision_timestamp)
                candle_age = int(now_ms)-int(intent.source_candle_close_timestamp)
                if not (0 <= int(now_ms)-approval_started_ms <= 180000
                        and 0 <= decision_age <= 360000 and 0 <= candle_age <= 360000):
                    return False
            except (TypeError, ValueError):
                return False
        if current_price is None:
            return False
        try:
            current_price = float(current_price)
        except (TypeError, ValueError):
            return False
        if not math.isfinite(current_price) or current_price <= 0:
            return False
        if side == "long":
            if donchian_upper is not None and current_price < donchian_upper * (1.0 - tfc.DONCHIAN_ENTRY_PROXIMITY_PCT / 100.0):
                return False
            if stop_price is not None and current_price <= stop_price:
                return False
            if target_price is not None and current_price >= target_price:
                return False
        elif side == "short":
            if donchian_lower is not None and current_price > donchian_lower * (1.0 + tfc.DONCHIAN_ENTRY_PROXIMITY_PCT / 100.0):
                return False
            if stop_price is not None and current_price >= stop_price:
                return False
            if target_price is not None and current_price <= target_price:
                return False
        context = snapshot.get('entry_guard_context')
        if context is not None:
            # Reuse the causal decision guard, including both fixed-percent chase
            # and ATR exhaustion/pullback rules, at this exact executable quote.
            recent = dec._candidate_entry_overextension_gate(
                side=side, entry_price=current_price, **context)
            if not recent.get('allowed'):
                return False
        return True

    def _still_valid() -> bool:
        try:
            return _validate_price(client.fetch_last_price())
        except Exception:
            return False

    # Final execution must validate the exact quote used for sizing, with clocks.
    _still_valid.validate_price = _validate_price
    return _still_valid


SAFE_HALT_MAX_AUTO_RECOVERY_ATTEMPTS = 3  # 이 한도를 넘으면 더 이상 자동 재시도하지
# 않고 CRITICAL 로그만 남긴다(무한 재시도로 API를 낭비하거나 예측 불가능하게
# 반복하지 않기 위함) - 그 이후의 조치는 사람의 몫이다.


def _handle_safe_halt_cycle(cfg, client, symbol: str, machine: "rsm.SymbolReversalMachine",
                             state: "HybridEngineState") -> dict:
    with ownership.account_order_lock(cfg.user_dir):
        return _handle_safe_halt_cycle_locked(cfg, client, symbol, machine, state)


def _handle_safe_halt_cycle_locked(cfg, client, symbol, machine, state) -> dict:
    """항목3(2026-09-13) - SAFE_HALT는 신규 진입만 영구 차단할 뿐, 무보호
    포지션을 방치하면 안 된다(재시작 후 reconciliation 대상에서 아예 빠지는
    것과, "지금 당장은" 매 사이클 계속 관찰/재시도하는 것은 다른 문제다). 매
    사이클(확정 5분봉마다) 실제 거래소 포지션/보호주문을 다시 조회하고,
    무보호 상태가 확인되면 제한된 횟수만 안전청산을 재시도한다.

    재부착이 아니라 안전청산을 시도하는 이유: SAFE_HALT는 애초에 우리 내부
    상태(EpochState 등)를 신뢰할 수 없어서 들어온 상태다 - 그 상태에서 "원래
    SL/TP가 얼마였는지" 추측해서 재부착하면 잘못된 보호주문을 붙일 위험이
    있다. 포지션을 없애는 쪽(안전청산)이 무보호로 방치하는 것보다 항상 더
    안전한 방향이므로 그것만 시도한다.

    절대 SAFE_HALT를 자동으로 해제하지 않는다(사람이 직접 확인 후 수동으로
    reversal_machine 상태를 되돌려야 한다 - 그 수동 해제 절차 자체는 이
    구현 범위 밖이다)."""
    try:
        position = client.fetch_position()
    except Exception as exc:
        if cfg.logger is not None:
            cfg.logger.critical("[%s] SAFE_HALT: 포지션 조회 실패 - 상태 확인 불가(%s)", symbol, exc)
        return {"executed": False, "intent_kind": "SAFE_HALT_MONITOR", "reason": "position_check_failed", "critical": True}

    if position is None:
        try:
            orphan_algo_ids = client.fetch_pending_protection_algo_ids()
        except Exception:
            return {"executed": False, "intent_kind": "SAFE_HALT_MONITOR",
                    "reason": "protection_check_unknown", "critical": True}
        if orphan_algo_ids is None:
            return {"executed": False, "intent_kind": "SAFE_HALT_MONITOR",
                    "reason": "protection_check_unknown", "critical": True}
        if not orphan_algo_ids:
            if any(r.kind == dec.INTENT_STOP_UPDATE for r in state.intent_ledger.pending_intents()):
                return {"executed": False, "intent_kind": "SAFE_HALT_MONITOR",
                        "reason": "stop_submission_UNKNOWN", "critical": True}
            if cfg.logger is not None:
                cfg.logger.warning(
                    "[%s] SAFE_HALT: 포지션/보호주문 모두 없음 확인 - 위험은 해소된 것으로 보이나 "
                    "SAFE_HALT는 사람이 직접 확인 후 해제해야 한다(자동 복귀 안 함)", symbol,
                )
            return {"executed": False, "intent_kind": "SAFE_HALT_MONITOR", "reason": "clear_requires_manual_release"}
        if not getattr(cfg, "CANDIDATE_C_LIVE_EXECUTE", False):
            return {"executed": False, "intent_kind": "SAFE_HALT_MONITOR", "reason": "shadow_orphan_observed"}
        # A symbol-scoped client does not establish ownership of an old bracket.
        proof = ownership.validate_candidate_c_orphan_protection_owner(
            client, symbol, state.intent_ledger, state.epoch_store,
        )
        if not proof["allowed"]:
            return {"executed": False, "intent_kind": "SAFE_HALT_MONITOR",
                    "reason": "orphan_ownership_unknown", "critical": True}
        # 포지션 없이 보호주문(algo)만 남은 고아 상태 - 정리를 시도한다.
        try:
            client.cancel_protection(proof["algo_ids"])
            if cfg.logger is not None:
                cfg.logger.warning("[%s] SAFE_HALT: 고아 보호주문 정리 시도 완료(%s)", symbol, orphan_algo_ids)
        except Exception as exc:
            if cfg.logger is not None:
                cfg.logger.critical("[%s] SAFE_HALT: 고아 보호주문 정리 실패(%s)", symbol, exc)
        return {"executed": False, "intent_kind": "SAFE_HALT_MONITOR", "reason": "orphan_protection_cleanup_attempted"}

    # 포지션이 실제로 존재한다 - 보호주문이 있는지 확인한다.
    try:
        existing_algo_ids = client.fetch_pending_protection_algo_ids()
    except Exception:
        return {"executed": False, "intent_kind": "SAFE_HALT_MONITOR",
                "reason": "protection_check_unknown", "critical": True}
    if existing_algo_ids is None:
        return {"executed": False, "intent_kind": "SAFE_HALT_MONITOR",
                "reason": "protection_check_unknown", "critical": True}
    owned = None
    if existing_algo_ids:
        # allow_missing_protection=True(2026-09-16, 사용자 직접 지시 - 상태6 경우A
        # 최소 수정) - 체결 확인 후 mark_protected() 전에 SAFE_HALT로 들어온 경우도
        # 이 첫 번째 확인에서 같은 이유로 "ownership_unknown"에 영구히 막혀 있었다
        # (실제 재현 확인됨). 아래쪽의 allow_missing_protection=True 재확인과
        # 완전히 같은 이유로 여기서도 필요하다.
        owned = ownership.validate_candidate_c_position_owner(
            client, symbol, state.intent_ledger, state.epoch_store, allow_missing_protection=True,
        )
        if not owned["allowed"]:
            return {"executed": False, "intent_kind": "SAFE_HALT_MONITOR",
                    "reason": "position_ownership_unknown", "critical": True}
        catch_up_ids = owned.get("needs_protected_catch_up")
        if catch_up_ids:
            # 실제 보호주문이 우리 것으로 확인됐지만 ledger 기록만 아직 못
            # 따라간 상태 - 지금 확인된 값으로 뒤늦게 기록한다(새로 만들지 않음).
            state.intent_ledger.mark_protected(owned["entry_record"].intent_id, catch_up_ids)
        epoch = state.epoch_store.get(owned["entry_record"].intent_id)
        try:
            verified = order_safety.verify_protection(
                client, epoch.side, epoch.remaining_contracts,
                expected_sl_price=epoch.current_stop_price or epoch.initial_stop_price,
                expected_tp_price=epoch.target_price,
            )
        except Exception:
            verified = {"ok": False, "reason": "oco_lookup_failed"}
        if verified["ok"]:
            return {"executed": False, "intent_kind": "SAFE_HALT_MONITOR", "reason": "position_protected_observed"}
        if verified.get("reason") != "oco_mismatch":
            return {"executed": False, "intent_kind": "SAFE_HALT_MONITOR",
                    "reason": "protection_check_unknown", "critical": True}
        # A positively owned but mismatched bracket does not protect the
        # intended position. The durable safety-close path below handles it.

    if not getattr(cfg, "CANDIDATE_C_LIVE_EXECUTE", False):
        return {"executed": False, "intent_kind": "SAFE_HALT_MONITOR", "reason": "shadow_unprotected_observed"}
    owned = owned or ownership.validate_candidate_c_position_owner(
        client, symbol, state.intent_ledger, state.epoch_store, allow_missing_protection=True,
    )
    if not owned["allowed"]:
        return {"executed": False, "intent_kind": "SAFE_HALT_MONITOR",
                "reason": "position_ownership_unknown", "critical": True}
    position = owned["position"]
    attempts = state.safe_halt_recovery_attempts.get(symbol, 0)
    if attempts >= SAFE_HALT_MAX_AUTO_RECOVERY_ATTEMPTS:
        if cfg.logger is not None:
            cfg.logger.critical(
                "[%s] SAFE_HALT: 자동 안전청산 재시도 한도(%d회) 소진 - 무보호 포지션 방치 위험, "
                "즉시 사람 확인 필요", symbol, SAFE_HALT_MAX_AUTO_RECOVERY_ATTEMPTS,
            )
        return {
            "executed": False, "intent_kind": "SAFE_HALT_MONITOR",
            "reason": "recovery_attempts_exhausted", "critical": True,
        }

    state.safe_halt_recovery_attempts[symbol] = attempts + 1
    try:
        entry = owned["entry_record"]
        epoch = state.epoch_store.get(entry.intent_id)
        emergency = dec.Intent(
            kind=dec.INTENT_EXIT, account_id=entry.account_id, symbol=symbol,
            strategy_id="candidate_c", setup_id=None, position_epoch=entry.intent_id,
            config_version_id=entry.config_version_id, config_hash=entry.config_hash,
            decision_timestamp=0, source_candle_close_timestamp=0, side=position["side"],
            idempotency_key="safe_halt:" + entry.intent_id, reason_code="safe_halt_unprotected",
            input_snapshot_hash="safe_halt:" + entry.intent_id,
        )
        result = live.execute_intent(
            cfg, client, emergency, snapshot={}, equity=0., is_still_valid_fn=lambda: False,
            current_position=recon.build_candidate_c_position_state(epoch),
            ledger=state.intent_ledger, epoch_store=state.epoch_store,
            allow_missing_protection=True,
        )
        if not result.get("flat_confirmed"):
            return {"executed": False, "intent_kind": "SAFE_HALT_MONITOR",
                    "reason": "safety_close_pending", "critical": True}
        if cfg.logger is not None:
            cfg.logger.critical(
                "[%s] SAFE_HALT: 무보호 포지션 확인 -> 안전청산 시도 성공(%d/%d회째)",
                symbol, attempts + 1, SAFE_HALT_MAX_AUTO_RECOVERY_ATTEMPTS,
            )
        return {
            "executed": False, "intent_kind": "SAFE_HALT_MONITOR",
            "reason": "unprotected_position_safety_closed", "critical": True,
        }
    except Exception as exc:
        if cfg.logger is not None:
            cfg.logger.critical(
                "[%s] SAFE_HALT: 무보호 포지션 안전청산 재시도 실패(%d/%d회째) - %s",
                symbol, attempts + 1, SAFE_HALT_MAX_AUTO_RECOVERY_ATTEMPTS, exc,
            )
        return {
            "executed": False, "intent_kind": "SAFE_HALT_MONITOR",
            "reason": "safety_close_retry_failed", "critical": True,
        }


def _current_position_from_epoch(state: "HybridEngineState") -> dict | None:
    """intent_ledger에서 이 심볼의 보호된 진입(entry_record)을 찾아 그 position_id로
    epoch_store를 조회한다. ambiguous(확정 불가)면 보수적으로 포지션이 없다고
    보지 않고 None을 반환해 신규 진입 자체가 막히게 한다(호출부가 별도로
    ambiguous 여부를 startup reconciliation에서 이미 걸렀어야 정상 경로에서는
    도달하지 않는다 - 방어적 이중 확인)."""
    entry_record, ambiguous = state.intent_ledger.find_protected_entry()
    if ambiguous or entry_record is None:
        return None
    epoch = state.epoch_store.get(entry_record.intent_id)
    if epoch is None or epoch.side is None:
        return None
    return recon.build_candidate_c_position_state(epoch)


def run_manual_close_request(
    cfg, client, symbol: str, *, state: HybridEngineState, account_id: str,
    config_version_id: str, config_hash: str, strategy_policy: dict,
    now_ms: int | None = None,
) -> dict | None:
    """Consume one durable Candidate C manual-close request through ExitIntent."""
    request = candidate_manual_close.get(cfg.user_dir, symbol)
    if not request or request.get("status") in ("confirmed", "failed", "completed"):
        return None

    now_ms = int(time.time() * 1000) if now_ms is None else int(now_ms)
    cooldown_seconds = int(getattr(cfg, "REENTRY_COOLDOWN_MINUTES", 15)) * 60

    with ownership.account_order_lock(cfg.user_dir):
        observation = cycle_recon.reconcile_managed_position(
            cfg, client, symbol, state, now_ms,
        )
    if observation.get("critical"):
        candidate_manual_close.patch(
            cfg.user_dir, symbol, request["close_id"],
            status="pending", last_result="reconciliation_critical",
            error=str(observation.get("reason"))[:500],
        )
        return {
            "intent_kind": dec.INTENT_EXIT, "executed": False, "pending": True,
            "critical": True, "reason": "manual_close_reconciliation_critical",
        }

    with ownership.account_order_lock(cfg.user_dir):
        state.intent_ledger.refresh()
        current_position = _current_position_from_epoch(state)
        machine = state.reversal_store.get(symbol)

        if current_position is None:
            try:
                parent = state.intent_ledger.get(request["position_epoch"])
                parent_terminal = parent.state == il.IntentState.TERMINAL.value
                exchange_position = client.fetch_position()
                algo_ids = client.fetch_pending_protection_algo_ids()
                open_orders = client.exchange.fetch_open_orders(symbol)
            except Exception as exc:
                candidate_manual_close.patch(
                    cfg.user_dir, symbol, request["close_id"],
                    status="pending", last_result="flat_recovery_unknown",
                    error=str(exc)[:500],
                )
                return {
                    "intent_kind": dec.INTENT_EXIT, "executed": False,
                    "pending": True, "reason": "manual_close_flat_recovery_unknown",
                }
            if parent_terminal and exchange_position is None and algo_ids == [] and open_orders == []:
                machine.converge_authoritative_flat(now_ms)
                state.reversal_store.persist(symbol, now_ms)
                confirmed = candidate_manual_close.confirm(
                    cfg.user_dir, symbol, request["close_id"],
                    cooldown_seconds=cooldown_seconds, now=now_ms / 1000.0,
                )
                return {
                    "intent_kind": dec.INTENT_EXIT, "executed": True,
                    "flat_confirmed": True, "recovered": True,
                    "manual_close": confirmed,
                }
            return {
                "intent_kind": dec.INTENT_EXIT, "executed": False,
                "pending": True, "reason": "manual_close_position_state_unresolved",
            }

        if current_position.get("position_id") != request.get("position_epoch"):
            failed = candidate_manual_close.fail(
                cfg.user_dir, symbol, request["close_id"],
                "position_identity_changed", now=now_ms / 1000.0,
            )
            return {
                "intent_kind": dec.INTENT_EXIT, "executed": False,
                "reason": "manual_close_position_identity_changed",
                "manual_close": failed,
            }

        outstanding = [
            record for record in state.intent_ledger.pending_intents()
            if record.kind != dec.INTENT_ENTRY
        ]
        blocking_outstanding = [
            record for record in outstanding if record.kind != dec.INTENT_STOP_UPDATE
        ]
        if blocking_outstanding:
            candidate_manual_close.patch(
                cfg.user_dir, symbol, request["close_id"],
                status="pending", last_result="waiting_for_management_reconcile",
                error=None,
            )
            return {
                "intent_kind": dec.INTENT_EXIT, "executed": False,
                "pending": True, "reason": "manual_close_waiting_for_management_reconcile",
            }

        if machine.state == rsm.State.EXIT_PENDING:
            machine.observe_exit_outcome(filled=False, unknown=False)
            state.reversal_store.persist(symbol, now_ms)

        if machine.state not in (rsm.State.LONG, rsm.State.SHORT):
            candidate_manual_close.patch(
                cfg.user_dir, symbol, request["close_id"],
                status="pending", last_result=f"waiting_for_state:{machine.state.value}",
                error=None,
            )
            return {
                "intent_kind": dec.INTENT_EXIT, "executed": False,
                "pending": True, "reason": "manual_close_state_not_ready",
            }

        requested_at_ms = int(float(request["started_at"]) * 1000)
        key_id = f'{current_position["position_id"]}:manual_close:{request["close_id"]}'
        idem = dec.make_idempotency_key(
            account_id=account_id, symbol=symbol, strategy_id="candidate_c",
            key_id=key_id, decision_timestamp=requested_at_ms, config_hash=config_hash,
        )
        intent = dec.Intent(
            kind=dec.INTENT_EXIT,
            account_id=account_id,
            symbol=symbol,
            strategy_id="candidate_c",
            setup_id=None,
            position_epoch=current_position["position_id"],
            config_version_id=config_version_id,
            config_hash=config_hash,
            decision_timestamp=requested_at_ms,
            source_candle_close_timestamp=requested_at_ms,
            side=current_position["side"],
            idempotency_key=idem,
            reason_code="manual_close_15m",
            input_snapshot_hash=dec.sha256_of({
                "manual_close_id": request["close_id"],
                "position_epoch": current_position["position_id"],
                "side": current_position["side"],
            }),
            strategy_policy=strategy_policy,
        )
        epoch = state.epoch_store.get(current_position["position_id"])
        current_protection = {
            "algo_ids": list(epoch.protective_algo_ids),
            "algo_id": (epoch.protective_algo_ids or [None])[0],
            "sl_price": epoch.current_stop_price,
            "tp_price": epoch.target_price,
        }
        machine.request_exit()
        state.reversal_store.persist(symbol, now_ms)
        candidate_manual_close.patch(
            cfg.user_dir, symbol, request["close_id"],
            status="submitting", last_result="exit_intent_created", error=None,
        )
        prepared = {
            "branch": "managed", "intent": intent, "snapshot": {},
            "decision_time_ms": now_ms, "machine": machine,
            "current_position": current_position,
            "current_protection": current_protection,
        }

    try:
        result = live.execute_intent(
            cfg, client, intent, snapshot={}, equity=0.0,
            is_still_valid_fn=lambda: True,
            current_position=current_position,
            current_protection=current_protection,
            ledger=state.intent_ledger,
            epoch_store=state.epoch_store,
            strategy_policy=strategy_policy,
        )
    except Exception as exc:
        with ownership.account_order_lock(cfg.user_dir):
            machine.enter_safe_halt()
            state.reversal_store.persist(symbol, now_ms)
        candidate_manual_close.patch(
            cfg.user_dir, symbol, request["close_id"],
            status="pending", last_result="execute_exception", error=str(exc)[:500],
        )
        return {
            "intent_kind": dec.INTENT_EXIT, "executed": False,
            "pending": True, "critical": True,
            "reason": "manual_close_execute_exception",
        }

    with ownership.account_order_lock(cfg.user_dir):
        final = _finalize_steady_state_result_locked(
            cfg, symbol, state=state, result=result, prepared=prepared,
        )
        if final.get("flat_confirmed"):
            confirmed = candidate_manual_close.confirm(
                cfg.user_dir, symbol, request["close_id"],
                cooldown_seconds=cooldown_seconds, now=now_ms / 1000.0,
            )
            return {**final, "manual_close": confirmed}

        if final.get("critical") or final.get("pending"):
            candidate_manual_close.patch(
                cfg.user_dir, symbol, request["close_id"],
                status="pending",
                last_result=final.get("reason") or final.get("gate_result") or "pending",
                error=str(final.get("error") or final.get("error_reason") or "")[:500] or None,
            )
            return final

        if machine.state == rsm.State.EXIT_PENDING:
            machine.observe_exit_outcome(filled=False, unknown=False)
            state.reversal_store.persist(symbol, now_ms)
        failed = candidate_manual_close.fail(
            cfg.user_dir, symbol, request["close_id"],
            final.get("reason") or final.get("gate_result") or "definite_rejection",
            now=now_ms / 1000.0,
        )
        return {**final, "manual_close": failed}


def run_steady_state_cycle(cfg, client, symbol: str, **kwargs) -> dict:
    """[2026-09-16, 사용자 직접 지시 - Item B 발견 결함 수정] account_order_lock을
    이 함수 전체(GPT 왕복 포함)에 걸쳐 쥐지 않는다 - 실제 스레드 테스트로
    확인된 문제(tests/test_candidate_c_gpt_delayed_response_safety.py의
    test_new_entry_gpt_wait_delays_other_symbols_exit_via_shared_account_lock)는
    "한 심볼의 신규진입 GPT 대기가 같은 계좌 공통락을 필요로 하는 다른
    심볼/CORE의 청산·보호 처리까지 지연시킨다"였다. live.execute_intent()/
    _execute_entry() 자신은 이미 올바른 구조(GPT 게이트는 락 밖, 최종 제출
    직전에만 락을 다시 잡고 모든 상태를 재확인)를 갖추고 있었다
    (candidate_c_hybrid_live_adapter.py의 gate 호출 vs 이후 account_order_lock
    진입 지점 참고) - 이 함수가 그 구조 위에 통째로 락을 한 번 더 씌워서
    무력화하고 있었을 뿐이다. 아래 _prepare_steady_state_decision_locked()가
    "신호 계산까지"만 락 안에서 하고, live.execute_intent() 호출(GPT가 있다면
    여기서 일어남) 자체는 락 밖에서, 그 결과를 반영하는 마무리만 다시 락 안에서
    한다 - 계좌 공통 주문락 자체를 없애거나 CORE/Candidate C를 분리하지 않는다
    (동시진입 한도·중복주문 방어는 전부 기존 그대로 유지)."""
    try: entry_attempt_notifications.core_entry_events.kick(cfg)
    except Exception: pass
    return _run_steady_state_cycle_impl(cfg, client, symbol, **kwargs)


def _run_steady_state_cycle_impl(
    cfg, client, symbol: str, *, bars_4h: list, bars_1h: list, bars_5m: list,
    state: HybridEngineState, account_id: str, config_version_id: str, config_hash: str,
    risk_per_trade_pct: float, lot_step: float, min_size: float, strategy_policy: dict,
    open_position_count_fn=None, max_concurrent_positions: int | None = None,
    indicator_fn=None, indicator_ready=True, indicator_reason=None,
    stop_event=None,
) -> dict:
    """confirmed_bars_from_df()로 이미 만들어진 확정봉 리스트를 받아 한 사이클을
    처리한다(봉 조회 자체는 호출부 책임 - 이 함수는 순수하게 신호->실행만 맡는다).

    open_position_count_fn/max_concurrent_positions(2026-09-13, 사용자 지시 -
    Candidate C 동시포지션 상한 배선) - live.execute_intent()/_execute_entry()가
    이미 갖추고 있던 인터페이스(항목3, "멀티심볼 카운트를 계산해 넘겨주는
    오케스트레이션은 아직 없다"고 스스로 문서화돼 있었다)를 그대로 통과시킨다.
    DOGE/SOL처럼 여러 심볼이 동시에 진입 후보여도 CANDIDATE_C_MAX_CONCURRENT_
    POSITIONS(기본 1)을 넘지 않도록, 호출부(trader.py의 Candidate C 심볼 루프)가
    candidate_c_hybrid_ownership.count_candidate_c_open_or_pending_positions()로
    이 콜백을 채워 넘긴다. 이 확인 자체는 _execute_entry()가 실제 주문 직전에
    수행하므로(계좌 공통락 안, GPT 게이트 통과 이후) - 여기서 별도로 다시 락을
    걸 필요가 없다. 둘 다 None이면(단일 심볼 테스트 등) 기존 동작과 완전히
    동일하게 아무 제한 없이 통과한다."""
    if not bars_5m:
        return {"executed": False, "skipped": True, "reason": "no_confirmed_5m_bars"}

    with ownership.account_order_lock(cfg.user_dir):
        prepared = _prepare_steady_state_decision_locked(
            cfg, client, symbol, bars_4h=bars_4h, bars_1h=bars_1h, bars_5m=bars_5m,
            state=state, account_id=account_id, config_version_id=config_version_id,
            config_hash=config_hash, risk_per_trade_pct=risk_per_trade_pct,
            lot_step=lot_step, min_size=min_size, strategy_policy=strategy_policy,
            indicator_fn=indicator_fn, indicator_ready=indicator_ready,
            indicator_reason=indicator_reason,
            open_position_count_fn=open_position_count_fn,
            max_concurrent_positions=max_concurrent_positions,
        )
    if "result" in prepared:
        result=prepared['result']
        entry_attempt_notifications.record_candidate_block(cfg,dict(result,symbol=symbol),result)
        return result

    intent, snapshot = prepared["intent"], prepared["snapshot"]
    if prepared["branch"] in ("entry_shadow", "entry_live"):
        is_still_valid_fn = _build_entry_still_valid_fn(client, intent, snapshot)
        if prepared["branch"] == "entry_shadow":
            # 항목6(2026-09-13)의 Shadow 관찰 목적 그대로 - CANDIDATE_C_LIVE_EXECUTE가
            # 꺼져 있어도 GPT 게이트까지는 실제로 호출된다(_execute_entry 자체
            # 계약, 여기서 새로 만들지 않음). 그 GPT 왕복 동안에도 이제 계좌
            # 공통락을 쥐지 않는다.
            shadow_result = live.execute_intent(
                cfg, client, intent, snapshot=snapshot, equity=client.fetch_usdt_equity(),
                is_still_valid_fn=is_still_valid_fn,
                ledger=state.intent_ledger, epoch_store=state.epoch_store,
                strategy_policy=strategy_policy, stop_event=stop_event,
            )
            # 이 분기에 왔다는 것 자체가 CANDIDATE_C_LIVE_EXECUTE=False라는 뜻이다
            # (_prepare_steady_state_decision_locked가 이미 그렇게 나눴다) - 그래서
            # decide()에도 항상 state.shadow_setup_tracker가 넘겨졌었다(파일 상단
            # is_live 삼항식). arm도 같은 인스턴스에 해야 다음 사이클의 조회와
            # 일치한다.
            with ownership.account_order_lock(cfg.user_dir):
                _maybe_arm_timeout_retry(
                    cfg, state.shadow_setup_tracker, intent, shadow_result,
                    prepared.get("source_bar_10m_open_time_ms"),
                )
            return shadow_result
        result = live.execute_intent(
            cfg, client, intent, snapshot=snapshot, equity=client.fetch_usdt_equity(),
            is_still_valid_fn=is_still_valid_fn,
            open_position_count_fn=open_position_count_fn, max_concurrent_positions=max_concurrent_positions,
            ledger=state.intent_ledger, epoch_store=state.epoch_store,
            strategy_policy=strategy_policy, stop_event=stop_event,
        )
    else:  # managed: StopUpdate/Exit/Reversal/Reduce - GPT를 호출하지 않는 경로
        result = live.execute_intent(
            cfg, client, intent, snapshot=snapshot, equity=client.fetch_usdt_equity(),
            is_still_valid_fn=lambda: True, current_position=prepared["current_position"],
            current_protection=prepared["current_protection"],
            ledger=state.intent_ledger, epoch_store=state.epoch_store,
            strategy_policy=strategy_policy,
        )

    with ownership.account_order_lock(cfg.user_dir):
        return _finalize_steady_state_result_locked(cfg, symbol, state=state, result=result, prepared=prepared)



def _adaptive_decision_context_kwargs(cfg):
    order_mode = str(getattr(cfg, "CANDIDATE_C_ORDER_MODE", "") or "").upper()
    if order_mode not in {"AUTO_ALL", "FIXED_MARGIN_AUTO_EXIT", "MANUAL_ALL"}:
        order_mode = "FIXED_MARGIN_AUTO_EXIT" if str(getattr(cfg, "CANDIDATE_C_SIZING_MODE", "FIXED_MARGIN") or "FIXED_MARGIN").upper() == "FIXED_MARGIN" else "AUTO_ALL"
    adaptive_mode = "OFF" if order_mode == "MANUAL_ALL" else str(getattr(cfg, "ADAPTIVE_EXIT_MODE", "OFF") or "OFF").upper()
    return {
        "sizing_mode": "VARIABLE_RISK" if order_mode == "AUTO_ALL" else "FIXED_MARGIN",
        "adaptive_exit_mode": adaptive_mode,
        "adaptive_exit_policy": production_adaptive_exit_policy(),
        "adaptive_audit_user_dir": getattr(cfg, "user_dir", None),
        "adaptive_fixed_margin_usdt": float(getattr(cfg, "CANDIDATE_C_FIXED_MARGIN_USDT", 0.0) or 0.0),
        "adaptive_leverage": float(getattr(cfg, "CANDIDATE_C_LEVERAGE", getattr(cfg, "LEVERAGE", 1.0)) or 1.0),
        "adaptive_order_cap_notional": float(getattr(cfg, "CANDIDATE_C_MAX_ORDER_NOTIONAL_USDT", 0.0) or 0.0),
        "adaptive_approved_policy_hash": str(getattr(cfg, "ADAPTIVE_EXIT_APPROVED_POLICY_HASH", "") or ""),
    }

def _prepare_steady_state_decision_locked(
    cfg, client, symbol: str, *, bars_4h: list, bars_1h: list, bars_5m: list,
    state: HybridEngineState, account_id: str, config_version_id: str, config_hash: str,
    risk_per_trade_pct: float, lot_step: float, min_size: float, strategy_policy: dict,
    indicator_fn=None, indicator_ready=True, indicator_reason=None,
    open_position_count_fn=None, max_concurrent_positions: int | None = None,
) -> dict:
    """account_order_lock을 쥔 채로 "무엇을 할지"만 정한다(GPT 호출은 절대
    여기서 하지 않는다) - 반환값은 {"result": <최종 dict>}(더 볼 것 없이 바로
    반환) 또는 {"branch":, "intent":, "snapshot":, ...}(호출부가 락 밖에서
    live.execute_intent()를 부른 뒤 다시 락 안에서 마무리해야 함) 둘 중 하나다.
    아래 로직 자체는 기존 _run_steady_state_cycle_locked과 동일하다 - 락 경계만
    옮겼을 뿐, 신호/실행 판단 순서나 값은 전혀 바꾸지 않았다.

    open_position_count_fn/max_concurrent_positions을 이 락 안에서도 한 번 확인하는
    이유(2026-09-16, 실제 회귀 재현으로 발견) - machine.request_entry()(ENTRY_PENDING
    예약 표시)가 이 락 안에서 일어나는데, 그 확인이 GPT 이후(락 밖에서 대기한 뒤
    다시 잡는 락)로만 미뤄지면, 서로 다른 두 심볼이 "확인 전에 먼저 서로 예약부터
    마치는" 레이스가 생긴다(둘 다 상대방의 ENTRY_PENDING을 보고 동시에 막혀버림 -
    tests/test_candidate_c_count_cycle_integration.py::test_two_real_cycles_at_limit_one_submit_exactly_one_entry
    로 실제로 재현·고정됨). 예약 표시 "직전"에 여기서 먼저 확인해 두면 - 예약
    표시 자체가 계좌 공통락으로 직렬화돼 있으므로, 두 심볼이 동시에 통과할 수
    없다(하나가 반드시 먼저 예약하고, 다른 하나는 그 예약을 본다). execute_intent()
    안의 GPT 이후 재확인(락을 다시 잡은 뒥, 최신 상태로)은 그대로 유지한다 - GPT
    대기 도중 상태가 바뀌는 것까지 잡아야 하므로 이 이른 확인이 그 재확인을
    대신하지 않는다(중복 확인, 의도적)."""
    as_of_ms = bars_5m[-1]["open_time_ms"]
    is_live = getattr(cfg, "CANDIDATE_C_LIVE_EXECUTE", False)
    setup_tracker = state.setup_tracker if is_live else state.shadow_setup_tracker

    machine = state.reversal_store.get(symbol)
    observation = cycle_recon.reconcile_managed_position(
        cfg, client, symbol, state, bars_5m[-1]["close_time_ms"],
    )
    if observation.get("critical"):
        if machine.state == rsm.State.SAFE_HALT:
            return {"result": _handle_safe_halt_cycle(cfg, client, symbol, machine, state)}
        if getattr(cfg, "CANDIDATE_C_LIVE_EXECUTE", False):
            machine.enter_safe_halt()
            state.reversal_store.persist(symbol, bars_5m[-1]["close_time_ms"])
        if cfg.logger is not None:
            cfg.logger.critical("[%s] Candidate C reconciliation: %s", symbol, observation["reason"])
        return {"result": {"executed": False, "intent_kind": "RECONCILE", **observation}}
    if machine.state == rsm.State.SAFE_HALT:
        # 항목3(2026-09-13) - decide()는 SAFE_HALT를 보면 곧바로 NO_ACTION을
        # 반환하므로(candidate_c_decision_engine.decide()), 여기서 부르지 않고
        # 매 사이클 능동적으로 포지션/보호주문을 재확인+재시도한다 - "신규
        # 진입만 차단"과 "위험 포지션 관찰/보호 시도 계속"은 서로 다른 것이다.
        return {"result": _handle_safe_halt_cycle(cfg, client, symbol, machine, state)}

    # Reconciliation / owned emergency protection continue while discretionary
    # entries, reductions, reversals and stop moves await reproducible indicators.
    if not indicator_ready:
        return {"result": {"executed": False, "intent_kind": "NoAction", "reason": "WARMING_UP",
                "indicator_reason": indicator_reason}}
    indicator_fn = indicator_fn or si.augment_with_indicators

    current_position = None
    if machine.state in (rsm.State.LONG, rsm.State.SHORT):
        current_position = _current_position_from_epoch(state)

    decision_time_ms = bars_5m[-1]["close_time_ms"]
    bars_4h_confirmed = [b for b in bars_4h if b.get("confirm") == 1 and b["close_time_ms"] <= decision_time_ms]
    bars_1h_confirmed = [b for b in bars_1h if b.get("confirm") == 1 and b["close_time_ms"] <= decision_time_ms]
    if is_live and current_position is not None:
        observed_epoch = state.epoch_store.get(current_position["position_id"])
        if (observed_epoch.last_action_tick_ms is not None
                and observed_epoch.last_action_tick_ms >= decision_time_ms):
            return {"result": {"executed": False, "skipped": True, "reason": "confirmed_action_tick_already_applied"}}
        old_water = observed_epoch.high_water or observed_epoch.raw_entry_price
        observed_epoch.high_water = (
            max(old_water, bars_5m[-1]["high"]) if observed_epoch.side == "long"
            else min(old_water, bars_5m[-1]["low"])
        )
        state.epoch_store.save(current_position["position_id"], observed_epoch)
        current_position = recon.build_candidate_c_position_state(observed_epoch)

    if is_live and machine.state == rsm.State.REVERSAL_WAIT_FLAT:
        try:
            reversal_snap = tfc.build_as_of_snapshot(
                as_of_ms, bars_4h=bars_4h_confirmed, bars_1h=bars_1h_confirmed,
                bars_1d=[], bars_5m_for_10m=bars_5m,
            )
            indicators_4h = indicator_fn(bars_4h_confirmed, donchian_n=20)[-1]
        except Exception:
            reversal_snap = None
        if reversal_snap is not None:
            machine.revalidate_and_request_reversal_entry(
                decision_time_ms,
                current_4h_direction=dec.direction_from_4h_indicators(
                    ema_20=indicators_4h["ema_20"], ema_50=indicators_4h["ema_50"], close=indicators_4h["close"],
                ), entry_conditions_still_met=tfc.donchian_setup_condition(reversal_snap, machine.pending_side),
            )
            state.reversal_store.persist(symbol, decision_time_ms)

    ctx = dec.DecisionContext(
        account_id=account_id, symbol=symbol, strategy_id="candidate_c",
        config_version_id=config_version_id, config_hash=config_hash,
        risk_per_trade_pct=risk_per_trade_pct, strategy_policy=strategy_policy,
        risk_adaptive_partials=(getattr(cfg, 'RISK_ADAPTIVE_PARTIAL_ENABLED', False)
                                and getattr(cfg, 'CANDIDATE_C_CHART_ONLY', False)),
        **_adaptive_decision_context_kwargs(cfg),
    )

    # 사고 재발 방지 수정(2026-09-13, CORE REDUCE_50 반복 사고와 동일한 패턴 발견) -
    # decide()는 매 사이클 weakening_now를 내부적으로 계산해 evaluate_1h_structural_
    # derisk()의 "상승 엣지(약화 아님->약화)에서만 발동" 판정에 쓰지만, 그 결과를
    # 호출부에 돌려주지 않는다(Intent에 그 필드가 없음). PositionEpochState에는
    # 이미 이 값을 위한 weakening_prev 필드가 있었지만(derisk_done/profit_lock_active와
    # 같은 자리) 실제로 읽거나 쓰는 코드가 어디에도 없어 항상 초기값 False로 고정돼
    # 있었다 - 결과적으로 "약화가 지속되는 한 매 사이클 반복 발동"이 되어 CORE의 옛
    # REDUCE_50과 같은 반복 감축 위험을 안고 있었다. 이 포지션의 epoch에서 직접
    # 읽고 쓴다 - 포지션(epoch)마다 자연스럽게 격리되고, 새 포지션은 항상 새 epoch을
    # 쓰므로 별도의 리셋 로직도 필요 없다.
    epoch_for_weakening = state.epoch_store.get(current_position["position_id"]) if current_position else None
    weakening_prev_for_position = epoch_for_weakening.weakening_prev if epoch_for_weakening else False

    intent = dec.decide(
        ctx, as_of_ms=as_of_ms,
        bars_4h_confirmed_up_to_asof=bars_4h_confirmed,
        bars_1h_confirmed_up_to_asof=bars_1h_confirmed,
        bars_1d_confirmed_up_to_asof=[],  # 사용자 지시 원본 계약대로 1D는 telemetry-only, 판단에 반영 안 함
        bars_5m_for_10m_up_to_asof=bars_5m,
        setup_tracker=setup_tracker, epoch_store=state.epoch_store,
        reversal_machine=machine, current_position=current_position,
        weakening_prev=weakening_prev_for_position, lot_step=lot_step, min_size=min_size,
        indicator_fn=indicator_fn,
    )

    if current_position is not None and bars_1h_confirmed:
        ema_20_1h = indicator_fn(bars_1h_confirmed, donchian_n=20)[-1]["ema_20"]
        weakening_now = dec.weakening_from_1h(
            side=current_position["side"], close_1h=bars_1h_confirmed[-1]["close"], ema_20_1h=ema_20_1h,
        )
        if getattr(cfg, "CANDIDATE_C_LIVE_EXECUTE", False):
            row4 = indicator_fn(bars_4h_confirmed, donchian_n=20)[-1]
            cem.observe_pilot_4h_confirmation(epoch_for_weakening,
                side=current_position["side"], direction_4h=dec.direction_from_4h_indicators(
                    ema_20=row4.get("ema_20"), ema_50=row4.get("ema_50"), close=row4.get("close")))
            epoch_for_weakening.weakening_prev = weakening_now
            if intent.reduction_policy_hash:
                epoch_for_weakening.reduction_policy_hash = intent.reduction_policy_hash
            state.epoch_store.save(current_position["position_id"], epoch_for_weakening)

    # decide()는 setup_tracker를 직접 갱신하지 않는다 - 포지션이 없을 때는 이번
    # tick의 실제 조건을 매번 관측해 둬야 다음 확정봉의 setup 중복 판정이 정확하다
    # (candidate_c_backtest_signal_adapter.py와 동일한, 이미 검증된 패턴).
    snap = None
    if current_position is None:
        try:
            snap = tfc.build_as_of_snapshot(
                as_of_ms, bars_4h=bars_4h, bars_1h=bars_1h, bars_1d=[], bars_5m_for_10m=bars_5m,
            )
        except Exception:
            snap = None
        if snap is not None:
            dec.observe_entry_setups(setup_tracker, symbol, snap,
                bars_4h=bars_4h_confirmed, bars_1h=bars_1h_confirmed,
                bars_5m=bars_5m, indicator_fn=indicator_fn)
    source_bar_10m_open_time_ms = snap.bar_10m_current["open_time_ms"] if snap is not None else None

    if intent.kind == dec.INTENT_NO_ACTION:
        _consume_overextension_no_action(
            cfg, symbol, setup_tracker, intent, source_bar_10m_open_time_ms,
        )
        return {"result": {"executed": False, "intent_kind": intent.kind, **_intent_observability_fields(intent)}}

    # 항목6(2026-09-13) - 이전에는 trend_direction/donchian_upper/donchian_lower가
    # 전부 하드코딩된 None이었다(GPT Entry Gate 프롬프트에 실제 판단 근거가 전혀
    # 안 보이는 상태로 넘어가고 있었음 - 별도 발견 사항). decide()가 이미 계산한
    # 것과 동일한 공개 함수(dec.compute_confirmed_4h_indicators/
    # direction_from_4h_indicators)로 실제 값을 채운다 - 새 계산 로직을 만들지
    # 않는다. donchian_upper/lower는 snap(위에서 계산됨)의 bars_10m_prior_20에서
    # donchian_setup_condition()과 동일한 방식으로 뽑는다.
    trend_direction = None
    atr_4h = None
    try:
        indicators_4h = indicator_fn(bars_4h_confirmed, donchian_n=20)[-1]
        atr_4h = indicators_4h.get("atr_14")
        trend_direction = dec.direction_from_4h_indicators(
            ema_20=indicators_4h["ema_20"], ema_50=indicators_4h["ema_50"], close=indicators_4h["close"],
        )
    except Exception:
        trend_direction = None
    donchian_upper = donchian_lower = None
    if snap is not None and snap.bars_10m_prior_20:
        donchian_upper = max(b["high"] for b in snap.bars_10m_prior_20)
        donchian_lower = min(b["low"] for b in snap.bars_10m_prior_20)

    snapshot = {
        "symbol": symbol, "tf_list": ["4h", "1h"], "position": current_position,
        "trend_direction": trend_direction, "donchian_upper": donchian_upper, "donchian_lower": donchian_lower,
        "current_price": bars_5m[-1]["close"], "atr_4h": atr_4h,
        "entry_guard_context": dict(
            bars_1h=[b for b in bars_1h_confirmed if b['close_time_ms'] <= intent.decision_timestamp],
            atr14_4h=atr_4h, as_of_ms=intent.decision_timestamp, indicator_fn=indicator_fn,
            bars_5m=[b for b in bars_5m if b.get('confirm') == 1
                     and b['close_time_ms'] <= intent.decision_timestamp]),
    }

    if intent.kind == dec.INTENT_ENTRY:
        if intent.reason_code == "timeout_retry_edge_triggered":
            # [2026-09-16, 사용자 직접 지시] 이 기회를 실제로 쓰기 *직전에*
            # 소비 처리한다(GPT를 부르기도 전, 아래 machine.state 확인보다도
            # 먼저) - 이후 어떤 이유로든(방어적 가드/admission/GPT wait/reject/
            # timeout) 승인에 이르지 못해도 이 재검토 봉의 기회 자체는 이미
            # 쓴 것으로 영구 확정한다. 재시작 중 크래시가 나도 다시 기회가
            # 생기지 않는다(fail-closed) - 최초 timeout과 동일한 원칙.
            setup_tracker.mark_timeout_retry_consumed(intent.setup_id)
            if not getattr(cfg, "CANDIDATE_C_GPT_ENTRY_GATE_ENABLED", True):
                # [2026-09-16, 사용자 직접 지시 - Candidate C 전용 GPT ON/OFF]
                # 재검토 기회는 GPT가 켜져 있을 때만 의미가 있는 개념이다 -
                # 지금 OFF라면 이 기회를 "그냥 규칙 기반으로 주문"으로
                # 바꿔치기하지 않는다(과거 wait/timeout setup을 되살리는
                # 것과 동일한 효과라 금지). 위에서 이미 소비 처리했으니
                # 다시 나타나지도 않는다 - 이번 사이클은 그냥 아무 것도
                # 안 한다.
                return {"result": {
                    "executed": False, "intent_kind": intent.kind,
                    "gate_result": "timeout_retry_skipped_gpt_disabled",
                    **_intent_observability_fields(intent),
                }}
        is_reversal_entry = machine.state == rsm.State.REVERSAL_ENTRY_PENDING
        if machine.state not in (rsm.State.FLAT, rsm.State.REVERSAL_ENTRY_PENDING):
            # 방어적 이중 확인(항목4) - 정상 경로에서는 decide()가 FLAT이 아니면
            # 절대 EntryIntent를 내지 않지만(candidate_c_decision_engine.decide()
            # 자체 가드), 단일 계층에만 의존하지 않기 위해 여기서도 한 번 더
            # 막는다 - 같은 신호가 재처리돼도(버그/레이스/재시작 등) 실주문이
            # 두 번 나가지 않는다는 것을 오케스트레이터 스스로 보장한다.
            return {"result": {
                "executed": False, "intent_kind": intent.kind,
                "reason": f"reversal_machine_not_flat:{machine.state.value}",
                **_intent_observability_fields(intent),
            }}
        if not getattr(cfg, "CANDIDATE_C_LIVE_EXECUTE", False):
            # GPT 게이트 호출(있다면 여기서 최대 15초+ 대기)은 호출부가 이 락 밖에서
            # 수행한다(2026-09-16 수정) - live.execute_intent() 인자만 준비해서 넘긴다.
            return {"branch": "entry_shadow", "intent": intent, "snapshot": snapshot,
                    "source_bar_10m_open_time_ms": source_bar_10m_open_time_ms}
        if open_position_count_fn is not None and max_concurrent_positions is not None:
            # 예약(ENTRY_PENDING) 표시 *직전*에 이 락 안에서 먼저 확인한다(2026-09-16,
            # 실제 회귀로 발견·수정) - 아래 machine.request_entry()가 이 락 안에서
            # 일어나므로, 확인도 같은 락 안에서 예약과 원자적으로 묶여야 두 심볼이
            # "서로의 예약을 보기 전에 먼저 둘 다 예약부터 마치는" 레이스가 없다.
            # live.execute_intent()/_execute_entry() 안의 GPT 이후 재확인(락 밖 대기
            # 후 다시 락을 잡고 최신 상태로)은 그대로 유지된다 - 이 이른 확인은 그
            # 재확인을 대신하지 않는다(대기 도중 상태가 바뀌는 것은 그쪽이 잡는다).
            try:
                current_count = open_position_count_fn()
            except Exception as exc:
                # _execute_entry()의 _pending() 계약과 동일한 모양(pending=True,
                # reason=gate_result 중복)을 맞춘다 - 이 경로가 그 함수를 대신 호출한
                # 자리이기 때문에 반환 스키마도 그대로 맞춰야 호출부/대시보드가 혼동하지
                # 않는다.
                return {"result": {
                    "intent_kind": intent.kind, "executed": False,
                    "gate_result": "blocked_position_count_unknown",
                    "reason": "blocked_position_count_unknown", "pending": True,
                    "error_reason": str(exc), **_intent_observability_fields(intent),
                }}
            if current_count >= max_concurrent_positions:
                return {"result": {
                    "intent_kind": intent.kind, "executed": False,
                    "gate_result": "blocked_max_concurrent_positions", "error_reason": None,
                    **_intent_observability_fields(intent),
                }}
        if not is_reversal_entry:
            machine.request_entry(intent.side)
        state.reversal_store.persist(symbol, decision_time_ms)
        return {
            "branch": "entry_live", "intent": intent, "snapshot": snapshot,
            "is_reversal_entry": is_reversal_entry, "decision_time_ms": decision_time_ms,
            "machine": machine, "source_bar_10m_open_time_ms": source_bar_10m_open_time_ms,
        }

    is_live = getattr(cfg, "CANDIDATE_C_LIVE_EXECUTE", False)
    if is_live and intent.kind in (dec.INTENT_EXIT, dec.INTENT_REVERSAL):
        if intent.kind == dec.INTENT_REVERSAL:
            machine.request_reversal(decision_time_ms, intent.target_side)
        else:
            machine.request_exit()
        state.reversal_store.persist(symbol, decision_time_ms)
    current_protection = (
        {"algo_ids": list(epoch_for_weakening.protective_algo_ids),
         "algo_id": (epoch_for_weakening.protective_algo_ids or [None])[0],
         "sl_price": epoch_for_weakening.current_stop_price,
         "tp_price": epoch_for_weakening.target_price}
        if epoch_for_weakening else None
    )
    return {
        "branch": "managed", "intent": intent, "snapshot": snapshot,
        "decision_time_ms": decision_time_ms, "machine": machine,
        "current_position": current_position, "current_protection": current_protection,
    }


def _managed_result_requires_safe_halt(result: dict) -> bool:
    """Only execution/exposure ambiguity requires SAFE_HALT; delayed accounting does not."""
    if result.get("accounting_reconciliation_pending") and not result.get("critical"):
        return False
    return bool(result.get("critical") or result.get("pending"))


def _finalize_steady_state_result_locked(cfg, symbol: str, *, state: HybridEngineState, result: dict, prepared: dict) -> dict:
    """account_order_lock을 다시 쥔 채로, live.execute_intent()가 락 밖에서 돌려준
    result를 반영한다 - 로직 자체는 기존과 동일하다(락 경계만 옮김)."""
    intent = prepared["intent"]
    decision_time_ms = prepared["decision_time_ms"]
    machine = prepared["machine"]

    if prepared["branch"] == "entry_live":
        is_reversal_entry = prepared["is_reversal_entry"]
        _maybe_arm_timeout_retry(
            cfg, state.setup_tracker, intent, result, prepared.get("source_bar_10m_open_time_ms"),
        )
        if result.get("critical") and cfg.logger is not None:
            # 항목1 - 보호주문 검증 실패(안전청산 성공/실패 모두 포함)는 실제
            # 체결/청산이 걸린 이상 조용히 넘어가면 안 된다.
            cfg.logger.error(
                "[%s] CRITICAL: 진입 실행 중 위험 상태(critical) 반환 - gate_result=%s error_reason=%s",
                symbol, result.get("gate_result"), result.get("error_reason"),
            )
        if result["executed"]:
            if is_reversal_entry:
                machine.confirm_reversal_entry_filled()
            else:
                machine.confirm_entry_filled()
            _safe_record_attempt_outcome(cfg, symbol, state.setup_tracker, intent.setup_id, "accepted")
        elif result.get("critical") or result.get("pending") or result.get("gate_result") in ("unknown_order_state", "critical_unprotected_position"):
            # 거래소 응답이 모호하거나(unknown_order_state) 보호주문 검증 실패 후
            # 안전청산 자체까지 실패해(critical_unprotected_position) 포지션이
            # 무보호로 남은 경우 - 절대 FLAT으로 되돌리지 않는다. SAFE_HALT가
            # request_entry() 자체를 막아 재주문을 원천 차단하고,
            # reconcile_authoritative_position이 다음 재시작/재조정 때 거래소
            # 실제 상태로 확정한다. setup_tracker에도 attempt_outcome을 기록하지
            # 않는다 - pending_setup_ids()가 이 setup_id를 계속 "재조정 필요"로
            # 잡아야 한다(여기서 "rejected"를 기록하면 이미 끝난 attempt처럼 보여
            # 재조정 대상에서 빠져버린다).
            if is_reversal_entry:
                machine.observe_reversal_entry_outcome(accepted=False, unknown=True)
            else:
                machine.observe_entry_outcome(accepted=False, unknown=True)
        else:
            # 확정적으로 주문이 안 나갔거나(게이트/손실가드 차단, 확정 미체결),
            # 실제 체결->안전청산까지 확정적으로 완결된 경우(protection_verification_
            # failed_closed - 포지션은 이미 거래소에서 확인상 flat)만 여기 온다.
            if is_reversal_entry:
                machine.observe_reversal_entry_outcome(accepted=False, unknown=False)
            else:
                machine.observe_entry_outcome(accepted=False, unknown=False)
            _safe_record_attempt_outcome(
                cfg, symbol, state.setup_tracker, intent.setup_id, f"rejected:{result.get('gate_result')}",
            )
        state.reversal_store.persist(symbol, decision_time_ms)
        return {**result, "intent_kind": intent.kind, **_intent_observability_fields(intent)}

    # branch == "managed" (StopUpdate/Exit/Reversal/Reduce - GPT를 호출하지 않음)
    is_live = getattr(cfg, "CANDIDATE_C_LIVE_EXECUTE", False)
    current_position = prepared["current_position"]
    if is_live:
        if result.get("flat_confirmed"):
            # Includes dust-safe ReduceIntent fills that fully close a
            # LONG/SHORT position without first passing EXIT_PENDING.
            machine.converge_authoritative_flat(decision_time_ms)
        elif _managed_result_requires_safe_halt(result):
            machine.enter_safe_halt()
        state.reversal_store.persist(symbol, decision_time_ms)
        if result.get("executed") and current_position is not None and not result.get("flat_confirmed"):
            completed_epoch = state.epoch_store.get(current_position["position_id"])
            completed_epoch.last_action_tick_ms = decision_time_ms
            state.epoch_store.save(current_position["position_id"], completed_epoch)
    if result.get("critical") and cfg.logger is not None:
        # 예: StopUpdate에서 cancel_protection은 성공했는데 attach_protection이
        # 실패해 포지션이 보호주문 없이 노출됨(항목4) - Candidate C에는 아직
        # CORE의 kill_switch에 해당하는 자동 안전청산/알림 경로가 없다(별도로
        # 설계해야 할 남은 갭). 지금은 최소한 큰 소리로 로그를 남겨 조용히
        # 묻히지 않게 한다.
        cfg.logger.error(
            "[%s] CRITICAL: %s 실행 중 위험 상태(critical) 반환 - reason=%s error=%s",
            symbol, intent.kind, result.get("reason"), result.get("error"),
        )
    return {**result, "intent_kind": intent.kind, **_intent_observability_fields(intent)}
