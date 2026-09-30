"""주문 체결 직후 보호주문(OCO) 검증 + UNKNOWN_ORDER_STATE 안전 복구
(2026-08-30, FAST 제거/CORE 통합 작업으로 fast_engine.py에서 이전).

이 코드는 원래 FAST 전용으로 작성됐지만 실제로는 심볼/엔진과 완전히 무관하다 -
OCO 매칭은 market_id/side/size/SL·TP 트리거가만 보고, 그마저도 production
CORE(BTC/ETH) 실제 live OCO로 이미 실측 검증된 적이 있다(아래 _match_oco_order
주석 참고). FAST를 삭제한다고 이 안전 계층까지 같이 지우면 CORE(이제 BTC/ETH/
XRP/PI 4종목)가 "주문 응답은 불확실한데 실제로는 체결됐을 수 있는" 상황에서
무방비 상태가 된다(사용자 지시 - UNKNOWN_ORDER_STATE/OCO 검증은 절대 삭제 금지,
공통 안전기능으로 유지).

CORE는 지금까지 이 보호 계층이 전혀 없었다(okx_client.create_position_with_sl_tp가
UnknownOrderStateError를 던져도 trader.py 어디서도 잡지 않았다) - FAST 제거를
계기로 CORE에도 처음 갖추는 것이지, 있던 걸 옮기기만 하는 게 아니다."""
import core_kill_switch
import telegram_notify
import trade_log
import web_push

# P0-4 micro-safety patch(원 출처 2026-08-27, fast_engine.py) - 퍼센트 기반
# 허용오차(가격 0.5%, 사이즈 5%)는 실제 SL/TP 거리 자체보다 크거나 비슷할 수
# 있어서 "다른(더 넓은) SL/TP를 가진 OCO"도 정상으로 오판할 여지가 있었다.
# 퍼센트 대신 거래소 precision(가격 tick/수량 lot step) 기준의 절대 허용오차로
# 비교한다 - 이게 "exchange rounding으로 생기는 정상적인 오차"의 실제 크기이기
# 때문이다.
PRICE_TICK_TOLERANCE_MULTIPLE = 2  # 트리거가는 최대 ±2틱까지 rounding 오차로 인정
SIZE_STEP_TOLERANCE_MULTIPLE = 1   # 수량은 최대 ±1 lot step까지 rounding 오차로 인정


def _within_tolerance(actual: float, expected: float, step: float | None, max_steps: int) -> bool:
    """step(tick/lot size)이 주어지면 그 배수 기준 절대 허용오차로, 정말 못 구하는
    경우(시장 메타데이터 이상)에만 아주 작은 float 오차(1e-8)로 fail-closed에 가깝게
    비교한다 - 퍼센트로 되돌아가지 않는다(그게 원래 문제였으므로)."""
    if step and step > 0:
        return abs(actual - expected) <= step * max_steps + 1e-12
    return abs(actual - expected) <= 1e-8


def _match_oco_order(order: dict, market_id: str, expected_close_side: str,
                      expected_contracts: float | None, expected_sl_price: float | None,
                      expected_tp_price: float | None, amount_step: float | None = None,
                      price_tick: float | None = None, *,
                      expected_algo_id: str | None = None,
                      expected_algo_cl_ord_id: str | None = None) -> bool:
    """이 OCO 주문이 "방금 낸 그 포지션"의 보호주문이 맞는지 정확히 확인한다.
    "pending OCO가 하나라도 있으면 성공" 수준으로는 다른(오래된/무관한) OCO가 우연히
    남아있어도 보호 성공으로 오판할 수 있어서, symbol/방향/수량/SL·TP 트리거가/
    live 상태까지 전부 맞아야만 매칭으로 인정한다. 수량/가격은 퍼센트가 아니라
    거래소 precision(lot step/price tick) 기준 절대 허용오차로 비교한다.

    expected_algo_id/expected_algo_cl_ord_id(R2, 2026-09-17, 외부 검토 R2 대응) -
    둘 중 하나라도 주어지면 "권한 확인"(phase A) 단계로 먼저 걸러낸다: 이
    algoId/algoClOrdId가 우리가 실제로 제출·기록한 값과 정확히 같지 않으면
    그 즉시 매칭 실패다 - 가격·방향·수량이 전부 같아 보여도 통과시키지 않는다
    (다른 누군가/다른 시스템이 우연히 같은 조건으로 걸어둔 보호주문을 "우리
    것"으로 채택하는 사고를 막기 위함). 둘 다 안 주면(예: 재시작 후 local
    record가 아예 없는 orphan 포지션 - 확인할 ID 자체가 없는 정당한 경우) 기존
    방식(가격/방향/수량만으로 best-effort 매칭)을 그대로 쓴다 - CORE 호출부는
    이 인자를 전혀 넘기지 않으므로 동작 변화가 없다."""
    if expected_algo_id is not None and order.get("algoId") != expected_algo_id:
        return False
    if expected_algo_cl_ord_id is not None and order.get("algoClOrdId") != expected_algo_cl_ord_id:
        return False
    if order.get("instId") != market_id:
        return False
    if order.get("side") != expected_close_side:
        return False
    if order.get("state") != "live":
        return False
    # production CORE(BTC/ETH) 실제 live OCO로 실측 확인(원 출처 2026-08-27, 읽기
    # 전용 조회) - reduceOnly는 "true"/"false" 문자열로 온다(bool이 아님). XRP/PI도
    # 동일한 OKX ordType=oco 엔드포인트를 쓰므로 필드 shape이 같다.
    if str(order.get("reduceOnly")).lower() != "true":
        return False

    if expected_contracts is not None and expected_contracts > 0:
        try:
            actual_sz = float(order.get("sz"))
        except (TypeError, ValueError):
            return False
        if not _within_tolerance(actual_sz, expected_contracts, amount_step, SIZE_STEP_TOLERANCE_MULTIPLE):
            return False

    if expected_sl_price is not None:
        try:
            actual_sl = float(order.get("slTriggerPx"))
        except (TypeError, ValueError):
            return False
        if not _within_tolerance(actual_sl, expected_sl_price, price_tick, PRICE_TICK_TOLERANCE_MULTIPLE):
            return False

    if expected_tp_price is not None:
        try:
            actual_tp = float(order.get("tpTriggerPx"))
        except (TypeError, ValueError):
            return False
        if not _within_tolerance(actual_tp, expected_tp_price, price_tick, PRICE_TICK_TOLERANCE_MULTIPLE):
            return False

    return True


def verify_protection(
    client, expected_side: str, expected_size: float,
    expected_sl_price: float | None = None, expected_tp_price: float | None = None,
    size_tolerance: float = 0.05, *,
    expected_algo_id: str | None = None,
    expected_algo_cl_ord_id: str | None = None,
) -> dict:
    """주문 체결 직후(또는 UNKNOWN_ORDER_STATE reconciliation 중) 포지션과 거래소측
    OCO(SL/TP)가 모두 실제로, 그리고 정확히 존재하는지 확인한다.

    expected_sl_price/expected_tp_price를 넘기면 그 값과 실제 algo 주문의
    slTriggerPx/tpTriggerPx가 (precision 허용오차 내에서) 일치하는 것까지 확인한다 -
    "다른 오래된 OCO가 존재한다"는 이유로 보호 성공 판정하지 않는다. 두 값을 안
    넘기면(예: startup reconciliation에서 local record가 없는 orphan 포지션) side/
    사이즈/live 상태만으로 best-effort 매칭한다 - 이 경우도 절대 "아무 OCO나 있으면
    성공"으로 판정하지 않고 최소한 방향/수량은 맞아야 한다.

    expected_algo_id/expected_algo_cl_ord_id(R2, 2026-09-17) - 우리가 이미 알고
    있는 정확한 algoId/algoClOrdId가 있으면 넘긴다(phase A: 관리 권한 확인).
    둘 다 없으면(정말 확인할 ID 자체가 없는 정당한 경우만) 기존 phase B(가격/
    방향/수량) 단독 매칭으로 fall back한다 - CORE는 이 인자를 넘기지 않으므로
    기존 동작 그대로다."""
    position = client.fetch_position()
    if position is None:
        return {"ok": False, "reason": "position_missing"}
    if position["side"] != expected_side:
        return {"ok": False, "reason": "side_mismatch", "actual_side": position["side"]}
    if expected_size > 0:
        diff_ratio = abs(position["contracts"] - expected_size) / expected_size
        if diff_ratio > size_tolerance:
            return {"ok": False, "reason": "size_mismatch", "actual_size": position["contracts"]}

    try:
        market = client.exchange.market(client.symbol)
        # F2(2026-09-16) - oco(SL+TP 결합)와 conditional(SL 단독, 예: Candidate C의
        # take_profit_price=None 진입) 양쪽 모두 조회한다. oco만 보면 TP 없이 SL만
        # 붙은 정상 보호주문을 계속 "없음"으로 오판한다 - _match_oco_order()는 이미
        # expected_tp_price=None일 때 TP 비교를 건너뛰도록 돼 있으므로(아래), 조회
        # 범위만 넓히면 된다.
        oco_orders = client.fetch_pending_protection_orders()
    except Exception:
        log = (client.cfg.logger if client.cfg else None) or _fallback_logger()
        log.exception(
            "[%s] OCO 보호주문 조회 실패 - 포지션 존재만으로는 부족하다고 보고 실패 처리", client.symbol,
        )
        return {"ok": False, "reason": "oco_lookup_failed"}

    if not oco_orders:
        return {"ok": False, "reason": "oco_missing"}

    precision = market.get("precision") or {}
    amount_step = precision.get("amount")
    price_tick = precision.get("price")

    expected_close_side = "sell" if expected_side == "long" else "buy"
    matches = [
        o for o in oco_orders
        if _match_oco_order(
            o, market["id"], expected_close_side, position["contracts"], expected_sl_price, expected_tp_price,
            amount_step=amount_step, price_tick=price_tick,
            expected_algo_id=expected_algo_id, expected_algo_cl_ord_id=expected_algo_cl_ord_id,
        )
    ]
    if not matches:
        return {"ok": False, "reason": "oco_mismatch", "candidates": len(oco_orders)}

    # matched_orders(원본 algo 주문 dict, slTriggerPx/tpTriggerPx 포함)도 함께
    # 반환한다 - 호출부가 "동일 주문으로 인정되는 허용오차 내 일치"와 "목표를
    # 실제로 달성했는지"를 서로 다른 기준으로 구분해야 하는 경우(예: Candidate C
    # no-op 판정)를 위함이다(2026-09-14, v9 재검토 지적). 기존 호출부는 이 키를
    # 안 쓰므로 동작이 바뀌지 않는다.
    return {"ok": True, "position": position, "oco_count": len(matches), "matched_orders": matches}


def _fallback_logger():
    import logging
    return logging.getLogger("trader.order_safety")


def notify_critical(cfg, symbol: str, reason: str) -> None:
    text = f"[KILL SWITCH]\n{symbol}\nreason={reason}"
    log = cfg.logger or _fallback_logger()
    try:
        telegram_notify.send(cfg, text)
    except Exception:
        log.warning("[%s] 텔레그램 critical 알림 실패", symbol, exc_info=True)
    try:
        web_push.send_push_notification(cfg, "치킨바나나랩 - KILL SWITCH", text)
    except Exception:
        log.warning("[%s] web_push critical 알림 실패", symbol, exc_info=True)


def handle_unknown_order_state(
    cfg, client, symbol: str, expected_side: str, expected_contracts: float,
    sl_price: float, tp_price: float, error_detail: str,
) -> dict:
    """create_position_with_sl_tp()가 UnknownOrderStateError를 던졌을 때만 호출한다
    (응답이 유실됐을 뿐 거래소에는 실제로 주문이 들어갔을 수 있음). 절대 "실패했으니
    다음 사이클에 재시도"로 넘어가지 않는다 - 즉시 신규 진입을 전부 동결(kill switch)
    하고 거래소 실제 상태를 조회해 4가지 케이스로 분기한다:

    A. 실제 포지션 없음 -> 안전 실패로 종료(다음 사이클부터 재시도 가능하려면
       operator가 kill switch를 확인 후 직접 해제해야 한다 - 자동 재개 안 함).
    B. 포지션 있음 + 우리가 낸 것과 정확히 일치하는 OCO 있음 -> trade_log에
       진입을 복구 기록해서 회계가 이 포지션을 놓치지 않게 하되, kill switch는
       그대로 켜둔다(주문이 애매했던 사건 자체는 operator가 검토해야 함).
    C. 포지션 있음 + OCO 없음/불일치 -> 즉시 안전청산 시도.
    D. 거래소 상태 자체를 확인할 수 없음(조회 실패) -> 아무것도 확정하지 않고
       kill switch만 유지 - 신규 주문은 계속 막힌 채로 operator 확인 대기."""
    logger = cfg.logger or _fallback_logger()
    logger.critical(
        "[%s] UNKNOWN_ORDER_STATE - 주문 응답 불확실(%s) - 신규 진입 동결 + 거래소 상태 확인",
        symbol, error_detail,
    )
    core_kill_switch.activate(cfg.user_dir, f"{symbol} UNKNOWN_ORDER_STATE: {error_detail}")
    notify_critical(cfg, symbol, f"UNKNOWN_ORDER_STATE: {error_detail}")

    try:
        position = client.fetch_position()
    except Exception as exc:
        logger.critical("[%s] UNKNOWN_ORDER_STATE reconciliation: 포지션 조회 실패 - 상태 확정 불가", symbol, exc_info=True)
        return {"case": "D", "detail": f"거래소 포지션 조회 실패: {exc}"}

    if position is None:
        logger.warning("[%s] UNKNOWN_ORDER_STATE reconciliation: 실제 포지션 없음 - 안전 실패로 종료", symbol)
        return {"case": "A", "detail": "실제 포지션 없음 - 주문이 체결되지 않았음이 확인됨"}

    protection = verify_protection(client, expected_side, expected_contracts, expected_sl_price=sl_price, expected_tp_price=tp_price)
    if protection["ok"]:
        try:
            trade_log.record_open(
                cfg.user_dir, symbol, expected_side, position.get("entry_price") or 0.0,
                position["contracts"], dry_run=False, sl_price=sl_price, tp_price=tp_price,
            )
        except Exception:
            logger.critical("[%s] UNKNOWN_ORDER_STATE reconciliation: 거래 기록 복구 실패", symbol, exc_info=True)
        logger.warning("[%s] UNKNOWN_ORDER_STATE reconciliation: 포지션+보호주문 정상 확인 - 거래 기록 복구, kill switch는 유지", symbol)
        return {"case": "B", "detail": "포지션과 보호주문이 정상 확인되어 거래 기록을 복구함"}

    logger.critical("[%s] UNKNOWN_ORDER_STATE reconciliation: 보호주문 비정상(%s) - 즉시 안전청산 시도", symbol, protection.get("reason"))
    try:
        client.close_position(position)
    except Exception:
        logger.critical("[%s] UNKNOWN_ORDER_STATE reconciliation: 안전청산 시도 자체가 실패 - 수동 확인 필요", symbol, exc_info=True)
    return {"case": "C", "detail": f"무보호 포지션 확인({protection.get('reason')}) - 안전청산 시도함"}


def handle_unknown_close_state(cfg, client, symbol: str, error_detail: str) -> dict:
    """client.close_position()이 UnknownOrderStateError를 던졌을 때만 호출한다
    (응답이 유실됐을 뿐 거래소에는 실제로 청산됐을 수 있음). 2026-08-31 실거래
    감사 - _execute_close()는 close_position() 호출을 완전히 무방비로(예외 처리
    없이) 실행하고 있었다. entry 쪽은 UnknownOrderStateError가 나면 즉시 신규
    진입을 동결하고 거래소 실제 상태를 확인하는데(handle_unknown_order_state),
    close 쪽엔 그 대응이 아예 없어서 응답 유실 시 로컬 trade_log에 기록이
    영원히 안 남을 위험(다음 사이클의 외부청산 감지가 결국 복구하긴 하지만 -
    run_cycle의 external_close_unknown 경로) - 그리고 무엇보다 "실제로는 아직
    안 닫혔는데 닫혔다고 착각"할 위험이 있었다. 절대 재청산을 자동 시도하지
    않는다(중복 청산 위험) - 신규 진입만 동결하고 실제 상태를 확인해 분기한다.

    A. 실제로 flat이 됨(청산 성공 확인) -> 로컬 trade_log에 놓친 거래 기록을
       호출부가 복구할 수 있도록 알려준다. kill switch는 그대로 켜둔다(응답이
       애매했던 사건 자체는 operator가 검토해야 함).
    B. 포지션이 그대로 남아있음(청산이 실제로는 안 됨) -> 재청산을 시도하지
       않는다 - 기존 진입 시점의 보호주문(OCO)은 청산 시도와 무관하게 그대로
       살아있을 가능성이 높으므로 무보호 상태가 아니다. 다음 사이클의 정상
       판단 흐름이 조건이 유지되면 다시 청산을 시도한다.
    C. 거래소 상태 자체를 확인할 수 없음(조회 실패) -> 아무것도 확정하지 않고
       kill switch만 유지."""
    logger = cfg.logger or _fallback_logger()
    logger.critical(
        "[%s] UNKNOWN_CLOSE_STATE - 청산 응답 불확실(%s) - 신규 진입 동결 + 거래소 상태 확인",
        symbol, error_detail,
    )
    core_kill_switch.activate(cfg.user_dir, f"{symbol} UNKNOWN_CLOSE_STATE: {error_detail}")
    notify_critical(cfg, symbol, f"UNKNOWN_CLOSE_STATE: {error_detail}")

    try:
        position_after = client.fetch_position()
    except Exception as exc:
        logger.critical("[%s] UNKNOWN_CLOSE_STATE reconciliation: 포지션 조회 실패 - 상태 확정 불가", symbol, exc_info=True)
        return {"case": "C", "detail": f"거래소 포지션 조회 실패: {exc}"}

    if position_after is None:
        logger.warning("[%s] UNKNOWN_CLOSE_STATE reconciliation: 실제로 flat 확인 - 놓친 청산 기록 복구 필요", symbol)
        return {"case": "A", "detail": "실제로 청산 완료됨이 확인됨 - 거래 기록 복구 필요"}

    logger.warning("[%s] UNKNOWN_CLOSE_STATE reconciliation: 포지션이 그대로 남아있음 - 재청산 시도 안 함(중복 방지)", symbol)
    return {"case": "B", "detail": "청산이 실제로 안 됐음이 확인됨 - 다음 사이클에서 정상 흐름으로 재시도"}
