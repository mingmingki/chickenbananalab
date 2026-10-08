"""Candidate C(DOGE/SOL) 실거래 배선(2026-09-13, 사용자 지시).

trader.py가 이미 완성된 candidate_c_hybrid_cycle.py 오케스트레이터(run_startup_
reconciliation/run_steady_state_cycle)를 실제로 호출하게 하는 어댑터 계층. CORE의
run_cycle()/죽은 FAST 루프를 재사용하지 않는다 - 독립된 심볼 루프를 새로 만들되,
두 엔진이 같은 계좌를 공유하는 지점(계좌 손실가드 상한, 계좌 공통 주문락, 심볼
소유권)만 명시적으로 연결한다.

이 파일 하나가 새로 담당하는 것:
1. build_symbol_snapshot() - 실제 OkxClient + 로컬 intent_ledger/epoch_store를
   대사해 candidate_c_position_reconciliation.SymbolSnapshot을 조립한다(읽기
   전용 - 아무 것도 취소/청산하지 않는다).
2. run_candidate_c_engine() - CORE_ENABLED_SYMBOLS와 CANDIDATE_C_SYMBOLS의 교집합을
   시작 시점에 검사하고, DOGE 같은 "의도된 전환 대상"이 아닌 예상 밖의 중복이면
   Candidate C 전체를 시작하지 않는다(fail-closed, CORE는 계속 정상 동작).
3. _candidate_c_symbol_loop() - 심볼별 독립 스레드. 시작 시 reconciliation ->
   확정 5분봉마다 신호 계산/실행. 같은 확정봉을 반복 처리하지 않고, 신호/포지션이
   없으면 GPT를 부르지 않는다(candidate_c_hybrid_cycle.py 자체가 이미 보장).
"""
import datetime
import copy
import logging
import threading
import time
import hashlib

import candidate_c_exit_management as cem
import candidate_c_forward_paper_trading
import candidate_c_hybrid_bars as hybrid_bars
import candidate_c_decision_engine as dec
import candidate_c_hybrid_cycle as cycle
import candidate_c_hybrid_ownership as ownership
import candidate_c_intent_ledger as il
import candidate_c_manual_close as candidate_manual_close
import candidate_c_manual_entry as candidate_manual_entry
import candidate_c_notification_delivery as notification_delivery
import candidate_c_position_reconciliation as recon
import candidate_c_reversal_state_machine as rsm
import candidate_c_setup_tracker as st
import candidate_c_strategy_policy as strategy_policy_contract
import candidate_c_runtime as runtime
import candidate_c_indicator_contract as indicator_contract
import candidate_c_timeframe_contract as tfc
import symbol_entry_control
import order_safety
import risk_manager
import telegram_notify

logger = logging.getLogger("trader.candidate_c")

# 확정 5분봉 도착을 감지하는 폴링 주기. 5분봉 자체는 최대 5분에 한 번만 바뀌므로
# 이보다 훨씬 촘촘히 돌 필요는 없지만, 너무 길면 확정 직후 반응이 늦어진다 -
# CORE의 POLL_INTERVAL_SECONDS(계정 설정)와 무관하게 Candidate C 자신의 확정봉
# 감지 주기는 고정값으로 둔다(사용자 지시 - "확정된 5m/1h/4h/1d 봉만 사용"과
# "같은 확정 5분봉을 반복 처리하지 않음"을 만족하려면 5분보다 촘촘한 폴링 +
# dedup이 필요하지, CORE처럼 검토주기=타임프레임 사다리 방식이 아니다).
CANDIDATE_C_POLL_INTERVAL_SECONDS = 30

_CANDIDATE_C_MONITOR_REASON_TEXT = {
    "no_setup": "Donchian setup 대기",
    "setup_bar_recheck": "조건 유지 · 새 확정 10분봉 재검토",
    "setup_stale_after_30m": "조건 30분 초과 · 새 setup 형성 대기",
    "setup_direction_mismatch_or_no_atr": "setup 발생 · 4H 방향 불일치 또는 ATR 확인 필요",
    "strategy_policy_missing_or_invalid": "전략 정책 확인 필요",
    "no_risk_per_trade_pct_configured": "위험 설정 확인 필요",
    "holding_no_change": "보유 포지션 관리 · 변경 없음",
    "safe_halt": "SAFE_HALT",
    "reversal_target_revalidation_failed": "반전 재검증 미충족",
    "WARMING_UP": "지표 준비 중",
    "entry_risk_strong_confirmation_required": "진입 대기 · 1H·5m 방향 정렬 확인 필요",
    "entry_risk_score_blocked": "위험 조건 중첩 · 신규진입 차단",
    "short_chase_30m_drop": "급락 추격 숏 차단 · 되돌림 확인 대기",
    "long_chase_30m_rise": "급등 추격 롱 차단 · 눌림 확인 대기",
    "recent_entry_data_unavailable": "단기 가격·변동성 데이터 확인 대기",
    "entry_late_exhaustion_no_pullback": "단기 움직임 과열 · 되돌림 확인 대기",
}


def _candidate_c_monitor_reason_text(reason_code):
    if not reason_code:
        return "정상 감시 · 진입조건 대기"
    text = _CANDIDATE_C_MONITOR_REASON_TEXT.get(str(reason_code))
    if text:
        return text
    code = str(reason_code)
    if "overextension" in code:
        return "진입조건 발생 · 추격진입 방지로 차단"
    if code.startswith("stale_or_missing_data:"):
        return "시장 데이터 확인 필요"
    if code.startswith("awaiting_pending_transition:"):
        return "주문/반전 상태 전이 완료 대기"
    return code.replace("_", " ")


def _candidate_c_monitor_telemetry(
    symbol, *, bars_4h, bars_1h, bars_5m, indicator_fn, result, blockers, live_execute,
):
    """관측 전용 진행상황. 이미 가져온 봉만 재사용하며 주문/판단/API 호출을 추가하지 않는다."""
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    safe_halt_monitor = (result.get("intent_kind") == "SAFE_HALT_MONITOR"
                         or result.get("reason") == "clear_requires_manual_release")
    effective_blockers = list(dict.fromkeys(blockers or []))
    if safe_halt_monitor and "safe_halt_manual_release_required" not in effective_blockers:
        effective_blockers.append("safe_halt_manual_release_required")
    base = {
        "at": now_iso, "mode": "LIVE" if live_execute else "SHADOW", "symbol": symbol,
        "reason_code": result.get("reason_code"),
        "reason_text": ("SAFE_HALT · 수동 해제 필요" if safe_halt_monitor else _candidate_c_monitor_reason_text(result.get("reason_code"))),
        "intent_kind": result.get("intent_kind"), "executed": bool(result.get("executed")),
        "blockers": effective_blockers,
    }
    if not bars_5m:
        return {**base, "status": "UNKNOWN", "stage_text": "확정 5분봉 없음"}

    last_5m = bars_5m[-1]
    base.update(
        status="BLOCKED" if safe_halt_monitor else "OK",
        last_closed_bar_ms=last_5m.get("close_time_ms"),
        next_5m_close_ms=(last_5m.get("close_time_ms") or 0) + 300_000,
        latest_5m_close=last_5m.get("close"),
    )
    try:
        snap = tfc.build_as_of_snapshot(
            last_5m["open_time_ms"],
            bars_4h=bars_4h,
            bars_1h=bars_1h,
            bars_1d=[],
            bars_5m_for_10m=bars_5m,
        )
        visible_4h = [b for b in bars_4h if b.get("close_time_ms", 0) <= last_5m["close_time_ms"]]
        indicators_4h = indicator_fn(visible_4h, donchian_n=20)[-1]
        direction = dec.direction_from_4h_indicators(
            ema_20=indicators_4h["ema_20"],
            ema_50=indicators_4h["ema_50"],
            close=indicators_4h["close"],
        )
        upper = max(b["high"] for b in snap.bars_10m_prior_20)
        lower = min(b["low"] for b in snap.bars_10m_prior_20)
        decision_price = snap.bar_10m_current["close"]

        target_side = None
        target_price = None
        setup_met = False
        distance_pct = None
        if direction == "LONG":
            target_side = "long"
            target_price = upper
            setup_met = tfc.donchian_setup_condition(snap, "long")
            if decision_price:
                distance_pct = (target_price / decision_price - 1.0) * 100.0
        elif direction == "SHORT":
            target_side = "short"
            target_price = lower
            setup_met = tfc.donchian_setup_condition(snap, "short")
            if decision_price:
                distance_pct = (decision_price - target_price) / decision_price * 100.0

        policy_wait = result.get("reason_code") in {
            "entry_risk_strong_confirmation_required", "entry_risk_score_blocked",
            "short_chase_30m_drop", "long_chase_30m_rise",
            "entry_late_exhaustion_no_pullback", "setup_stale_after_30m", "recent_entry_data_unavailable",
        }
        if policy_wait:
            base["blockers"].append(base["reason_text"])
        if policy_wait and not blockers:
            if result.get("reason_code") == "entry_risk_strong_confirmation_required":
                stage_text = ("1H·5m 하락 정렬 확인 대기" if direction == "SHORT"
                              else "1H·5m 상승 정렬 확인 대기")
            else:
                stage_text = base["reason_text"]
        elif base["blockers"]:
            stage_text = "신규진입 차단 · 상태 감시 계속"
        elif result.get("executed"):
            stage_text = "실주문 실행"
        elif result.get("intent_kind") == dec.INTENT_ENTRY:
            stage_text = "진입 후보 처리"
        elif direction not in ("LONG", "SHORT"):
            stage_text = "4H 방향 형성 대기"
        elif setup_met and result.get("reason_code") == "no_setup":
            stage_text = "Donchian 조건 유지 · 다음 확정 10분봉 재검토"
        elif setup_met:
            stage_text = "Donchian 돌파 조건 충족"
        else:
            stage_text = "Donchian 돌파 대기"

        base.update(
            direction_4h=direction,
            donchian_upper=upper,
            donchian_lower=lower,
            decision_price=decision_price,
            target_side=target_side,
            target_price=target_price,
            setup_met=setup_met,
            distance_pct=distance_pct,
            stage_text=stage_text,
        )
        return base
    except Exception as exc:
        return {
            **base,
            "status": "PARTIAL",
            "stage_text": "감시 중 · 진행률 계산 일부 확인 불가",
            "telemetry_error": type(exc).__name__,
        }


def _candidate_c_monitor_history(user_dir, symbol, monitor, limit=12):
    """최근 확정 5분봉 감시 이력만 보존한다. 같은 봉은 중복 저장하지 않는다."""
    previous = runtime.snapshot(user_dir, symbol).get("monitor_history") or []
    rows = [row for row in previous if isinstance(row, dict)]
    bar = monitor.get("last_closed_bar_ms")
    if bar is not None:
        rows = [row for row in rows if row.get("last_closed_bar_ms") != bar]
    rows.append(copy.deepcopy(monitor))
    return rows[-limit:]


def _log_candidate_c_monitor(logger_, symbol, monitor):
    direction = monitor.get("direction_4h") or "NONE"
    stage = monitor.get("stage_text") or monitor.get("reason_text") or "감시 중"
    price = monitor.get("decision_price")
    target = monitor.get("target_price")
    distance = monitor.get("distance_pct")
    blocked = "없음" if not monitor.get("blockers") else " · ".join(monitor["blockers"])
    logger_.info(
        "[%s] Candidate C %s 감시 · 5분 확정봉 · 4H=%s · %s · 판단가=%s · 진입선=%s · 거리=%s · 사유=%s · 차단=%s",
        symbol, monitor.get("mode", "UNKNOWN"), direction, stage,
        "-" if price is None else f"{price:.8g}",
        "-" if target is None else f"{target:.8g}",
        "-" if distance is None else f"{distance:.2f}%",
        monitor.get("reason_text") or "-",
        blocked,
    )


# 사용자 지시(2026-09-13) - DOGE는 CORE에서 Candidate C로 "의도적으로 전환 중인"
# 유일한 심볼이다(이번 세션에서 CORE에서 실제로 제거하지는 않음). 이 심볼이
# CORE_ENABLED_SYMBOLS와 CANDIDATE_C_SYMBOLS 양쪽에 동시에 있는 것은 misconfiguration이
# 아니라 예상된 전환기 상태이므로, 전체 교집합 검사에서 예외로 취급하고 대신
# 심볼별 게이트는 CORE의 실행 중 심볼과 현재 설정 양쪽에서 제외됐는지 확인한다.
# flat만으로 소유권이 이전되지는 않는다. 이 외의 심볼이 겹치면(예: SOL이
# 실수로 CORE ENABLED_SYMBOLS에도 들어간 경우) 진짜 설정 오류로 보고 Candidate C
# 전체를 시작하지 않는다.
EXPECTED_TRANSITION_SYMBOLS = frozenset({"DOGE/USDT:USDT"})


def _build_candidate_c_notification_store(cfg, symbol, ledger):
    logger_ = getattr(cfg, "logger", None) or logger
    try:
        store = notification_delivery.NotificationDeliveryStore(
            cfg.user_dir, symbol,
        )
        entry, ambiguous = ledger.find_protected_entry()
        suppressed = []
        if entry is not None and not ambiguous:
            suppressed.append((
                "entry", entry.intent_id,
                "preexisting_at_notification_rollout",
            ))
        store.initialize(suppressed=suppressed)
        return store
    except Exception:
        logger_.warning(
            "[%s] Candidate C 알림 상태 초기화 실패 - 매매는 계속",
            symbol, exc_info=True,
        )
        return None


def _dispatch_candidate_c_notifications(cfg, state, result=None) -> None:
    """Send only finalized lifecycle events, after the cycle releases order locks.

    Delivery is deliberately fail-open. Events are removed from the in-memory
    queue before I/O so a Telegram outage cannot create duplicate alerts.
    """
    events = list(getattr(state, "notification_events", ()))
    if hasattr(state, "notification_events"):
        state.notification_events.clear()
    direct_event = (result or {}).get("notification_event")
    if direct_event:
        events.append(direct_event)

    logger_ = getattr(cfg, "logger", None) or logger
    store = getattr(state, "notification_store", None)
    deliver_now = []
    if store is None:
        deliver_now = events
    else:
        for event in events:
            try:
                store.enqueue(event)
            except Exception:
                logger_.warning(
                    "[%s] Candidate C 알림 상태 저장 실패 - 즉시 전송으로 계속",
                    event.get("symbol"), exc_info=True,
                )
                deliver_now.append(event)
        try:
            deliver_now = store.claim_pending() + deliver_now
        except Exception:
            logger_.warning(
                "Candidate C 대기 알림 claim 실패 - 다음 사이클에서 재시도",
                exc_info=True,
            )

    seen = set()
    for event in deliver_now:
        identity = (
            event.get("event_type"), event.get("symbol"), event.get("intent_id"),
        )
        if identity in seen:
            continue
        seen.add(identity)
        try:
            telegram_notify.send(cfg, telegram_notify.format_candidate_c_event(event))
        except Exception:
            logger_.warning(
                "[%s] Candidate C 텔레그램 알림 전송 실패 - 매매 흐름은 계속",
                event.get("symbol"), exc_info=True,
            )


def install_shadow_transport_audit(client):
    """Observe this Candidate-only client; no global/CORE transport changes.

    Preserve failed GET exceptions as UNKNOWN upstream. A second boundary
    blocks a write even if a future execution path misses the LIVE gate.
    """
    fetch = getattr(getattr(client, 'exchange', None), 'fetch', None)
    if not callable(fetch):
        return  # Non-HTTP test/custom clients have no transport evidence.
    audit = {'reads': 0, 'mutation_attempts': 0, 'mutations_sent': 0}
    client.shadow_transport_audit = audit
    def readonly_fetch(url, method='GET', headers=None, body=None):
        if method.upper() != 'GET':
            audit['mutation_attempts'] += 1
            raise RuntimeError('shadow_exchange_mutation_blocked')
        audit['reads'] += 1
        return fetch(url, method, headers, body)
    client.exchange.fetch = readonly_fetch


def _strategy_policy(cfg) -> dict:
    return strategy_policy_contract.production_strategy_policy()


def _config_version_id(cfg) -> str:
    return getattr(cfg, "CANDIDATE_C_CONFIG_VERSION_ID", "v1")


def _config_hash(cfg, policy: dict) -> str:
    return strategy_policy_contract.policy_sha256(policy)


def build_symbol_snapshot(
    cfg, client, symbol: str, intent_ledger: "il.IntentLedger", epoch_store: "cem.PositionEpochStore",
) -> "recon.SymbolSnapshot":
    """읽기 전용 - 실제 거래소 포지션/미체결 주문/보호주문과 로컬 intent_ledger/
    epoch_store를 대사해 reconcile_symbol_at_startup()이 요구하는 스냅샷을 만든다.
    아무 것도 취소/청산하지 않는다(candidate_c_position_reconciliation.py 자체의
    설계 원칙 그대로)."""
    query_failed = False
    try:
        exchange_position = client.fetch_position()
    except Exception:
        exchange_position = None
        query_failed = True
        logger.warning("[%s] 포지션 조회 실패 - UNKNOWN, 신규진입 차단", symbol)
    try:
        exchange_open_orders = client.exchange.fetch_open_orders(symbol)
    except Exception:
        logger.warning("[%s] 미체결 주문 조회 실패 - UNKNOWN, 신규진입 차단", symbol)
        exchange_open_orders = None
        query_failed = True
    try:
        exchange_protective_algo_ids = client.fetch_pending_protection_algo_ids()
    except Exception:
        logger.warning("[%s] 보호주문 조회 실패 - UNKNOWN, 신규진입 차단", symbol)
        exchange_protective_algo_ids = None
        query_failed = True

    internal_state_ambiguous = (query_failed or exchange_open_orders is None
                                or exchange_protective_algo_ids is None
                                or intent_ledger.has_ambiguous_pending_intent())
    entry_record, ambiguous = intent_ledger.find_protected_entry()
    if ambiguous:
        internal_state_ambiguous = True

    internal_trade_log_record = None
    internal_risk_reservation = None
    if entry_record is not None:
        epoch = epoch_store.get(entry_record.intent_id)
        internal_trade_log_record = recon.build_internal_trade_log_record(entry_record, epoch)
        internal_risk_reservation = entry_record.reserved_risk_usdt

    return recon.SymbolSnapshot(
        symbol=symbol, exchange_position=exchange_position, exchange_open_orders=exchange_open_orders,
        exchange_protective_algo_ids=exchange_protective_algo_ids,
        internal_trade_log_record=internal_trade_log_record,
        internal_risk_reservation=internal_risk_reservation,
        internal_state_ambiguous=internal_state_ambiguous,
    )


def compute_candidate_c_symbols_to_run(cfg, core_active_symbols: list) -> list:
    """CORE가 실제로 지금 활성화한 심볼(cfg.ENABLED_SYMBOLS 교집합, 단순 static
    CORE_SYMBOLS 상수 전체가 아니다 - 사용자가 대시보드에서 DOGE를 먼저 꺼두면
    그 즉시 "충돌 없음"으로 인식돼야 한다)과 CANDIDATE_C_SYMBOLS의 교집합을 본다.
    EXPECTED_TRANSITION_SYMBOLS(DOGE)만 겹치면 Candidate C를 그대로 시작하되(해당
    심볼의 신규진입만 동적 게이트로 차단), 그 외 심볼이 겹치면 misconfiguration으로
    보고 Candidate C 전체를 시작하지 않는다(빈 리스트 반환) - CORE는 이 함수와
    무관하게 항상 그대로 동작한다."""
    candidate_c_symbols = list(getattr(cfg, "CANDIDATE_C_SYMBOLS", None) or [])
    if not candidate_c_symbols:
        return []
    overlap = set(core_active_symbols) & set(candidate_c_symbols)
    unexpected = overlap - EXPECTED_TRANSITION_SYMBOLS
    if unexpected:
        logger.critical(
            "Candidate C 시작 차단(fail-closed) - CORE 활성 심볼과 예상 밖 중복: %s "
            "(CORE는 영향받지 않고 정상 동작)", sorted(unexpected),
        )
        return []
    if overlap:
        logger.warning(
            "Candidate C: %s는 아직 CORE에도 활성화돼 있음 - 소유권 이전 전까지 "
            "해당 심볼의 신규진입만 차단하고 나머지 심볼은 정상 진행", sorted(overlap),
        )
    return candidate_c_symbols


def _confirmed_5m_open_time_ms(bars_5m: list) -> int | None:
    return bars_5m[-1]["open_time_ms"] if bars_5m else None


def observe_runtime_exchange(cfg, client, symbol, ledger, epochs, core_active_symbols=()):
    """Actual read-only observation. A missing/failed query is never flat proof."""
    snap = build_symbol_snapshot(cfg, client, symbol, ledger, epochs)
    result = cycle.run_startup_reconciliation(snap)
    unknown = snap.internal_state_ambiguous
    blockers = [result['reason']] if result.get('reason') else []
    core_owners = set(core_active_symbols) | set(getattr(cfg, 'ENABLED_SYMBOLS', ()))
    owner = 'core' if symbol in core_owners else 'UNKNOWN'
    protection = {'status': 'UNKNOWN', 'algo_ids': snap.exchange_protective_algo_ids}
    if symbol in core_owners:
        blockers.append('core_entry_owner_active')
    elif not unknown and snap.exchange_position is None and not snap.exchange_open_orders and not snap.exchange_protective_algo_ids:
        owner = 'candidate_c'
        protection['status'] = 'NOT_REQUIRED_FLAT'
    elif not unknown and snap.exchange_position:
        proof = ownership.validate_candidate_c_position_owner(client, symbol, ledger, epochs)
        if proof['allowed']:
            owner = 'candidate_c'
            record = proof['entry_record']
            epoch = epochs.get(record.intent_id)
            try:
                verified = order_safety.verify_protection(client, epoch.side, epoch.remaining_contracts,
                    expected_sl_price=epoch.current_stop_price, expected_tp_price=epoch.target_price)
                protection['status'] = 'VERIFIED' if verified['ok'] else 'UNVERIFIED'
                protection['reason'] = verified.get('reason')
                # sl_price/tp_price(2026-09-14 UI 정리 항목3) - 이미 위에서 verify_protection에
                # 넘긴 것과 동일한 epoch 값을 그대로 노출한다("확인된 SL/TP") - CORE처럼 고정
                # %를 역산하지 않는다(Candidate C는 ATR 기반이라 그 역산 자체가 성립하지 않음).
                protection['sl_price'] = epoch.current_stop_price
                protection['tp_price'] = epoch.target_price
                if not verified['ok']:
                    blockers.append('protection_' + str(verified.get('reason', 'UNKNOWN')))
            except Exception:
                blockers.append('protection_detail_UNKNOWN')
        else:
            blockers.append(proof['reason'])
    if symbol_entry_control.is_paused(cfg.user_dir, symbol):
        blockers.append('user_entry_pause')
    try:
        manual_block = candidate_manual_close.block_reason(
            candidate_manual_close.get(cfg.user_dir, symbol),
        )
    except Exception:
        manual_block = 'candidate_manual_close_state_UNKNOWN'
    if manual_block:
        blockers.append(manual_block)
    position = snap.exchange_position
    if position:
        # mark_price/unrealized_pnl/pnl_pct/leverage(2026-09-14 UI 정리 항목3) - 이미
        # client.fetch_position()이 거래소 응답에서 그대로 채워 반환하는 필드다
        # (okx_client.fetch_position() 참고, CORE의 pos.unrealized_pnl 등과 동일한
        # 출처/형식). 새로 조회하거나 계산하지 않고, 기존에 이미 가져왔다가 이
        # allowlist에서 조용히 버려지던 값을 그대로 통과시킨다 - 대시보드가 CORE와
        # 같은 방식으로 미실현손익/수익률/레버리지/현재가를 보여줄 수 있게 한다.
        position = {k: position.get(k) for k in (
            'side', 'contracts', 'entry_price', 'position_id', 'entry_timestamp_ms',
            'mark_price', 'unrealized_pnl', 'pnl_pct', 'leverage',
        )}
    sizing_observation={'configured_margin_usdt':getattr(cfg,'CANDIDATE_C_FIXED_MARGIN_USDT',None),
        'actual_margin_estimate_usdt':None,'sizing_reduction_reason':'historical_sizing_evidence_unavailable'}
    if position:
        try:
            meta=client.instrument_metadata()
            lev=float(position.get('leverage') or cfg.CANDIDATE_C_LEVERAGE)
            sizing_observation['actual_margin_estimate_usdt']=float(position['contracts'])*float(meta['contract_size'])*float(position['entry_price'])/lev
            sizing_observation['basis']='observed_contracts_times_contract_size_times_entry_price_divided_by_leverage'
        except (AttributeError,KeyError,TypeError,ValueError,ZeroDivisionError): pass
    return dict(owner=owner, actual_position=position,sizing_observation=sizing_observation,
                shadow_transport_audit=copy.deepcopy(getattr(client, 'shadow_transport_audit', None)),
                position_query_status='UNKNOWN' if unknown else 'KNOWN',
                pending_orders=None if snap.exchange_open_orders is None else len(snap.exchange_open_orders),
                unresolved_intents=len(ledger.pending_intents()), protection=protection,
                blockers=list(dict.fromkeys(blockers)))


def _candidate_c_symbol_loop(
    cfg, symbol: str, client, shared_setup_tracker: "st.SetupTracker",
    shared_epoch_store: "cem.PositionEpochStore", shared_reversal_store: "rsm.ReversalStateStore",
    clients_for_admission_check: dict, account_daily_loss_guard: "risk_manager.DailyLossGuard",
    account_id: str, stop_event: threading.Event, core_active_symbols=(),
) -> None:
    logger_ = cfg.logger or logger
    runtime.register(cfg.user_dir, symbol, threading.current_thread(), runtime.effective_settings(cfg))
    intent_ledger = il.IntentLedger.load(
        f"{cfg.user_dir}/candidate_c_intent_ledger_{symbol.replace('/', '_').replace(':', '_')}.jsonl",
        cfg.user_dir, symbol,
    )
    state = cycle.HybridEngineState(
        setup_tracker=shared_setup_tracker, epoch_store=shared_epoch_store,
        reversal_store=shared_reversal_store, intent_ledger=intent_ledger,
        notification_store=_build_candidate_c_notification_store(
            cfg, symbol, intent_ledger,
        ),
    )

    # 시작 시 reconciliation - 조금이라도 불명확하면 신규진입만 차단하고, 기존
    # 포지션/주문은 절대 건드리지 않는다(read-only 판정).
    snap = build_symbol_snapshot(cfg, client, symbol, intent_ledger, shared_epoch_store)
    startup_result = cycle.run_startup_reconciliation(snap)
    new_entries_allowed_from_startup = startup_result["new_entries_allowed"]
    logger_.info(
        "[%s] Candidate C 시작 시 reconciliation: status=%s reason=%s new_entries_allowed=%s",
        symbol, startup_result["status"], startup_result["reason"], new_entries_allowed_from_startup,
    )
    if not new_entries_allowed_from_startup:
        logger_.warning(
            "[%s] Candidate C reconciliation 결과 신규진입 차단 - 사람이 확인할 때까지 "
            "이 심볼은 관찰만 하고 진입하지 않는다(기존 포지션/주문은 그대로 둠)", symbol,
        )

    policy = _strategy_policy(cfg)
    config_version_id = _config_version_id(cfg)
    config_hash = _config_hash(cfg, policy)
    last_processed_5m_open_time_ms: int | None = None
    indicator_book = None
    indicator_path = f"{cfg.user_dir}/candidate_c_indicators_{hashlib.sha256(symbol.encode()).hexdigest()[:16]}.json"

    while not stop_event.is_set():
        try:
            # The running CORE threads retain their startup symbol list even if
            # a dashboard setting changes. Handoff therefore requires restart.
            core_entry_owners = set(core_active_symbols) | set(getattr(cfg, "ENABLED_SYMBOLS", ()) or ())
            transition_gate_ok = symbol not in core_entry_owners
            if symbol in EXPECTED_TRANSITION_SYMBOLS and transition_gate_ok:
                entry_record, ambiguous = intent_ledger.find_protected_entry()
                foreign = ({"reconciliation_required": False} if entry_record and not ambiguous else
                           ownership.check_foreign_position_before_candidate_c_takes_symbol(
                               client, symbol, core_active_symbols=core_entry_owners,
                           ))
                if foreign["reconciliation_required"]:
                    transition_gate_ok = False
                    logger_.warning(
                        "[%s] CORE->Candidate C 소유권 이전 대기 중 - reason=%s position=%s. "
                        "신규진입 차단", symbol, foreign.get("reason"), foreign["position"],
                    )

            # Explicit operator entry is consumed by the owning symbol loop;
            # the HTTP request only leaves a durable reservation. Never
            # rerun an ambiguous/executing request after a restart.
            if new_entries_allowed_from_startup and transition_gate_ok:
                manual_entry_result = candidate_manual_entry.process_request(
                    cfg, client, symbol, state=state, account_id=account_id,
                    config_version_id=config_version_id, config_hash=config_hash,
                    strategy_policy=policy, stop_event=stop_event,
                    clients_for_admission_check=clients_for_admission_check,
                )
                if manual_entry_result is not None:
                    _dispatch_candidate_c_notifications(cfg, state, manual_entry_result)
                    observation = observe_runtime_exchange(
                        cfg, client, symbol, intent_ledger, shared_epoch_store, core_active_symbols)
                    runtime.publish(cfg.user_dir, symbol, status='RUNNING',
                                    last_cycle_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                                    last_signal=dec.INTENT_ENTRY, **observation)
                    logger_.info("[%s] Candidate C operator manual entry processed: executed=%s pending=%s reason=%s",
                                 symbol, manual_entry_result.get('executed'),
                                 manual_entry_result.get('pending'), manual_entry_result.get('reason'))
                    stop_event.wait(1.0)
                    continue

            manual_result = cycle.run_manual_close_request(
                cfg, client, symbol, state=state, account_id=account_id,
                config_version_id=config_version_id, config_hash=config_hash,
                strategy_policy=policy,
            )
            if manual_result is not None:
                _dispatch_candidate_c_notifications(cfg, state, manual_result)
                observation = observe_runtime_exchange(
                    cfg, client, symbol, intent_ledger, shared_epoch_store, core_active_symbols,
                )
                now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
                runtime.publish(
                    cfg.user_dir, symbol, status='RUNNING', reason=None,
                    last_cycle_at=now_iso, last_signal=dec.INTENT_EXIT,
                    gpt_gate_reason=manual_result.get('reason'),
                    **observation,
                )
                logger_.info("[%s] Candidate C 수동청산 처리: %s", symbol, manual_result)
                stop_event.wait(1.0)
                continue

            try:
                manual_block = candidate_manual_close.block_reason(
                    candidate_manual_close.get(cfg.user_dir, symbol),
                )
            except Exception:
                manual_block = 'candidate_manual_close_state_UNKNOWN'

            entries_allowed = new_entries_allowed_from_startup and transition_gate_ok
            entries_allowed = entries_allowed and not symbol_entry_control.is_paused(cfg.user_dir, symbol)
            entries_allowed = entries_allowed and not manual_block

            equity = client.fetch_usdt_equity()
            loss_guard_allowed = account_daily_loss_guard.allow_new_entry(equity)
            if not loss_guard_allowed:
                entries_allowed = False
            admission_blockers = []
            if not new_entries_allowed_from_startup:
                admission_blockers.append('startup_reconciliation_manual_release_required')
            if not transition_gate_ok:
                admission_blockers.append('ownership_handoff_incomplete')
            if manual_block:
                admission_blockers.append(manual_block)
            if not loss_guard_allowed:
                admission_blockers.append('group_or_account_daily_loss_guard')
            observation = observe_runtime_exchange(cfg, client, symbol, intent_ledger, shared_epoch_store, core_active_symbols)
            observation['blockers'].extend(admission_blockers)
            runtime.publish(cfg.user_dir, symbol, status='RUNNING', reason=None, **observation)

            raw_4h = client.fetch_ohlcv_df("4h")
            raw_1h = client.fetch_ohlcv_df("1h")
            raw_5m = client.fetch_ohlcv_df("5m")
            bars_5m = hybrid_bars.confirmed_bars_from_df(raw_5m, "5m", symbol=symbol)
            bars_4h, bars_1h = [], []

            indicator_ready = False
            diagnostics = {'contract': indicator_contract.VERSION, 'timeframes': {}}
            try:
                bars_4h = hybrid_bars.confirmed_bars_from_df(raw_4h, "4h", symbol=symbol)
                bars_1h = hybrid_bars.confirmed_bars_from_df(raw_1h, "1h", symbol=symbol)
                if indicator_book is None:
                    indicator_book = indicator_contract.IndicatorBook(symbol, indicator_path)
                decision_close = bars_5m[-1]['close_time_ms'] if bars_5m else -1
                # Even a confirmed HTF response is invisible before the 5m
                # decision boundary. Never advance a checkpoint into its future.
                for timeframe, bars in (('4h', bars_4h), ('1h', bars_1h)):
                    if not bars:
                        raise indicator_contract.IndicatorUnavailable(f'{timeframe}_empty_response')
                    indicator_book.update(timeframe, [b for b in bars if b['close_time_ms'] <= decision_close])
                indicator_ready = indicator_book.ready_at(decision_close)
                diagnostics = indicator_book.diagnostics()
            except (ValueError, TypeError, KeyError, IndexError, OSError) as exc:
                diagnostics = indicator_book.diagnostics() if indicator_book else diagnostics
                reason = str(exc) if isinstance(exc, indicator_contract.IndicatorUnavailable) else type(exc).__name__
                diagnostics['reason'] = reason
                if indicator_book and reason in ('history_gap', 'confirmed_bar_revision'):
                    try:
                        indicator_book.quarantine(reason)
                    except OSError:
                        diagnostics['reason'] = 'indicator_quarantine_write_failed'
            if not indicator_ready:
                admission_blockers.append('indicator_warming_up:' + str(diagnostics.get('reason')))
                entries_allowed = False
            runtime.publish(cfg.user_dir, symbol, status='RUNNING', reason=None,
                            indicator_status='READY' if indicator_ready else 'WARMING_UP',
                            indicator_diagnostics=diagnostics,
                            blockers=list(dict.fromkeys(observation['blockers'] + admission_blockers)))

            current_5m_open_time_ms = _confirmed_5m_open_time_ms(bars_5m)
            if current_5m_open_time_ms is None or current_5m_open_time_ms == last_processed_5m_open_time_ms:
                # 아직 새로 확정된 5분봉이 없다 - 같은 봉을 반복 처리하지 않는다
                # (사용자 지시). 포지션 관리(스테이지/derisk)는 확정봉 단위로만
                # 일어나야 하므로 여기서 그냥 다음 폴링까지 대기한다.
                stop_event.wait(CANDIDATE_C_POLL_INTERVAL_SECONDS)
                continue

            if not entries_allowed:
                # entries_allowed=False(reconciliation/전환 대기/일일손실가드)면
                # 동시포지션 카운트를 부풀리지 않도록 항상 상한 초과로 취급해
                # execute_intent 쪽 게이트(gate_result=blocked_max_concurrent_positions)로
                # 일관되게 차단한다 - 별도의 새 gate_result를 만들지 않는다.
                open_position_count_fn = lambda: cfg.CANDIDATE_C_MAX_CONCURRENT_POSITIONS
            else:
                open_position_count_fn = lambda: ownership.count_candidate_c_open_or_pending_positions(
                    clients_for_admission_check, shared_reversal_store, current_symbol=symbol,
                    user_dir=cfg.user_dir,
                )

            result = cycle.run_steady_state_cycle(
                cfg, client, symbol, bars_4h=bars_4h, bars_1h=bars_1h, bars_5m=bars_5m,
                state=state, account_id=account_id, config_version_id=config_version_id,
                config_hash=config_hash, risk_per_trade_pct=cfg.CANDIDATE_C_RISK_PER_TRADE_PCT,
                lot_step=client.instrument_metadata()["lot_step"],
                min_size=client.instrument_metadata()["min_contracts"],
                strategy_policy=policy, open_position_count_fn=open_position_count_fn,
                max_concurrent_positions=cfg.CANDIDATE_C_MAX_CONCURRENT_POSITIONS,
                indicator_fn=indicator_book.indicators if indicator_book else None,
                indicator_ready=indicator_ready, indicator_reason=diagnostics.get('reason'),
                # 안전 항목A(2026-09-16) - 이 심볼 루프 자신의 stop_event를 그대로
                # 전달한다. GPT 대기 도중 중지가 요청돼도 이미 진행 중인 사이클
                # 자체는 구조적으로 못 끊지만, 실제 주문 제출 직전(계좌락 안)에서
                # 이 값을 다시 확인해 늦은 승인이 주문으로 나가지 않게 막는다.
                stop_event=stop_event,
            )
            _dispatch_candidate_c_notifications(cfg, state, result)
            last_processed_5m_open_time_ms = current_5m_open_time_ms
            try:
                # 전향 가상 체결 기록(2026-09-14, ChatGPT 검토 지적) - 실제 결정/실행
                # 경로와 완전히 분리된 순수 관찰 기능이다. 여기서 예외가 나도 실제
                # 사이클(위 result)에는 이미 영향을 줄 수 없다 - 그래서 실제
                # 사이클 로직 밖, 이 try/except 안에서만 호출한다.
                candidate_c_forward_paper_trading.update(
                    cfg, symbol, result, bars_5m, client.instrument_metadata()["contract_size"],
                    bars_4h=bars_4h, bars_1h=bars_1h, strategy_policy=policy,
                    # 2026-09-14 수정(ChatGPT v5 재검토 P1 지적) - 실제 사이클이 이번에
                    # 쓴 것과 동일한 연속 지표 checkpoint·준비 상태·상품 규칙을 그대로
                    # 넘긴다(가상 관리 판단이 실제와 다른 EMA/lot 규칙으로 갈라지지
                    # 않게 - 재현된 문제: 400봉 대 마지막 200봉만으로 계산한 같은
                    # 마지막봉의 EMA50이 실제로 다르고 방향까지 갈림).
                    indicator_fn=indicator_book.indicators if indicator_book else None,
                    indicator_ready=indicator_ready,
                    lot_step=client.instrument_metadata()["lot_step"],
                    min_size=client.instrument_metadata()["min_contracts"],
                )
            except Exception:
                logger_.exception("[%s] 전향 가상 체결 기록 중 오류 - 실제 사이클과 무관, 다음 폴링에 계속", symbol)
            observation = observe_runtime_exchange(cfg, client, symbol, intent_ledger, shared_epoch_store, core_active_symbols)
            observation['blockers'].extend(admission_blockers)
            now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
            # NoAction 사유 유실 수정(2026-09-15, 사용자 직접 지시) - 지금까지는
            # intent_kind=="NoAction"이면 decide()가 계산한 reason_code가 어디에도
            # 남지 않아(last_signal은 intent_kind만 담음), "왜 NoAction이었는지"를
            # 재구성할 방법이 전혀 없었다(실측 조사로 확인됨). last_evaluation은 매
            # 사이클 덮어써서 "가장 최근 평가"만 보여주고, last_entry_attempt는
            # decide()가 실제로 EntryIntent를 냈을 때만 새로 채운다(publish()는
            # dict.update 방식 병합이라, 이 키를 안 넘기면 다음 NoAction 사이클이
            # 직전 wait/timeout 이력을 지우지 않는다).
            last_evaluation = {
                "at": now_iso,
                "intent_kind": result.get('intent_kind'),
                "reason_code": result.get('reason_code'),
                "setup_id": result.get('setup_id'),
                "source_candle_close_timestamp": result.get('source_candle_close_timestamp'),
                "config_version_id": result.get('config_version_id'),
                "config_hash": result.get('config_hash'),
            }
            monitor = _candidate_c_monitor_telemetry(
                symbol,
                bars_4h=bars_4h, bars_1h=bars_1h, bars_5m=bars_5m,
                indicator_fn=indicator_book.indicators if indicator_book else None,
                result=result,
                blockers=list(dict.fromkeys(observation['blockers'] + admission_blockers)),
                live_execute=bool(getattr(cfg, 'CANDIDATE_C_LIVE_EXECUTE', False)),
            )
            monitor_history = _candidate_c_monitor_history(cfg.user_dir, symbol, monitor)
            publish_kwargs = dict(
                status='RUNNING', reason=None, last_cycle_at=now_iso,
                last_closed_bar=bars_5m[-1]['close_time_ms'], last_signal=result.get('intent_kind'),
                gpt_gate_reason=result.get('gate_result') or result.get('reason'),
                instrument_metadata=client.instrument_metadata(), last_evaluation=last_evaluation,
                monitor=monitor, monitor_history=monitor_history,
                **observation,
            )
            if result.get('intent_kind') == dec.INTENT_ENTRY:
                publish_kwargs['last_entry_attempt'] = {
                    "at": now_iso,
                    "setup_id": result.get('setup_id'),
                    "executed": bool(result.get('executed')),
                    "gate_result": result.get('gate_result'),
                    "error_reason": result.get('error_reason'),
                    "execution_evidence":result.get('execution_evidence'),
                    "configured_margin_usdt":getattr(cfg,'CANDIDATE_C_FIXED_MARGIN_USDT',None),
                }
            runtime.publish(cfg.user_dir, symbol, **publish_kwargs)
            # 새 확정 5분봉을 실제로 처리한 직후 딱 한 번만 진행상황을 남긴다.
            # 30초 polling heartbeat는 runtime에만 갱신하고 실행 로그는 도배하지 않는다.
            _log_candidate_c_monitor(logger_, symbol, monitor)
            if result.get("executed") or result.get("intent_kind") not in (None, "NoAction"):
                logger_.info("[%s] Candidate C 사이클 결과: %s", symbol, result)
        except Exception:
            runtime.publish(cfg.user_dir, symbol, status='ERROR', reason='cycle_error',
                            shadow_transport_audit=copy.deepcopy(getattr(client, 'shadow_transport_audit', None)),
                            position_query_status='UNKNOWN', protection={'status': 'UNKNOWN'},
                            blockers=['cycle_error_exchange_state_UNKNOWN'])
            logger_.exception("[%s] Candidate C 사이클 처리 중 예상치 못한 오류 - 다음 폴링에 계속", symbol)

        stop_event.wait(CANDIDATE_C_POLL_INTERVAL_SECONDS)

    runtime.publish(cfg.user_dir, symbol, status='STOPPED', reason='stop_event',
                    shadow_transport_audit=copy.deepcopy(getattr(client, 'shadow_transport_audit', None)))
    logger_.info("[%s] Candidate C 심볼 루프 종료(stop_event) - 신규진입만 중단, 기존 포지션 OCO는 그대로 유지", symbol)


def run_candidate_c_engine(cfg, state, core_active_symbols: list, account_id: str, stop_event: threading.Event) -> list:
    with ownership.account_order_lock(cfg.user_dir):
        return _prepare_candidate_c_engine(cfg, state, core_active_symbols, account_id, stop_event)


def _prepare_candidate_c_engine(cfg, state, core_active_symbols, account_id, stop_event):
    """CANDIDATE_C_ENABLED가 꺼져 있으면 아무 스레드도 만들지 않는다. 반환값은
    아직 시작되지 않은(daemon) 스레드 목록이다 - 호출부(trader.run_all)가 CORE
    스레드와 함께 균일하게 start()/join()한다(여기서 먼저 start()하면 중복 시작
    RuntimeError가 난다)."""
    if not getattr(cfg, "CANDIDATE_C_ENABLED", False):
        return []

    # Freeze settings for this run: .env reload cannot silently turn observation
    # into live execution or resize an in-flight position. This release has not
    # passed the VM/strategy acceptance gates, even if an old .env says LIVE.
    cfg = copy.copy(cfg)
    # 2026-09-15 수정(사용자 직접 지시) - live_activation_blockers()가 이제
    # user_dir을 넘기면 실제 사용자 승인 기록까지 확인한다. 여기서 user_dir을
    # 안 넘기면 항상 "확인 없음" 취급이라(fail-closed 기본값) 승인을 실제로
    # 받아도 이 안전판이 매번 다시 강제로 Shadow로 되돌리게 된다 - 그러면
    # /api/candidate_c_start가 이미 확인한 "진짜로 막힌 게 없다"는 판단을
    # 여기서 조용히 뒤집는 두 번째 버그가 생긴다. cfg.user_dir(계정 디렉터리)을
    # 그대로 넘겨 같은 판단 기준을 쓰게 한다.
    if getattr(cfg, 'CANDIDATE_C_LIVE_EXECUTE', False) and runtime.live_activation_blockers(cfg, user_dir=cfg.user_dir):
        (cfg.logger or logger).warning('Candidate C live activation blocked; effective runtime remains Shadow: %s',
                                      runtime.live_activation_blockers(cfg, user_dir=cfg.user_dir))
        cfg.CANDIDATE_C_LIVE_EXECUTE = False

    symbols_to_run = compute_candidate_c_symbols_to_run(cfg, core_active_symbols)
    if not symbols_to_run:
        return []

    # 2026-09-15(실계정에서 재현된 버그, 사용자 직접 보고) - 이 함수는 trader.run_all()
    # (CORE 시작)과 web_app.py의 /api/candidate_c_start(Candidate C 전용 시작) 양쪽에서
    # 호출된다. 후자는 자기 자신이 이미 thread_alive/start_reserved를 확인해 중복 시작을
    # 막지만(web_app.py), 이 함수 자체에는 그 보호장치가 없었다 - 그래서 사용자가
    # Candidate C를 직접 켠 직후 CORE를 켜면, run_all()이 이 함수를 또 호출했다. 정확한
    # 사고 경로를 실제 systemd 저널에서 확인함: 아래에서 만드는 스레드를 candidate_c_
    # runtime.register()에 등록하려 할 때, 그 함수 자신의 기존 안전장치(이미 살아있는
    # 스레드가 등록돼 있으면 RuntimeError('candidate_c_loop_already_running') 발생)가
    # 걸렸는데, 그 예외를 여기서도 run_all()에서도 아무도 안 잡아서 run_all() 스레드
    # 전체가 그 자리에서 조용히 죽었다("Exception in thread Thread-N (run_all)") - CORE
    # 심볼 스레드가 단 하나도 시작되지 못했는데 대시보드는 state.running=True가 이미
    # 찍힌 뒤라 "실행 중"으로 잘못 표시되는 이중 사고였다(실계정에서 두 번 재현). 이미
    # thread_alive거나 start_reserved인 심볼은 register() 호출 자체에 이르기 전에 여기서
    # 제외한다 - 남는 심볼이 없으면 조용히 빈 리스트를 반환한다(CORE 시작 자체는 막지
    # 않음 - CANDIDATE_C_ENABLED가 꺼진 것과 동일하게 그냥 아무 것도 안 하고 넘어간다).
    already_running = {
        symbol for symbol in symbols_to_run
        if (lambda s: s.get('thread_alive') or s.get('start_reserved'))(runtime.snapshot(cfg.user_dir, symbol))
    }
    if already_running:
        (cfg.logger or logger).info(
            'Candidate C 중복 시작 방지 - 이미 실행 중: %s', sorted(already_running))
        symbols_to_run = [s for s in symbols_to_run if s not in already_running]
    if not symbols_to_run:
        return []

    import okx_client
    import pnl_store
    import trader

    shared_setup_tracker = st.SetupTracker.load(f"{cfg.user_dir}/candidate_c_setup_tracker.jsonl")
    shared_epoch_store = cem.PositionEpochStore.load(f"{cfg.user_dir}/candidate_c_epoch_store.jsonl")
    shared_reversal_store = rsm.ReversalStateStore.load(f"{cfg.user_dir}/candidate_c_reversal_store.jsonl")
    clients = {symbol: okx_client.OkxClient(symbol, cfg) for symbol in symbols_to_run}
    if not getattr(cfg, 'CANDIDATE_C_LIVE_EXECUTE', False):
        for client in clients.values():
            install_shadow_transport_audit(client)
    account_daily_loss_guard = risk_manager.DailyLossGuard(
        cfg, limit_attr="CANDIDATE_C_MAX_DAILY_LOSS_PCT", group="candidate_c",
    )

    (cfg.logger or logger).info(
        "Candidate C 시작 - SYMBOLS=%s LIVE_EXECUTE=%s max_concurrent=%s daily_loss_limit=%.1f%%",
        symbols_to_run, getattr(cfg, "CANDIDATE_C_LIVE_EXECUTE", False),
        cfg.CANDIDATE_C_MAX_CONCURRENT_POSITIONS, cfg.CANDIDATE_C_MAX_DAILY_LOSS_PCT,
    )

    threads = [
        threading.Thread(
            target=_candidate_c_symbol_loop,
            args=(
                cfg, symbol, client, shared_setup_tracker, shared_epoch_store, shared_reversal_store,
                clients, account_daily_loss_guard, account_id, stop_event, tuple(core_active_symbols),
            ),
            daemon=True,
        )
        for symbol, client in clients.items()
    ]
    for symbol, thread in zip(clients, threads):
        runtime.register(cfg.user_dir, symbol, thread, runtime.effective_settings(cfg))

    # 2026-09-15(사용자 직접 지시) - CORE는 trader.run_all()이 시작 시 baseline_equity를
    # 잡고 _live_display_refresh_loop를 띄워 대시보드 "계좌/총수익"이 자동 갱신됐지만,
    # Candidate C 전용 시작 경로(run_candidate_c_engine, /api/candidate_c_start)는 CORE
    # 없이도 독립 동작하도록 만들어지면서 정작 이 표시 갱신은 빠져 있었다 - CORE를 켠 적
    # 없이 Candidate C만 실거래로 시작하면 계좌/총수익이 영영 안 뜨는 버그(실제 라이브
    # 계정에서 발견). run_all()과 동일한 패턴을 그대로 재사용한다(매매 client와 분리된
    # 별도 client, 순수 표시 전용 - 매매 판단/주문에 전혀 관여하지 않음, trader.py의
    # _live_display_refresh_loop 자체 문서 참고). 이미 baseline이 파일에 있으면(이전에
    # CORE 등으로 설정된 적 있으면) 덮어쓰지 않고 그대로 쓴다 - 이번 프로세스의 메모리
    # state에만 아직 안 실려 있을 수 있어 매번 state.update는 한다.
    baseline = pnl_store.load_baseline(cfg.user_dir)
    if baseline is None:
        any_client = next(iter(clients.values()))
        baseline = any_client.fetch_usdt_equity()
        pnl_store.save_baseline(cfg.user_dir, baseline)
    state.update(baseline_equity=baseline)
    refresh_clients = {symbol: okx_client.OkxClient(symbol, cfg) for symbol in symbols_to_run}
    threads.append(threading.Thread(
        target=trader._live_display_refresh_loop, args=(cfg, state, refresh_clients, stop_event), daemon=True,
    ))
    return threads
