"""Durable Candidate C exchange execution. All mutations are live-gated and
account-locked; owned management uses confirmed fills and persisted recovery.
Only entry decisions call the GPT gate. Quantities in stores are contracts.
"""
import dataclasses
import copy
import datetime
import math
import os
import time

import ccxt

import candidate_c_decision_engine as dec
import candidate_c_gpt_gate_adapter as gga
import ai_exit_plan_audit
from adaptive_exit_engine import normalize_ai_price_plan
from adaptive_exit_policy import production_adaptive_exit_policy
import candidate_c_hybrid_ownership as ownership
import execution_units
import okx_client
import order_safety
import risk_manager
import trade_log
import candidate_c_intent_ledger as il
import candidate_c_manual_close as candidate_manual_close
import candidate_c_notification_delivery as notification_delivery
import candidate_c_exit_management as cem
import stop_contract

PROTECTION_VERIFY_RETRIES = 3  # CORE(_resolve_external_close_pnl)와 동일한 재시도
# 횟수 - OKX의 OCO/algo 주문 등록이 포지션 생성보다 살짝 늦게 반영되는 eventual
# consistency 때문에 즉시 오판하지 않기 위함(항목1, 2026-09-13). 무한 재시도는
# 아니다 - 끝까지 실패하면 진짜 실패로 확정한다.
PROTECTION_VERIFY_RETRY_DELAY_SECONDS = 1.0

# Candidate C production accounting contract; matches forward-paper/backtest assumptions.
CANDIDATE_C_TAKER_FEE_RATE = 0.0005
CANDIDATE_C_SPREAD_BPS = 3.0
CANDIDATE_C_SLIPPAGE_BPS = 3.0

def _entry_cost_metadata(client, order, position, record):
    fee_rate=CANDIDATE_C_TAKER_FEE_RATE
    amount_coin=float(position["contracts"])*float(record.contract_size)
    entry_fee=float(position["entry_price"])*amount_coin*fee_rate
    order_id=(order or {}).get("id") or getattr(record,"exchange_order_id",None)
    since_ms=position.get("entry_timestamp_ms")
    if order_id and since_ms:
        trades=client.fetch_trades_for_order(str(order_id),int(since_ms)-60000,limit=50)
        fees=[abs(float((t.get("fee") or {}).get("cost"))) for t in trades
              if (t.get("fee") or {}).get("cost") is not None]
        notionals=[abs(float(t.get("cost"))) for t in trades if t.get("cost") is not None]
        if fees:
            entry_fee=sum(fees)
            if sum(notionals)>0:
                fee_rate=entry_fee/sum(notionals)
    return {"entry_fee_usdt":entry_fee,"fee_rate":fee_rate,
            "spread_bps":CANDIDATE_C_SPREAD_BPS,"slippage_bps":CANDIDATE_C_SLIPPAGE_BPS}

def _scale_remaining_entry_fee(entry_fee_usdt,before_contracts,remaining_contracts):
    return entry_fee_usdt if before_contracts<=0 else entry_fee_usdt*(remaining_contracts/before_contracts)


def _apply_reduce_lifecycle_flags(epoch, reason_code):
    if reason_code in ("partial_take_profit_2r", "PARTIAL_TP_2R_DUST_SAFE_FULL_EXIT"):
        epoch.partial_take_profit_done = True
    else:
        epoch.derisk_done = True


def _persist_notification_event(cfg, event: dict) -> dict:
    """Persist after trading state commits; notification I/O stays fail-open."""
    try:
        store = notification_delivery.NotificationDeliveryStore(
            cfg.user_dir, event.get("symbol"),
        )
        store.enqueue(event)
    except Exception:
        logger_ = getattr(cfg, "logger", None)
        if logger_ is not None:
            logger_.warning(
                "[%s] Candidate C 알림 상태 저장 실패 - 매매 결과는 유지",
                event.get("symbol"), exc_info=True,
            )
    return event


def _candidate_manual_close_entry_block(cfg, symbol: str) -> str | None:
    try:
        return candidate_manual_close.block_reason(
            candidate_manual_close.get(cfg.user_dir, symbol),
        )
    except Exception:
        return "candidate_manual_close_state_UNKNOWN"


def _candidate_c_new_entry_allowed(cfg, equity: float, start_equity_override: float | None = None) -> bool:
    """candidate_c 그룹 손실가드(risk_manager.DailyLossGuard) - allow_new_entry() 내부에서
    계좌 전체 catastrophic 체크와 candidate_c 그룹 실현손익 체크를 모두 수행한다(이미
    tests/test_candidate_c_hybrid_daily_loss_guard.py로 그 구성 자체를 검증함)."""
    guard = risk_manager.DailyLossGuard(cfg, limit_attr="CANDIDATE_C_MAX_DAILY_LOSS_PCT", group="candidate_c")
    if start_equity_override is not None:
        # 테스트 전용 - 정식 공개 API(allow_new_entry)로 "오늘 시작 자산"을 먼저
        # 확정시킨 뒤(그 시점엔 손실이 없으니 항상 True), 실제 낮아진 equity로 다시
        # 확인한다 - DailyLossGuard의 private 구현에 의존하지 않는다.
        guard.allow_new_entry(start_equity_override)
    return guard.allow_new_entry(equity)


def _verify_protection_with_retry(client, side: str, amount_coin: float,
                                   sl_price: float | None, tp_price: float | None, *,
                                   expected_algo_id: str | None = None,
                                   expected_algo_cl_ord_id: str | None = None) -> dict:
    """항목1 - "일시적인 거래소 eventual consistency" 때문에 즉시 오판하지 않도록
    제한된 횟수만 재시도한다. 마지막 시도까지 실패하면 그 마지막 결과를 그대로
    반환한다(무한정 기다리지 않음 - 계속 확인 안 되면 진짜 문제로 취급).

    expected_algo_id/expected_algo_cl_ord_id(R2, 2026-09-17) - 알고 있으면
    그대로 order_safety.verify_protection()에 전달해 phase A(관리 권한) 확인을
    같이 받는다."""
    protection = None
    for attempt in range(PROTECTION_VERIFY_RETRIES):
        protection = order_safety.verify_protection(
            client, side, amount_coin, expected_sl_price=sl_price, expected_tp_price=tp_price,
            expected_algo_id=expected_algo_id, expected_algo_cl_ord_id=expected_algo_cl_ord_id,
        )
        if protection["ok"]:
            return protection
        if attempt < PROTECTION_VERIFY_RETRIES - 1:
            time.sleep(PROTECTION_VERIFY_RETRY_DELAY_SECONDS)
    return protection


def _extract_order_ids(order: dict | None) -> tuple[str | None, str | None]:
    """ccxt의 create_order 반환값에서 거래소 주문ID/clientOrderId를 뽑는다.
    OKX raw 응답의 clientOrderId 필드명은 clOrdId다(ccxt가 top-level로 통일해
    주지 않는 필드라 info에서 직접 찾아야 하는 경우가 있음) - 없으면 지어내지
    않고 None을 그대로 남긴다."""
    if order is None:
        return None, None
    order_id = order.get("id")
    client_order_id = order.get("clientOrderId") or (order.get("info") or {}).get("clOrdId")
    return order_id, client_order_id


def _candidate_c_order_mode(cfg) -> str:
    mode = str(getattr(cfg, "CANDIDATE_C_ORDER_MODE", "") or "").upper()
    if mode in {"AUTO_ALL", "FIXED_MARGIN_AUTO_EXIT", "MANUAL_ALL"}:
        return mode
    return "FIXED_MARGIN_AUTO_EXIT" if str(getattr(cfg, "CANDIDATE_C_SIZING_MODE", "FIXED_MARGIN") or "FIXED_MARGIN").upper() == "FIXED_MARGIN" else "AUTO_ALL"


def _candidate_c_entry_protection_prices(cfg, intent, fresh_price: float) -> tuple[float, float]:
    if _candidate_c_order_mode(cfg) != "MANUAL_ALL":
        return intent.raw_stop_price, intent.raw_target_price
    sl = float(getattr(cfg, "CANDIDATE_C_STOP_LOSS_PCT", 0.0) or 0.0) / 100.0
    tp = float(getattr(cfg, "CANDIDATE_C_TAKE_PROFIT_PCT", 0.0) or 0.0) / 100.0
    if sl <= 0 or tp <= 0:
        raise ValueError("invalid_candidate_manual_sl_tp")
    if intent.side == "long":
        return fresh_price * (1.0 - sl), fresh_price * (1.0 + tp)
    if intent.side == "short":
        return fresh_price * (1.0 + sl), fresh_price * (1.0 - tp)
    raise ValueError("invalid_candidate_side")



def _apply_verified_ai_exit_plan_to_intent(intent, gate: dict, *, entry_price: float, atr_4h: float | None):
    """Apply only GPT-verified AI prices that fit the same bounded ATR/R:R envelope as CORE."""
    raw = (gate or {}).get("ai_exit_plan")
    plan = normalize_ai_price_plan(raw)
    if plan is None:
        return intent, (gate or {}).get("ai_exit_source") or "ai_plan_missing"
    try:
        entry = float(entry_price)
        atr = float(atr_4h)
    except (TypeError, ValueError):
        return intent, "ai_missing_atr"
    if not math.isfinite(entry) or entry <= 0 or not math.isfinite(atr) or atr <= 0:
        return intent, "ai_missing_atr"
    policy = production_adaptive_exit_policy()
    stop = float(plan["stop_loss_price"])
    tp1 = float(plan["take_profit_1_price"])
    tp2 = plan.get("take_profit_2_price")
    side = getattr(intent, "side", None)
    if side == "long":
        if not (stop < entry < tp1) or (tp2 is not None and float(tp2) < tp1):
            return intent, "ai_price_direction_invalid"
    elif side == "short":
        if not (stop > entry > tp1) or (tp2 is not None and float(tp2) > tp1):
            return intent, "ai_price_direction_invalid"
    else:
        return intent, "ai_side_invalid"
    risk = abs(entry - stop)
    stop_atr = risk / atr
    if stop_atr < float(policy["initial_atr_min"]):
        return intent, "ai_stop_below_atr_min"
    if stop_atr > float(policy["initial_atr_max"]):
        return intent, "ai_stop_above_atr_max"
    rr1 = abs(tp1 - entry) / risk
    if not float(policy["tp1_r_min"]) <= rr1 <= float(policy["tp1_r_max"]):
        return intent, "ai_tp1_r_out_of_bounds"
    if rr1 < float(policy["min_post_cost_rr"]):
        return intent, "ai_post_cost_rr_below_minimum"
    if tp2 is not None:
        rr2 = abs(float(tp2) - entry) / risk
        if not float(policy["tp2_r_min"]) <= rr2 <= float(policy["tp2_r_max"]):
            return intent, "ai_tp2_r_out_of_bounds"
    final_target = float(tp2) if tp2 is not None else tp1
    if dataclasses.is_dataclass(intent):
        updated = dataclasses.replace(intent, raw_stop_price=stop, raw_target_price=final_target)
    else:
        updated = copy.copy(intent)
        updated.raw_stop_price = stop
        updated.raw_target_price = final_target
    return updated, "ai_exit_plan_applied"


def _calculate_candidate_c_entry_amount(cfg, client, intent, fresh_price: float, fresh_equity: float) -> dict:
    """항목1(2026-09-13) - 실제 주문 수량 계산. 이전에는 amount_coin이
    intent.requested_risk_pct(퍼센트 숫자, 예: 1.0)를 그대로 코인 수량으로 쓰는
    자리표시자였다 - 단위가 완전히 틀려서 어떤 실주문 경로에도 연결하면 안
    되는 상태였다.

    execution_units.calculate_candidate_entry_size()(이미 검증된 공용 계약,
    2026-09-01)를 그대로 재사용한다 - 새 공식을 만들지 않는다. 기존 계정은
    FIXED_MARGIN을 유지하고, CANDIDATE_C_SIZING_MODE=VARIABLE_RISK를 명시한
    계정만 계좌 equity와 실제 entry~stop 거리, CANDIDATE_C_RISK_PER_TRADE_PCT로
    수량을 계산한다. VARIABLE_RISK에서도 CANDIDATE_C_FIXED_MARGIN_USDT는 최대
    증거금 상한으로 유지하며 주문상한과 더 작은 notional을 적용한다.

    stop_risk_per_coin은 "지금 이 가격에서 stop까지의 거리"로 계산한다(신호
    발생 시점이 아니라 주문 직전 fresh_price 기준) - stop_price 자체는
    decide()가 ATR로 이미 확정한 intent.raw_stop_price를 그대로 쓴다(재계산
    안 함, 전략 판단을 여기서 새로 하지 않음).

    실패(설정 누락/가격·수량 비정상/최소수량 미달)는 절대 예외로 새지 않고
    fail-closed 결과 dict로만 반환한다 - 호출부가 반드시 "ok" 필드를 확인해야
    한다."""
    if fresh_price is None or not math.isfinite(fresh_price) or fresh_price <= 0:
        return {"ok": False, "reason": "invalid_price_for_sizing"}
    if fresh_equity is None or not math.isfinite(fresh_equity) or fresh_equity <= 0:
        # FIXED_MARGIN 모드 자체의 notional 계산식은 equity를 쓰지 않지만(증거금
        # 고정이라는 게 원래 그런 뜻), equity가 비정상(음수/0/NaN/inf)이라는 건
        # 계좌 상태 자체를 신뢰할 수 없다는 신호다 - 사이징 모드와 무관하게 항상
        # 막는다(정상 흐름에서는 이 값이 음수면 이미 앞단의 DailyLossGuard가 먼저
        # 막았을 것이지만, 이 함수 자체도 독립적으로 방어한다).
        return {"ok": False, "reason": "invalid_equity_for_sizing"}
    if intent.raw_stop_price is None:
        return {"ok": False, "reason": "missing_stop_price_for_sizing"}
    stop_risk_per_coin = abs(fresh_price - intent.raw_stop_price)
    if not math.isfinite(stop_risk_per_coin) or stop_risk_per_coin <= 0:
        return {"ok": False, "reason": "invalid_stop_risk_per_coin"}

    try:
        meta = client.instrument_metadata()
    except Exception as exc:
        return {"ok": False, "reason": f"instrument_metadata_failed:{exc}"}
    contract_size = meta.get("contract_size")
    lot_step = meta.get("lot_step")
    min_contracts = meta.get("min_contracts")
    if contract_size is None or lot_step is None or min_contracts is None:
        # 없는 값을 추측해서 채우지 않는다(execution_units.instrument_metadata()
        # 자신의 계약) - 정밀도 정보가 불완전하면 사이징 자체를 하지 않는다.
        return {"ok": False, "reason": "instrument_metadata_incomplete"}

    order_mode = _candidate_c_order_mode(cfg)
    sizing_mode = "VARIABLE_RISK" if order_mode == "AUTO_ALL" else "FIXED_MARGIN"
    leverage = cfg.CANDIDATE_C_LEVERAGE
    max_order_notional = cfg.CANDIDATE_C_MAX_ORDER_NOTIONAL_USDT
    if sizing_mode == "VARIABLE_RISK":
        risk_pct = getattr(cfg, "CANDIDATE_C_RISK_PER_TRADE_PCT", None)
        try:
            risk_pct = float(risk_pct)
            leverage_value = float(leverage)
            max_order_notional = min(float(max_order_notional), float(fresh_equity) * leverage_value)
        except (TypeError, ValueError):
            return {"ok": False, "reason": "invalid_variable_risk_config"}
        if (not math.isfinite(risk_pct) or risk_pct <= 0
                or not math.isfinite(max_order_notional) or max_order_notional <= 0):
            return {"ok": False, "reason": "invalid_variable_risk_config"}
        config_payload = {
            "sizing_mode": "VARIABLE_RISK",
            "leverage": leverage,
            "max_order_notional_usdt": max_order_notional,
            "risk_per_trade_pct": risk_pct,
        }
    elif sizing_mode == "FIXED_MARGIN":
        config_payload = {
            "sizing_mode": "FIXED_MARGIN",
            "leverage": leverage,
            "max_order_notional_usdt": max_order_notional,
            "fixed_margin_usdt": cfg.CANDIDATE_C_FIXED_MARGIN_USDT,
        }
    else:
        return {"ok": False, "reason": "unsupported_candidate_c_sizing_mode"}
    try:
        result = execution_units.calculate_candidate_entry_size(
            config_payload=config_payload, entry_price=fresh_price, equity=fresh_equity,
            stop_risk_per_coin=stop_risk_per_coin, contract_size=contract_size,
            lot_step=lot_step, min_contracts=min_contracts,
        )
    except ValueError as exc:
        return {"ok": False, "reason": f"invalid_sizing_input:{exc}"}
    if result["action"] == "NO_ENTRY":
        return {"ok": False, "reason": result["reason"]}
    output={"ok": True, "amount_coin": result["amount_coin"], "notional_usdt": result["notional_usdt"]}
    if sizing_mode == "VARIABLE_RISK":
        output["planned_loss_usdt"] = result["amount_coin"] * stop_risk_per_coin
        output["adaptive_risk_capped"] = True
    return output


def _pending(intent, reason="unknown_order_state", **extra):
    return {"intent_kind": intent.kind, "executed": False,
            "gate_result": reason, "reason": reason, "pending": True, **extra}


def _verify_and_finalize_entry(cfg, client, intent, snapshot, amount_coin, order,
                               ledger=None, epoch_store=None, record=None, *, gate_result):
    position = client.fetch_position()
    if position is None:
        return _pending(intent)
    quantity = position.get("contracts")
    price = position.get("entry_price")
    if (position.get("side") != intent.side or not _positive(quantity) or not _positive(price)):
        return _pending(intent, "entry_position_unverified", critical=True)
    if record is not None and quantity > record.requested_quantity + 1e-8:
        return _pending(intent, "entry_aggregate_exceeds_submission", critical=True)
    terminal_statuses = ("closed", "canceled", "cancelled", "rejected", "filled")
    if record is not None and (order or {}).get("status") not in terminal_statuses:
        try:
            observed_order = client.fetch_order_status_by_client_id(record.cl_ord_id)
            if observed_order:
                order = observed_order
        except Exception:
            pass
    submission_final = (order or {}).get("status") in terminal_statuses
    if submission_final and (
        ((order or {}).get("filled") is not None and not math.isclose(float(order["filled"]),quantity,rel_tol=1e-9,abs_tol=1e-8))
        or ((order or {}).get("average") is not None and not math.isclose(float(order["average"]),price,rel_tol=1e-9,abs_tol=1e-8))
    ):
        return _pending(intent,"entry_order_position_mismatch",critical=True)
    # R2(2026-09-17) - record.attach_algo_cl_ord_id는 이 진입 제출 때 이미 결정론적으로
    # 만들어 실제 attachAlgoOrds 요청에 실어 보낸 값이다(candidate_c_intent_ledger.
    # make_attach_algo_cl_ord_id) - 조건(방향/수량/SL·TP)만 보고 추측하지 않고, 이
    # ID로 "우리가 방금 그 요청으로 만든 그 보호주문"인지부터 확인한다.
    protection = _verify_protection_with_retry(
        client, intent.side, quantity, intent.raw_stop_price, intent.raw_target_price,
        expected_algo_cl_ord_id=record.attach_algo_cl_ord_id if record is not None else None)
    ids = client.fetch_pending_protection_algo_ids()
    if ids is None:
        return _pending(intent, "protection_query_unknown", critical=True)
    protection_confirmed = protection.get("ok") and protection.get("oco_count") == 1 and len(ids) == 1
    if record is not None:
        if quantity < record.requested_quantity:
            ledger.mark_partial_fill(record.intent_id, quantity)
        else:
            ledger.mark_filled(record.intent_id, quantity)
        cost_meta = _entry_cost_metadata(client, order, position, record)
        epoch = cem.PositionEpochState(
            execution_mode_at_open="LIVE", account_id=intent.account_id, symbol=intent.symbol,
            side=intent.side, entry_intent_id=record.intent_id, setup_id=intent.setup_id,
            weakening_prev=bool(getattr(intent, "entry_weakening_baseline", False)),
            config_version_id=str(intent.config_version_id), config_hash=intent.config_hash,
            entry_time_ms=int(time.time()*1000), raw_entry_price=price,
            exchange_position_id=position.get("position_id") or (position.get("info") or {}).get("posId"),
            exchange_entry_timestamp_ms=position.get("entry_timestamp_ms"),
            effective_entry_price=price, entry_fee_usdt=cost_meta["entry_fee_usdt"],
            fee_rate=cost_meta["fee_rate"], spread_bps=cost_meta["spread_bps"],
            slippage_bps=cost_meta["slippage_bps"], contract_size=record.contract_size,
            lot_step=record.lot_step, min_contracts=record.min_contracts, tick_size=record.tick_size,
            max_contracts=record.max_contracts, original_contracts=quantity, remaining_contracts=quantity,
            initial_stop_price=intent.raw_stop_price, current_stop_price=intent.raw_stop_price,
            target_price=intent.raw_target_price, high_water=price,
            remaining_reserved_risk_usdt=(record.reserved_risk_usdt * quantity / record.requested_quantity
                if submission_final else record.reserved_risk_usdt),
            remaining_gross_notional_usdt=quantity * record.contract_size * price,
            reservation_id=record.reservation_id, protective_algo_ids=list(ids) if protection_confirmed else [],
            attach_algo_cl_ord_id=record.attach_algo_cl_ord_id,
            protective_order_type=record.protective_order_type, strategy_policy=record.strategy_policy)
        # Even unprotected fills are durable owned exposure, never an invented FLAT.
        epoch_store.save(record.intent_id, epoch)
        ledger.mark_protection_pending(record.intent_id)
        if submission_final:
            ledger.mark_remote_submission_finalized(record.intent_id)
    trade_log.record_open(cfg.user_dir, intent.symbol, intent.side, price, quantity,
        dry_run=False, sl_price=intent.raw_stop_price, tp_price=intent.raw_target_price,
        strategy_group="candidate_c", execution_id=record.intent_id if record else None)
    if not protection_confirmed:
        return _pending(intent, "critical_unprotected_position", critical=True,
                        intent_id=record.intent_id if record else None,
                        filled_contracts=quantity, average_price=price)
    if record is not None:
        ledger.mark_protected(record.intent_id, list(ids))
        if submission_final:
            ledger.mark_remote_submission_finalized(record.intent_id)
        else:
            return _pending(intent,"entry_remainder_unknown",intent_id=record.intent_id,
                filled_contracts=quantity,average_price=price)
    # [2026-09-16, 사용자 직접 지시 - Candidate C 전용 GPT ON/OFF] 이 성공 반환은
    # 원래 "GPT approve_now"만이 여기 도달하는 유일한 방법이었을 때 만들어져
    # "approved"를 하드코딩했다. 이제 GPT 미사용(rule_based_no_gpt_review) 경로도
    # 동일하게 성공적으로 여기 도달할 수 있으므로, 실제로 이 주문을 통과시킨
    # 게이트 값을 그대로 남겨야 한다 - 하드코딩하면 두 경로의 결과가 섞여
    # 이후 성과를 GPT ON/OFF로 분리할 수 없게 된다(사용자가 명시적으로 금지한 것).
    contract_size = record.contract_size if record else client.contract_size()
    intent_id = record.intent_id if record else None
    event = _persist_notification_event(cfg, {
        "event_type": "entry", "symbol": intent.symbol, "side": intent.side,
        "price": price, "contracts": quantity,
        "amount_coin": quantity * contract_size,
        "stop_price": intent.raw_stop_price,
        "target_price": intent.raw_target_price,
        "intent_id": intent_id,
    })
    return {"intent_kind": intent.kind, "executed": True, "gate_result": gate_result,
            "error_reason": None, "order": order, "filled_contracts": quantity,
            "amount_coin": quantity * contract_size,
            "average_price": price, "protective_algo_ids": list(ids),
            "intent_id": intent_id, "notification_event": event}


def _positive(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0


def _reconcile_ambiguous_entry(cfg, client, intent, snapshot, amount_coin,
                             client_order_id, error_detail, ledger=None, epoch_store=None, record=None,
                             *, gate_result):
    try:
        order = client.fetch_order_status_by_client_id(client_order_id)
    except Exception:
        order = None
    # A single not-found read may race the submission. Only an explicit terminal
    # exchange order with zero fills permits releasing the reservation.
    if order and order.get("status") in ("canceled", "cancelled", "rejected") and order.get("filled") == 0:
        if ledger and record:
            ledger.mark_terminal(record.intent_id)
        return {"intent_kind": intent.kind, "executed": False,
                "gate_result": "confirmed_no_fill_after_ambiguous_response", "critical": False}
    if not order or not _positive(order.get("filled")):
        return _pending(intent, error_reason=error_detail,
                        intent_id=record.intent_id if record else None)
    return _verify_and_finalize_entry(cfg, client, intent, snapshot, amount_coin, order,
                                      ledger, epoch_store, record, gate_result=gate_result)


def _execute_entry(cfg, client, intent, snapshot: dict, equity: float, is_still_valid_fn,
                    start_equity_override: float | None,
                    open_position_count_fn=None, max_concurrent_positions: int | None = None,
                    ledger=None, epoch_store=None, strategy_policy=None, stop_event=None) -> dict:
    if not _candidate_c_new_entry_allowed(cfg, equity, start_equity_override):
        return {
            "intent_kind": dec.INTENT_ENTRY, "executed": False,
            "gate_result": "blocked_daily_loss_guard", "error_reason": None,
        }
    manual_block = _candidate_manual_close_entry_block(cfg, intent.symbol)
    if manual_block:
        return {
            "intent_kind": dec.INTENT_ENTRY, "executed": False,
            "gate_result": manual_block, "error_reason": None,
        }

    # [2026-09-16, 사용자 직접 지시 - Candidate C 전용 GPT ON/OFF] 기존 전역
    # GPT_ENTRY_GATE_ENABLED(CORE 쪽)와 완전히 분리된 별도 설정. 새 설정이 없는
    # 계정은 getattr 기본값 True로 기존 GPT ON 동작을 그대로 유지한다.
    if getattr(cfg, "CANDIDATE_C_GPT_ENTRY_GATE_ENABLED", True):
        gate = gga.verify_candidate_signal(cfg, intent, snapshot, is_still_valid_fn=is_still_valid_fn)
    else:
        gate = gga.rule_based_entry_without_gpt(cfg, intent, snapshot, is_still_valid_fn=is_still_valid_fn)
    if not gate["allowed"]:
        return {
            "intent_kind": dec.INTENT_ENTRY, "executed": False,
            "gate_result": gate["gate_result"], "error_reason": gate["error_reason"],
        }

    # CANDIDATE_C_LIVE_EXECUTE 하드 게이트(2026-09-13, 사용자 지시) - CANDIDATE_C_
    # ENABLED(분석 루프 자체)와 완전히 분리된 별도 스위치다. 여기까지는 결정론적
    # 신호 계산 + GPT 승인/거부 판정까지 전부 실제로 수행돼(Shadow 관찰 목적) 로그로
    # 남지만, 이 지점 이후(실제 계좌 락 + 주문 제출)로는 절대 진행하지 않는다 -
    # CANDIDATE_C_ENABLED=true/CANDIDATE_C_LIVE_EXECUTE=false 조합으로 "분석은
    # 돌지만 실주문은 없음" 상태를 그대로 만든다. reversal_machine은 이 반환 이후
    # 호출부가 FLAT으로 되돌리므로(machine.observe_entry_outcome(accepted=False)),
    # LIVE_EXECUTE를 나중에 true로 켜도 이전 Shadow 관찰이 상태를 오염시키지 않는다.
    if not getattr(cfg, "CANDIDATE_C_LIVE_EXECUTE", False):
        # side/raw_stop_price/raw_target_price(2026-09-14, 전향 평가 항목5) - 실제
        # 주문/락/제어흐름에는 전혀 영향 없는 순수 추가 필드다. GPT가 실제로 승인한
        # 진입 의도의 방향/목표가를 반환값에 실어, 별도의 가상 체결 기록기
        # (candidate_c_forward_paper_trading.py, 실주문 경로와 완전히 분리)가
        # "이 순간 실제로 주문했다면 어떤 조건이었을지"를 재구성할 수 있게 한다.
        return {
            "intent_kind": dec.INTENT_ENTRY, "executed": False,
            "gate_result": "shadow_mode_live_execute_disabled", "error_reason": None,
            "side": intent.side, "raw_stop_price": intent.raw_stop_price,
            "raw_target_price": intent.raw_target_price,
        }

    # Entry GPT OFF means only admission is rule-based. Price review remains independent:
    # Gemini proposes SL/TP and GPT reviews prices only. AI failure never blocks entry.
    if (not getattr(cfg, "CANDIDATE_C_GPT_ENTRY_GATE_ENABLED", True)
            and getattr(cfg, "CANDIDATE_C_AI_EXIT_PLAN_ENABLED", True)
            and _candidate_c_order_mode(cfg) != "MANUAL_ALL"):
        try:
            gate.update(gga.review_exit_plan_only(cfg, intent, snapshot))
        except Exception as exc:
            (cfg.logger or logger).warning(
                "[%s] Candidate C AI_EXIT_PLAN 가격전용 검증 예외(%s) - 진입은 유지, Adaptive fallback",
                intent.symbol, type(exc).__name__, exc_info=True,
            )
            gate.update({
                "ai_exit_plan": None, "ai_exit_source": "exit_review_exception",
                "gemini_exit_plan": None, "exit_review_error": type(exc).__name__,
            })

    # 항목6(2026-09-13) - GPT 승인은 "그 순간" 기준일 뿐이다. GPT 왕복 시간(+락
    # 대기 시간) 동안 계좌/포지션/가격이 바뀌었을 수 있으므로, 실제 주문 직전에
    # 전부 다시 확인한다 - 이 재확인+주문 전체를 계좌 공통 락으로 감싸 직렬화한다
    # (지금은 Candidate C 자신의 주문 경로만 이 락에 참여한다 - CORE의 trader.py가
    # 같은 락에 실제로 참여하는지는 별도 항목7에서 다룬다. 즉 이 락은 아직 "Candidate
    # C 내부 직렬화"만 보장하고, CORE와의 진짜 계좌 공통 직렬화는 아직 미완성이다).
    with ownership.account_order_lock(cfg.user_dir):
        # [2026-09-16, 사용자 직접 지시 - 안전 항목A] Candidate C 전체 중지
        # (candidate_c_stop_event, /api/candidate_c_stop)는 사이클 자체를
        # 다음 폴링부터만 멈춘다 - 이미 진행 중인 사이클(GPT 대기 포함)은
        # 구조적으로 중단시키지 않는다(candidate_c_trader_adapter.py의 while
        # 루프가 그렇게 짜여 있음, 새로 만들지 않음). 그래서 "중지 요청 중에
        # 늦게 도착한 승인이 그대로 주문으로 나가는" 문제를 막으려면, 이미
        # GPT 응답을 받은 뒤 실제 제출 직전인 여기서 한 번 더 신선하게
        # 확인해야 한다 - symbol_entry_control.is_paused()와 같은 자리, 같은
        # 원칙(재시도로 뚫으려 하지 않고 그냥 이번 시도를 포기한다).
        if stop_event is not None and stop_event.is_set():
            return {"intent_kind": intent.kind, "executed": False, "gate_result": "candidate_c_stopped"}
        import symbol_entry_control
        if symbol_entry_control.is_paused(cfg.user_dir, intent.symbol):
            return {"intent_kind": intent.kind, "executed": False, "gate_result": "user_entry_pause"}
        manual_block = _candidate_manual_close_entry_block(cfg, intent.symbol)
        if manual_block:
            return {"intent_kind": intent.kind, "executed": False, "gate_result": manual_block}
        if not getattr(cfg, "CANDIDATE_C_LIVE_EXECUTE", False):
            return {"intent_kind": intent.kind, "executed": False,
                    "gate_result": "shadow_mode_live_execute_disabled"}
        ledger.refresh()
        if ledger.pending_intents():
            return _pending(intent, "existing_durable_intent")
        fresh_equity = client.fetch_usdt_equity()
        if not _candidate_c_new_entry_allowed(cfg, fresh_equity):
            return {
                "intent_kind": dec.INTENT_ENTRY, "executed": False,
                "gate_result": "blocked_daily_loss_guard_post_gpt", "error_reason": None,
            }
        if not is_still_valid_fn():
            # gpt_gate_adapter도 승인 직후 한 번 확인하지만, 락을 잡느라 대기하는
            # 사이에 또 시간이 흐를 수 있어 주문 바로 직전에 한 번 더 확인한다.
            return {
                "intent_kind": dec.INTENT_ENTRY, "executed": False,
                "gate_result": "blocked_stale_post_gpt", "error_reason": None,
            }
        existing = client.fetch_position()
        if existing is not None:
            # 이 심볼에 이미 포지션이 있다 - reversal_machine이 FLAT일 때만
            # request_entry()를 허용하므로 정상 경로에서는 도달하지 않지만,
            # 외부에서 별도로 포지션이 생긴 경우(예: 수동 개입/다른 프로세스)까지
            # 대비해 실제 주문 직전에 한 번 더 확인한다 - 중복 포지션을 만들지 않는다.
            return {
                "intent_kind": dec.INTENT_ENTRY, "executed": False,
                "gate_result": "blocked_duplicate_position_exists", "error_reason": None,
            }
        # F4(2026-09-17, 외부 검토 지적 + 직접 재현 확인) - sizing 계산은
        # cfg.CANDIDATE_C_LEVERAGE를 가정하는데, 실제 거래소에 그 값을 적용한
        # 적이 없었다(CORE의 ensure_leverage()는 cfg.LEVERAGE를 쓰고, Candidate C는
        # 아예 호출하지 않았음 - sizing과 실제 적용값이 어긋날 수 있는 구조적
        # 결함). 여기(포지션 없음이 이미 확인된 지점)에서만 명시적으로 자신의
        # 값을 적용한다 - 실패/충돌(예: 다른 프로세스가 그 사이 포지션을 열어
        # 거래소가 레버리지 변경을 거부)이면 추측하지 않고 신규 진입을 막는다.
        try:
            client.ensure_leverage(cfg.CANDIDATE_C_LEVERAGE)
        except Exception as exc:
            return {
                "intent_kind": dec.INTENT_ENTRY, "executed": False,
                "gate_result": "blocked_leverage_verification_failed", "error_reason": str(exc)[:500],
            }
        # 항목3(2026-09-13) - 포지션은 없지만 보호주문(algo)만 남아있는 "고아"
        # 상태를 확인한다(이전 포지션 청산 시 보호주문 정리가 누락된 경우). 이
        # 상태에서 새로 SL/TP를 붙이면 order_safety.verify_protection()의 OCO
        # 매칭이 오래된 algo와 뒤섞여 오판할 위험이 있다 - 확인 자체가 실패해도
        # (조회 예외) 낙관하지 않고 막는다(fail-closed).
        try:
            orphan_algo_ids = client.fetch_pending_protection_algo_ids()
        except Exception:
            orphan_algo_ids = None
        if orphan_algo_ids is None:
            return {
                "intent_kind": dec.INTENT_ENTRY, "executed": False,
                "gate_result": "blocked_protection_orphan_check_failed", "error_reason": None,
            }
        if orphan_algo_ids:
            return {
                "intent_kind": dec.INTENT_ENTRY, "executed": False,
                "gate_result": "blocked_orphan_protective_order_exists", "error_reason": None,
            }
        if open_position_count_fn is not None and max_concurrent_positions is not None:
            # 항목3 - Candidate C 전체(이 심볼 하나가 아니라 DOGE+SOL 등 소유한
            # 모든 심볼) 동시포지션 수 한도. 실제 여러 심볼에 걸친 카운트를
            # 계산해서 넘겨주는 오케스트레이션(멀티심볼 wiring)은 아직 없다 -
            # 이 함수는 그 카운트를 넘겨받는 인터페이스만 미리 갖춘다(호출부가
            # 넘기지 않으면 이 체크 자체를 건너뛴다 - 기존 동작 불변).
            try:
                current_count = open_position_count_fn()
            except Exception as exc:
                return _pending(intent, "blocked_position_count_unknown", error_reason=str(exc))
            if current_count >= max_concurrent_positions:
                return {
                    "intent_kind": dec.INTENT_ENTRY, "executed": False,
                    "gate_result": "blocked_max_concurrent_positions", "error_reason": None,
                }

        # 항목1(2026-09-13) - 실제 주문 직전 가격(is_still_valid_fn과 별개로 여기서
        # 다시 조회 - 사이징은 그 순간의 최신가를 기준으로 해야 한다)으로 실제
        # 수량을 계산한다. 이전에는 intent.requested_risk_pct(퍼센트 숫자)를 그대로
        # 코인 수량으로 취급하는 자리표시자였다 - 단위가 완전히 틀려서 실주문에
        # 연결하면 안 되는 상태였다.
        try:
            fresh_price = client.fetch_last_price()
        except Exception:
            fresh_price = None
        try:
            effective_stop, effective_target = _candidate_c_entry_protection_prices(cfg, intent, fresh_price)
        except (TypeError, ValueError) as exc:
            return {"intent_kind": dec.INTENT_ENTRY, "executed": False,
                    "gate_result": "blocked_invalid_manual_protection", "error_reason": str(exc)}
        intent = dataclasses.replace(intent, raw_stop_price=effective_stop, raw_target_price=effective_target)
        if _candidate_c_order_mode(cfg) != "MANUAL_ALL":
            intent, ai_exit_reason = _apply_verified_ai_exit_plan_to_intent(
                intent, gate, entry_price=fresh_price, atr_4h=snapshot.get("atr_4h"),
            )
            (cfg.logger or logger).info(
                "[%s] Candidate C AI_EXIT_PLAN 실행결정 source=%s result=%s SL=%s TP=%s",
                intent.symbol, gate.get("ai_exit_source"), ai_exit_reason,
                intent.raw_stop_price, intent.raw_target_price,
            )
        sizing = _calculate_candidate_c_entry_amount(cfg, client, intent, fresh_price, fresh_equity)
        if not sizing["ok"]:
            return {
                "intent_kind": dec.INTENT_ENTRY, "executed": False,
                "gate_result": f"blocked_sizing_{sizing['reason']}", "error_reason": sizing["reason"],
            }
        amount_coin = sizing["amount_coin"]
        meta = client.instrument_metadata()
        quantity = amount_coin / meta["contract_size"]
        record = ledger.persist_intent(
            account_id=intent.account_id, symbol=intent.symbol, strategy_id="candidate_c",
            setup_id=intent.setup_id or intent.idempotency_key, position_epoch=None,
            config_version_id=str(intent.config_version_id), config_hash=intent.config_hash,
            kind=intent.kind, requested_side=intent.side, requested_quantity=quantity,
            requested_stop_price=intent.raw_stop_price, requested_target_price=intent.raw_target_price,
            contract_size=meta["contract_size"], lot_step=meta["lot_step"],
            min_contracts=meta["min_contracts"], tick_size=meta.get("tick_size"),
            max_contracts=meta.get("max_contracts"), reservation_id=intent.idempotency_key,
            reserved_risk_usdt=amount_coin * abs(fresh_price-intent.raw_stop_price),
            gross_notional_usdt=amount_coin*fresh_price, entry_price_estimate=fresh_price,
            strategy_policy=strategy_policy)
        if record.state != il.IntentState.INTENT_PERSISTED.value:
            return _pending(intent, "existing_durable_intent", intent_id=record.intent_id)
        response = {}
        def submit():
            # F3(2026-09-16, 외부 검토 지적 + 직접 재현 확인) - okx_client.
            # create_position_with_sl_tp()는 이미 "거래소에 절대 도달하지 않은
            # 확정 거부"(ccxt.InvalidOrder/InsufficientFunds/AuthenticationError,
            # 또는 우리가 실제로 확인한 안전한 OKX 거부 코드 - _is_definite_rejection
            # 참고)를 raw ccxt 예외로 그대로 재던지고, 그 외 애매한 경우만
            # UnknownOrderStateError로 감싼다(okx_client.py 자체 계약, 여기서
            # 바꾸지 않음 - CORE도 같은 함수를 쓰므로 그 계약을 건드리면 안 된다).
            # 그런데 여기서 이 구분을 무시하고 전부 그대로 던지면 attempt_submit()의
            # bare except가 다시 뭉뚱그려 submission_unknown(SAFE_HALT 후보)으로
            # 만든다 - 이미 확정된 구분을 여기서 살려서 "rejected"로 넘긴다(재시도
            # 금지는 그대로 유지 - ledger.attempt_submit()이 REJECTED를 즉시
            # mark_terminal()하므로 같은 intent_id 재제출은 여전히 막힌다).
            try:
                response["order"] = client.create_position_with_sl_tp(
                    intent.side, amount_coin, intent.raw_stop_price, intent.raw_target_price,
                    client_order_id=record.cl_ord_id,
                    attach_algo_cl_ord_id=record.attach_algo_cl_ord_id)
            except ccxt.ExchangeError as exc:
                return {"outcome": "rejected", "reason": f"{type(exc).__name__}: {exc}"[:500]}
            order = response["order"]
            has_id = bool(order and order.get("id"))
            return {"outcome": "ack" if has_id else "unknown",
                    "exchange_order_id": (order or {}).get("id"),
                    # 예외 없이 돌아왔지만 id가 없는 응답도(예외가 없다고 성공은
                    # 아님) 원인을 남긴다 - attempt_submit()의 except 분기와
                    # 동일한 취지, 그냥 다른 원인일 뿐이다.
                    "unknown_reason": None if has_id else f"order response missing id: {order!r}"[:500]}
        submitted = ledger.attempt_submit(record.intent_id, submit)
        # REJECTED는 attempt_submit() 안에서 곧바로 mark_terminal()까지 실행되므로
        # 여기 도달했을 때 submitted.state는 이미 "terminal"이다(SUBMISSION_UNKNOWN과
        # 달리 별도 reconcile을 기다리지 않음) - reject_reason의 존재 여부로 판별한다.
        if submitted.reject_reason is not None:
            return {"intent_kind": dec.INTENT_ENTRY, "executed": False,
                    "gate_result": "rejected_by_exchange", "error_reason": submitted.reject_reason}
        if submitted.state == il.IntentState.SUBMISSION_UNKNOWN.value:
            return _reconcile_ambiguous_entry(cfg, client, intent, snapshot, amount_coin,
                record.cl_ord_id, submitted.submission_unknown_reason or "submission response unknown",
                ledger, epoch_store, record, gate_result=gate["gate_result"])
        result = _verify_and_finalize_entry(cfg, client, intent, snapshot, amount_coin,
                                          response.get("order"), ledger, epoch_store, record,
                                          gate_result=gate["gate_result"])
        if _candidate_c_order_mode(cfg) != "MANUAL_ALL":
            try:
                audit_row = {
                    "engine":"Candidate C", "symbol":intent.symbol, "side":intent.side,
                    "gemini_exit_plan":gate.get("gemini_exit_plan"),
                    "gpt_exit_plan_decision":gate.get("gpt_exit_plan_decision"),
                    "gpt_exit_plan":gate.get("gpt_exit_plan"), "ai_source":gate.get("ai_exit_source"),
                    "result":ai_exit_reason, "final_sl":intent.raw_stop_price,
                    "final_tp":intent.raw_target_price, "order_executed":bool(result.get("executed")),
                }
                audit_row = ai_exit_plan_audit.enrich_with_exchange_protection(client, audit_row)
                ai_exit_plan_audit.append_record(cfg.user_dir, audit_row)
            except Exception:
                (cfg.logger or logger).warning("[%s] Candidate C AI_EXIT_PLAN observability 기록 실패", intent.symbol, exc_info=True)
        return result


CLOSE_HISTORY_VERIFY_RETRIES = 8
CLOSE_HISTORY_VERIFY_DELAY_SECONDS = 1.0


def _close_order_fill_evidence(client, close_order_id: str | None, since_ms: int | None,
                               expected_close_contracts: float | None) -> dict | None:
    """Exact final-close order evidence. Never infer from another order on the symbol."""
    if not close_order_id:
        return None
    try:
        trades = client.fetch_trades_for_order(
            str(close_order_id), max(0, int(since_ms or 0) - 60_000), limit=100,
        )
    except Exception:
        trades = []
    if not trades:
        return None
    amounts = [float(t.get("amount") or 0.0) for t in trades]
    total_amount = sum(amounts)
    if expected_close_contracts is not None and not math.isclose(
            total_amount, float(expected_close_contracts), rel_tol=1e-9, abs_tol=1e-8):
        return None
    weighted_num = sum(float(t.get("price") or 0.0) * amount for t, amount in zip(trades, amounts))
    if total_amount <= 0 or weighted_num <= 0:
        return None
    fill_times = [int(t["timestamp"]) for t in trades if t.get("timestamp") is not None]
    if not fill_times:
        return None
    return {
        "close_price": weighted_num / total_amount,
        "close_fee": sum(abs(float((t.get("fee") or {}).get("cost") or 0.0)) for t in trades),
        "fill_timestamp_ms": max(fill_times),
        "filled_contracts": total_amount,
    }


def _resolve_candidate_c_close_pnl(
    client, position: dict, *, close_order_id: str | None = None,
    expected_close_contracts: float | None = None,
    expected_total_contracts: float | None = None,
) -> dict:
    """Resolve the *fresh final* OKX position-history record after a managed close.

    OKX mutates the same posId/cTime history row as partial reductions happen. Immediately
    after a final market close the history endpoint can briefly return the previous partial
    state. Exact identity alone is therefore insufficient: when the final order is known,
    require history uTime >= that order's fill time and closeTotalPos to cover the full epoch.
    """
    evidence = _close_order_fill_evidence(
        client, close_order_id, position.get("entry_timestamp_ms"), expected_close_contracts,
    )
    resolved = None
    if (evidence is not None and position.get("position_id")
            and position.get("entry_timestamp_ms")):
        for attempt in range(CLOSE_HISTORY_VERIFY_RETRIES):
            try:
                history = client.exchange.fetch_positions_history([client.symbol], limit=100)
            except Exception:
                history = []
            matches = []
            for row in history:
                info = row.get("info") or {}
                if (str(info.get("posId")) != str(position.get("position_id"))
                        or str(info.get("cTime")) != str(position.get("entry_timestamp_ms"))):
                    continue
                try:
                    update_ms = int(info.get("uTime"))
                    closed_total = float(info.get("closeTotalPos"))
                except (TypeError, ValueError):
                    continue
                if update_ms < evidence["fill_timestamp_ms"]:
                    continue
                if (expected_total_contracts is not None
                        and closed_total + 1e-8 < float(expected_total_contracts)):
                    continue
                matches.append(row)
            if matches:
                matches.sort(key=lambda r: int((r.get("info") or {}).get("uTime") or 0), reverse=True)
                resolved = okx_client.match_realized_close(
                    [matches[0]], position["side"], position["raw_entry_price"],
                    int(time.time() * 1000),
                    max_age_minutes=max(30, int((time.time()*1000-position["entry_timestamp_ms"])/60000)+5),
                )
                if resolved is not None:
                    resolved = dict(resolved)
                    resolved["exit_price"] = evidence["close_price"]
                    resolved["close_order_fee"] = evidence["close_fee"]
                    resolved["close_order_id"] = str(close_order_id)
                    return resolved
            if attempt < CLOSE_HISTORY_VERIFY_RETRIES - 1:
                time.sleep(CLOSE_HISTORY_VERIFY_DELAY_SECONDS)
    elif evidence is None:
        try:
            if position.get("position_id") or position.get("entry_timestamp_ms"):
                resolved = client.fetch_realized_close_for_position(
                    position["side"], position["raw_entry_price"],
                    position.get("position_id"), position.get("entry_timestamp_ms"),
                )
            else:
                resolved = client.fetch_last_realized_close(
                    position["side"], position["raw_entry_price"])
        except Exception:
            resolved = None
    if resolved is not None:
        return resolved
    return {
        "gross_pnl": position.get("pre_close_unrealized_pnl") or 0.0,
        "fee": 0.0, "net_pnl": None, "exit_price": None,
        "funding_fee": None, "source": "estimated",
    }


def _resolve_candidate_c_reduce_pnl(cfg, client, symbol: str, position: dict, reduced_coin_amount: float,
                                     reduce_order_id: str | None = None) -> dict:
    """CORE(trader.py._resolve_reduce_pnl)와 동일한 공식/원칙을 그대로 재사용한다 -
    OKX position history(fetch_positions_history)는 포지션이 "완전히" 닫힐 때만
    기록이 생겨서 부분 감축에는 쓸 수 없다(_resolve_candidate_c_close_pnl과 달리
    이 경로는 절대 pnl_source="okx_realized"를 반환하지 않는다). pnl_source는
    항상 "estimated"로 명시한다(CORE와 동일한 이유) - gross_pnl 자체가
    mark_price 기준 추정치이기 때문이다.

    수수료 매칭 정밀도(항목5, 2026-09-13 개선): "CORE도 같은 한계"는 완료 근거가
    아니라는 지적을 받아들여, 가능하면 order_id로 정확히 매칭한다 -
    client.fetch_trades_for_order(reduce_order_id, ...)는 OKX의 params={'ordId':...}
    서버측 필터링을 쓰므로, 같은 심볼의 다른(예: CORE 또는 이전 Candidate C)
    체결이 섞일 수 없다. reduce_order_id가 없거나(호출부가 못 넘김) 그 주문ID로
    체결을 하나도 못 찾으면(반영 지연 등) - 절대 다른 체결과 섞어 추측하지
    않는다. 보수적으로 fee=0.0을 쓰고 fee_source="estimated_or_ambiguous"로
    명시하며 경고 로그를 남긴다(이 값을 확정 수수료처럼 취급하면 안 된다)."""
    side_sign = 1 if position["side"] == "long" else -1
    try:
        live_position = client.fetch_position()
    except Exception:
        live_position = None
    mark_price = (live_position.get("mark_price") if live_position else None) or position["raw_entry_price"]
    gross_pnl = (mark_price - position["raw_entry_price"]) * side_sign * reduced_coin_amount

    since_ms = int((datetime.datetime.now() - datetime.timedelta(minutes=5)).timestamp() * 1000)
    fee = 0.0
    fee_source = "estimated_or_ambiguous"
    if reduce_order_id is not None:
        try:
            trades = client.fetch_trades_for_order(reduce_order_id, since_ms)
        except Exception:
            trades = []
        if trades:
            fee = sum(abs(float((t.get("fee") or {}).get("cost") or 0.0)) for t in trades)
            fee_source = "order_id_matched"
        elif cfg.logger is not None:
            cfg.logger.warning(
                "[%s] 부분감축 수수료: order_id=%s로 체결을 하나도 못 찾음 - 다른 체결과 "
                "섞어 추측하지 않고 fee=0.0(estimated_or_ambiguous)으로 보수적 처리",
                symbol, reduce_order_id,
            )
    return {
        "gross_pnl": gross_pnl, "fee": fee, "net_pnl": None, "exit_price": mark_price,
        "funding_fee": None, "source": "estimated", "fee_source": fee_source,
    }


def execute_intent(
    cfg, client, intent, *, snapshot: dict, equity: float, is_still_valid_fn,
    current_position: dict | None = None, current_protection: dict | None = None,
    start_equity_override: float | None = None,
    open_position_count_fn=None, max_concurrent_positions: int | None = None,
    ledger=None, epoch_store=None, strategy_policy=None, allow_missing_protection=False,
    stop_event=None,
) -> dict:
    """decide()가 반환한 단일 Intent를 실행한다. 반환 dict는 항상 "intent_kind"와
    "executed"를 포함한다. 실행 성공/실패와 무관하게 setup_tracker/epoch_store/
    reversal_machine의 상태 갱신은 이 함수의 책임이 아니다 - 호출부(사이클
    오케스트레이터)가 이 반환값을 보고 실행이 실제로 성공했을 때만 그 상태들을
    갱신해야 한다(결정/실행/영속화 분리 원칙, candidate_c_decision_engine.decide()와
    동일한 설계).

    open_position_count_fn/max_concurrent_positions(항목3, 2026-09-13) - Candidate C
    전체(여러 심볼)의 동시 보유 포지션 수 한도를 신규 진입 직전에 재확인하기
    위한 훅이다. 지금은 단일 심볼 사이클(run_steady_state_cycle)만 존재해서
    실제 멀티심볼 카운트를 계산해 넘겨주는 오케스트레이션이 아직 없다 - 둘 다
    None(기본값)이면 이 체크 자체를 건너뛰어 기존 동작과 완전히 동일하다."""
    if intent.kind == dec.INTENT_NO_ACTION:
        return {"intent_kind": dec.INTENT_NO_ACTION, "executed": False}
    if intent.kind != dec.INTENT_ENTRY and not getattr(cfg, "CANDIDATE_C_LIVE_EXECUTE", False):
        # side/raw_stop_price(2026-09-14, 전향 평가 항목5) - entry와 동일한 이유로
        # 순수 참고용 필드만 추가한다(제어흐름/실주문 전혀 무관). reduce_quantity/
        # target_residual(2026-09-14, ChatGPT 검토 지적 - 부분감축 계약 보강)도
        # 마찬가지로 순수 추가 필드다 - ReduceIntent가 아니면 Intent 데이터클래스
        # 기본값 그대로 둘 다 None이라 다른 kind의 반환값은 전혀 바뀌지 않는다.
        return {"intent_kind": intent.kind, "executed": False,
                "gate_result": "shadow_mode_live_execute_disabled",
                "side": intent.side, "raw_stop_price": intent.raw_stop_price,
                "reduce_quantity": intent.reduce_quantity, "target_residual": intent.target_residual}
    safe_symbol = intent.symbol.replace("/", "_").replace(":", "_")
    if ledger is None:
        ledger = il.IntentLedger.load(os.path.join(cfg.user_dir, f"candidate_c_live_{safe_symbol}_intents.jsonl"), cfg.user_dir, intent.symbol)
    if epoch_store is None:
        epoch_store = cem.PositionEpochStore.load(os.path.join(cfg.user_dir, f"candidate_c_live_{safe_symbol}_epochs.jsonl"))
    if getattr(client, "symbol", intent.symbol) != intent.symbol:
        return _pending(intent, "blocked_client_symbol_mismatch", critical=True)
    if intent.kind == dec.INTENT_ENTRY and intent.symbol in set(getattr(cfg, "CORE_SYMBOLS", [])) & set(getattr(cfg,"ENABLED_SYMBOLS",getattr(cfg,"CORE_SYMBOLS",[]))):
        return _pending(intent, "blocked_foreign_symbol", critical=True)
    if intent.kind == dec.INTENT_ENTRY:
        # stop_event(안전 항목A)는 오직 "신규진입" 경로에만 적용한다 - 기존
        # 포지션의 관리/청산(아래 STOP_UPDATE/EXIT/REVERSAL/REDUCE 분기)은
        # Candidate C 중지 요청과 무관하게 항상 그대로 유지돼야 한다(사용자
        # 지시, /api/candidate_c_stop 자신의 문서화된 계약과도 일치).
        return _execute_entry(
            cfg, client, intent, snapshot, equity, is_still_valid_fn, start_equity_override,
            open_position_count_fn=open_position_count_fn, max_concurrent_positions=max_concurrent_positions,
            ledger=ledger, epoch_store=epoch_store, strategy_policy=strategy_policy,
            stop_event=stop_event,
        )
    if intent.kind in (dec.INTENT_STOP_UPDATE, dec.INTENT_EXIT, dec.INTENT_REVERSAL, dec.INTENT_REDUCE):
        with ownership.account_order_lock(cfg.user_dir):
            if not getattr(cfg, "CANDIDATE_C_LIVE_EXECUTE", False):
                return {"intent_kind": intent.kind, "executed": False,
                        "gate_result": "shadow_mode_live_execute_disabled"}
            ledger.refresh()
            outstanding = [record for record in ledger.pending_intents() if record.kind != dec.INTENT_ENTRY]
            emergency_stop_only = (allow_missing_protection and intent.kind == dec.INTENT_EXIT
                and outstanding and all(record.kind == dec.INTENT_STOP_UPDATE for record in outstanding))
            if outstanding and not emergency_stop_only:
                return _pending(intent, "unresolved_management_intent", critical=True)
            proof = ownership.validate_candidate_c_position_owner(client, intent.symbol, ledger, epoch_store,
                expected_position_id=intent.position_epoch, allow_missing_protection=allow_missing_protection)
            if not proof["allowed"]:
                return _pending(intent, "blocked_ownership_" + str(proof["reason"]), critical=True)
            return _execute_managed(cfg, client, intent, ledger, epoch_store, proof)
    return {"intent_kind": intent.kind, "executed": False, "reason": "unknown_intent_kind"}


def _worst_matched_sl_price(matched_orders, side):
    """verify_protection()이 돌려준 matched_orders(신원 확인된, 우리 소유의 실제
    algo 주문들)에서 가장 덜 보호적인 slTriggerPx를 뽑는다(2026-09-14 v9). 정상
    상태에서는 보통 1개뿐이지만, 여러 개가 매칭되는 비정상 상태라면 그중 하나만
    부족해도 목표를 확보 못 한 것으로 봐야 하므로 가장 나쁜 값을 기준으로 삼는다
    (장: 가장 낮은 값, 숏: 가장 높은 값 = 둘 다 "가장 손절에 가까운" 값). 값을
    못 읽으면(파싱 실패 등) 비교 자체를 할 수 없으므로 None을 돌려줘 호출부가
    "확인 실패로 취급하지 않는다"는 기존 정책과 별개로 안전하게 스킵 경로를
    타게 한다(허용오차 매치 자체는 이미 confirmed이므로)."""
    prices = []
    for order in matched_orders or []:
        try:
            prices.append(float(order.get("slTriggerPx")))
        except (TypeError, ValueError):
            continue
    if not prices:
        return None
    return min(prices) if side == "long" else max(prices)


def _stop_update_target_and_disposition(client, epoch, side, before, intent):
    """STOP_UPDATE Intent 하나에 대해 (호가 단위로 정규화한 목표가,
    처리방식, 사유)를 반환한다. 처리방식은 셋 중 하나:
    - "skip": 거래소 호출 자체를 시작하지 않는다(취소/재설치 없음, 새 durable
      intent도 없음).
    - "proceed": 정상적인 취소/재설치 경로로 진행해야 한다(실제로 확인된
      불일치/부재, 또는 애초에 확인할 근거 자체가 없는 경우).
    - "unknown": 지금 실제 보호 상태를 확인할 수 없다(조회 실패) - 스킵도
      일반 진행도 하지 않는다(확인 안 된 상태에서 기존 보호를 취소하는
      것 자체가 위험할 수 있음) - 호출부가 기존 pending/대사 경로로 넘겨야
      한다.

    반환하는 목표가는 항상 정규화된 값이다 - 2026-09-14 v8 재검토 지적,
    직접 재현 확인: 이전에는 이 정규화가 스킵 판단(비교)에만 쓰이고, 실제
    제출(attach_protection)·원장(requested_stop_price)·검증(_verify_
    protection_with_retry)·epoch 기록(current_stop_price)에는 전부 원시
    intent.raw_stop_price가 그대로 나갔다(재현: tick=0.001, 롱 현재 0.105/
    후보 0.10503 -> 비교에선 0.106으로 정규화했지만 실제 attach_protection·
    epoch 기록엔 0.10503이 그대로 남음). 호출부가 이 반환값으로 intent 자체를
    바꿔치기해 이후 전 구간이 같은 목표를 쓰게 해야 한다.

    1) 후퇴 후보(stop_would_loosen) - 2026-09-14 v5 수정.
    2) 2026-09-14 v7 수정 - (1)은 엄격한 부등호라 "정확히 같은 값"은 통과시켜,
       decide()가 이전과 동일한 후보를 다시 낼 때마다 아무 실익 없이 기존
       OCO를 매번 취소·재설치했다. 호가 단위로 정규화한 후보가 현재 확인된
       stop과 같고 수량도 마지막으로 확인된 값과 같을 때만 후보로 삼는다.
    3) 2026-09-14 v8 재검토 지적, 직접 재현 확인 - "algo ID가 1개 있다"는
       사실만으로는 그 algo의 실제 SL이 목표와 같은지 전혀 확인하지 않았다
       (재현: 기록/후보 SL 0.105, 실제 algo의 SL 0.090이어도 "정상 확인"으로
       오판). order_safety.verify_protection()(방향·수량·SL/TP 트리거가·live
       상태까지 tick 허용오차로 실제 확인하는 기존 공용 계약)을 그대로 재사용해
       정규화한 목표 자체를 넘겨 확인한다. 그 확인 자체가 실패(oco_lookup_failed)
       하면 "확인된 불일치"가 아니라 "확인 불가"이므로 "proceed"가 아니라
       "unknown"으로 분리한다(이전에는 둘 다 그냥 None -> 일반 취소/재설치로
       흘러가, 확인 안 된 상태에서도 이미 정상일 수 있는 보호를 건드릴 위험이
       있었다)."""
    if epoch.current_stop_price is None or intent.raw_stop_price is None:
        return intent.raw_stop_price, "proceed", None  # stop_would_loosen() 계약대로 항상 허용.
    tick_size = epoch.tick_size
    # "후퇴 여부"와 "동일값 여부"를 서로 다른 기준(하나는 raw, 하나는 반올림)으로
    # 비교하면 모순이 생긴다 - 두 판단 모두 "실제로 설치될" 반올림된 값 기준으로
    # 통일한다.
    normalized_candidate = stop_contract.round_stop_price_never_loosening(side, intent.raw_stop_price, tick_size)
    normalized_current = stop_contract.round_stop_price_never_loosening(side, epoch.current_stop_price, tick_size)
    if stop_contract.stop_would_loosen(side, normalized_current, normalized_candidate):
        return normalized_candidate, "skip", "stop_update_would_loosen_skipped"
    if normalized_candidate != normalized_current:
        return normalized_candidate, "proceed", None
    last_known_qty = epoch.remaining_contracts if epoch.remaining_contracts is not None else epoch.original_contracts
    if last_known_qty is not None and abs(last_known_qty - before) > 1e-9:
        return normalized_candidate, "proceed", None
    # R2(2026-09-17) - epoch.protective_algo_ids는 이미 소유권이 확인된 값이다
    # (entry 확정 또는 이전 amend 확정 때 ID로 검증하고 기록함) - 정확히 하나면
    # phase A(관리 권한) 확인까지 같이 넘긴다.
    verification = order_safety.verify_protection(
        client, side, before, expected_sl_price=normalized_candidate, expected_tp_price=epoch.target_price,
        expected_algo_id=(epoch.protective_algo_ids[0] if len(epoch.protective_algo_ids) == 1 else None),
    )
    if verification["ok"]:
        # 2026-09-14 v9 재검토 지적, 직접 재현 확인 - verify_protection()의 매칭
        # 허용오차(PRICE_TICK_TOLERANCE_MULTIPLE=2틱)는 "이게 우리가 붙인 그
        # OCO가 맞는지"(신원 확인, exchange rounding/echo-back 오차 흡수용)를
        # 위한 것이지 "목표 손절을 실제로 확보했는지"를 위한 것이 아니다. 같은
        # 소유 algo인데 실제 slTriggerPx가 정규화된 목표보다 딱 1틱 덜
        # 보호적이어도(예: 실제 tick=0.00001, 목표 0.105/실제 0.10499) 이
        # 허용오차 안에 들어 "정상 확인"으로 스킵되고 profit_lock_active까지
        # True로 찍힐 수 있었다 - 목표를 아직 확보 못 했는데 확보했다고 기록한
        # 셈. 공용 verify_protection()/PRICE_TICK_TOLERANCE_MULTIPLE 자체(CORE도
        # 같이 씀)는 그대로 두고, Candidate C의 no-op 판정에만 실제 설치된
        # 값으로 "목표보다 덜 보호적이지 않은지"를 별도로 확인한다 - 이미
        # 검증된 stop_would_loosen()을 (목표를 "현재", 실제 설치값을 "후보"로)
        # 그대로 재사용해 방향(장/숏)을 다시 손으로 구현하지 않는다.
        actual_sl = _worst_matched_sl_price(verification.get("matched_orders"), side)
        if actual_sl is None or not stop_contract.stop_would_loosen(side, normalized_candidate, actual_sl):
            return normalized_candidate, "skip", "stop_update_noop_already_matches_confirmed_protection"
        return normalized_candidate, "proceed", None
    if verification.get("reason") == "oco_lookup_failed":
        return normalized_candidate, "unknown", "protection_state_unknown_before_stop_update"
    # oco_missing/oco_mismatch/side_mismatch/size_mismatch/position_missing -
    # 실제로 확인된 불일치/부재다. 정상 취소/재설치 경로로 진행해 실제로 고친다.
    return normalized_candidate, "proceed", None


def _execute_managed(cfg, client, intent, ledger, epoch_store, proof):
    parent = proof["entry_record"]
    epoch = epoch_store.get(parent.intent_id)
    position = {**proof["position"], "raw_entry_price": epoch.raw_entry_price}
    before = position["contracts"]
    # 2026-09-14 수정(ChatGPT v5/v7 재검토 P1 지적, 직접 재현 확인) - decide()의
    # trailing 분기는 이미 설치된 stop을 모르고 순수 ATR 공식만으로 새 후보를
    # 낸다(설계상 결정/실행이 분리돼 있음 - backtest_engine.py/portfolio_mtm_
    # engine.py도 같은 이유로 이 검사를 decide() 안이 아니라 각자의 실행 지점에
    # 둔다). 후퇴 후보든, 실익 없는 동일 후보든 실제 거래소 취소/재설치 자체를
    # 시작하지 않는다 - durable ledger intent도 만들지 않아(아래 persist_intent
    # 이전에 반환) 취소할 대상 자체가 생기지 않는다.
    if intent.kind == dec.INTENT_STOP_UPDATE:
        normalized_target, disposition, reason = _stop_update_target_and_disposition(
            client, epoch, position["side"], before, intent)
        if disposition == "unknown":
            # 2026-09-14 v8 재검토 지적 - 확인 자체가 실패한 상태에서 일반
            # 취소/재설치로 흘려보내면, 이미 정상일 수 있는 기존 보호를 실수로
            # 건드릴 위험이 있다. 스킵도 진행도 하지 않고 기존 critical/pending
            # 경로(대사가 나중에 다시 확인)로 넘긴다 - 새 durable intent도,
            # 거래소 호출도 없음.
            return _pending(intent, reason, critical=True,
                            current_stop_price=epoch.current_stop_price, candidate_stop_price=normalized_target)
        if disposition == "skip":
            if (reason == "stop_update_noop_already_matches_confirmed_protection"
                    and intent.reason_code == "profit_lock_activated" and not epoch.profit_lock_active):
                # 동일 가격이라도 profit-lock 조건 자체는 충족됐고, 방금 그
                # 가격이 실제로 확인된 보호임을 재확인했다 - 상태만 반영한다
                # (거래소 호출은 없음).
                epoch.profit_lock_stop_price = normalized_target
                epoch.profit_lock_active = True
                epoch_store.save(parent.intent_id, epoch)
            return {"intent_kind": intent.kind, "executed": False, "reason": reason,
                    "current_stop_price": epoch.current_stop_price, "candidate_stop_price": normalized_target}
        # disposition == "proceed" - 정규화한 목표를 이후 전 구간(원장·제출·
        # 검증·epoch 기록)에서 일관되게 쓰도록 intent 자체를 바꿔치기한다 -
        # 비교에만 정규화를 쓰고 실행은 원시값을 쓰는 불일치를 막기 위해
        # (2026-09-14 v8 재검토 지적, 직접 재현 확인).
        intent = dataclasses.replace(intent, raw_stop_price=normalized_target)
    quantity = intent.reduce_quantity if intent.kind == dec.INTENT_REDUCE else before
    if not _positive(quantity) or quantity > before:
        return _pending(intent, "invalid_management_quantity", critical=True)
    # Every action has an independent ID, including subsequent trailing updates.
    action = ledger.persist_intent(account_id=intent.account_id, symbol=intent.symbol,
        strategy_id="candidate_c", setup_id=None,
        position_epoch=parent.intent_id + ":" + intent.idempotency_key,
        config_version_id=str(intent.config_version_id), config_hash=intent.config_hash,
        kind=intent.kind, requested_side=position["side"], requested_quantity=quantity,
        requested_stop_price=intent.raw_stop_price, contract_size=epoch.contract_size)
    if action.state != il.IntentState.INTENT_PERSISTED.value:
        return _pending(intent, "existing_durable_intent", intent_id=action.intent_id)
    response = {}
    def submit():
        if intent.kind == dec.INTENT_STOP_UPDATE:
            if intent.reason_code == "profit_lock_activated":
                epoch.profit_lock_stop_price = intent.raw_stop_price
                epoch_store.save(parent.intent_id, epoch)
            # R1(2026-09-17, 외부 검토 R1 대응) - 기존 cancel_protection()+
            # attach_protection() 순서(먼저 취소 -> 나중에 새로 붙임)를 없앤다.
            # 그 사이에 애매한 응답이 나면 포지션이 실제로 무보호로 남는 구간이
            # 있었다. 소유권이 이미 확인된(parent.protective_algo_ids) 그
            # algoId 하나를 amend_protective_stop()으로 그 자리에서 바꾼다 -
            # cxlOnFail=False라 이 요청이 어떤 이유로 실패해도(우리가 분류 못한
            # 이유 포함) 기존 보호주문 자체는 거래소 쪽에서 그대로 살아있다
            # (애초에 취소된 적이 없음).
            if len(parent.protective_algo_ids) != 1:
                return {"outcome": "rejected",
                        "reason": "amend_target_not_singular:" + repr(list(parent.protective_algo_ids))}
            ledger.mark_protection_pending(parent.intent_id)
            try:
                amend = client.amend_protective_stop(parent.protective_algo_ids[0], new_sl_price=intent.raw_stop_price)
            except ccxt.ExchangeError as exc:
                # okx_client.amend_protective_stop()이 이미 확정 거부로 분류해
                # raw ccxt 예외를 그대로 재던진 경우만 여기 도달한다(F3와 동일한
                # 계약) - cxlOnFail=False였으므로 기존 보호주문은 그대로다.
                return {"outcome": "rejected", "reason": f"{type(exc).__name__}: {exc}"[:500]}
            response["amend"] = amend
            if not amend["ok"]:
                # 거래소가 이 수정 요청 자체를 명시적으로 거부함(sCode) -
                # cxlOnFail=False라 기존 보호주문은 그대로다. cancel+attach로
                # 자동 전환하지 않는다(사용자 지시) - 다음 사이클이 조건이
                # 유지되면 새 STOP_UPDATE intent로 다시 시도한다.
                return {"outcome": "rejected", "reason": amend["reason"]}
            return {"outcome":"ack"}
        if intent.kind == dec.INTENT_REDUCE:
            response["order"] = client.reduce_position(position, quantity, client_order_id=action.cl_ord_id)
        else:
            response["order"] = client.close_position(position, client_order_id=action.cl_ord_id)
        order = response["order"] or {}
        return {"outcome":"ack" if order.get("id") else "unknown", "exchange_order_id":order.get("id")}
    submitted = ledger.attempt_submit(action.intent_id, submit)
    if submitted.reject_reason is not None:
        # R1 - 확정 거부(amend가 거래소에 절대 도달 안 했거나, 도달했지만
        # 명시적으로 거절됨)라 기존 보호주문은 손대지 않은 채 그대로 남아있다.
        # SAFE_HALT/critical 대상이 아니다 - 다음 사이클이 조건 유지 시 새
        # intent로 재시도한다(이 action 자체는 이미 terminal).
        return {"intent_kind": intent.kind, "executed": False,
                "reason": "management_action_rejected_existing_protection_preserved",
                "reject_reason": submitted.reject_reason}
    if submitted.state == il.IntentState.SUBMISSION_UNKNOWN.value:
        return _pending(intent, critical=True, intent_id=action.intent_id)
    return _finalize_managed(cfg, client, intent, ledger, epoch_store, parent, epoch,
                             position, before, quantity, action, response)


def _finalize_managed(cfg, client, intent, ledger, epoch_store, parent, epoch,
                      position, before, quantity, action, response):
    if intent.kind == dec.INTENT_STOP_UPDATE:
        # R1/R2(2026-09-17) - amend는 같은 algoId를 그 자리에서 바꾼 것이므로
        # (submit()에서 이미 소유권 확인된 그 id로만 호출했다) 새로 조건 매칭을
        # 할 필요가 없다 - 바로 그 algoId를 다시 조회해서 실제 slTriggerPx가
        # 목표대로 바뀌었는지만 확인한다(API 접수 응답이 아니라 실제 조회로
        # 확정, 사용자 지시).
        algo_id = parent.protective_algo_ids[0] if len(parent.protective_algo_ids) == 1 else None
        if algo_id is None:
            return _pending(intent, "protection_verification_failed", critical=True)
        market = client.exchange.market(client.symbol)
        price_tick = (market.get("precision") or {}).get("price")
        confirmed = None
        for attempt in range(PROTECTION_VERIFY_RETRIES):
            confirmed = client.fetch_protection_order_by_algo_id(algo_id)
            if (confirmed is not None
                    and order_safety._within_tolerance(confirmed["sl_price"], intent.raw_stop_price,
                                                        price_tick, order_safety.PRICE_TICK_TOLERANCE_MULTIPLE)):
                break
            if attempt < PROTECTION_VERIFY_RETRIES - 1:
                time.sleep(PROTECTION_VERIFY_RETRY_DELAY_SECONDS)
        if confirmed is None or not order_safety._within_tolerance(
                confirmed["sl_price"], intent.raw_stop_price, price_tick, order_safety.PRICE_TICK_TOLERANCE_MULTIPLE):
            # amend 응답은 성공(sCode=="0")이었는데 실제 조회로는 아직 목표값이
            # 아니거나(전파 지연) 그 algoId 자체가 사라짐(예: 그 사이 SL이
            # 이미 발동해 포지션이 청산됨) - 추측하지 않고 critical/pending으로
            # 남겨 다음 사이클/대사가 실제 포지션·체결 상태부터 다시 본다.
            return _pending(intent, "protection_verification_failed", critical=True)
        epoch.current_stop_price = intent.raw_stop_price
        if epoch.profit_lock_stop_price == intent.raw_stop_price:
            epoch.profit_lock_active = True
        epoch.protective_algo_ids = [algo_id]
        epoch_store.save(parent.intent_id, epoch)
        ledger.mark_protected(parent.intent_id, [algo_id]); ledger.mark_terminal(action.intent_id)
        return {"intent_kind":intent.kind,"executed":True,"protective_algo_ids":[algo_id]}
    after = client.fetch_position()
    if after is not None and (after.get("side") != position["side"] or not _positive(after.get("contracts"))):
        return _pending(intent, "position_drift_after_order", critical=True)
    if after is not None and (
        (epoch.exchange_position_id is not None and str(after.get("position_id")) != str(epoch.exchange_position_id))
        or (epoch.exchange_entry_timestamp_ms is not None and after.get("entry_timestamp_ms") != epoch.exchange_entry_timestamp_ms)
        or after.get("entry_price") != epoch.raw_entry_price
    ):
        return _pending(intent, "position_lifecycle_changed_after_order", critical=True)
    remaining = after["contracts"] if after else 0.
    reduced = before - remaining
    if reduced <= 0 or reduced > quantity + 1e-8:
        return _pending(intent, "fill_not_confirmed", remaining_contracts=remaining, critical=True)
    order = response.get("order") or {}
    if remaining:
        resolved = _resolve_candidate_c_reduce_pnl(cfg, client, intent.symbol, position,
            reduced * epoch.contract_size, reduce_order_id=order.get("id"))
        if _positive(order.get("average")):
            resolved["gross_pnl"] = (order["average"]-epoch.raw_entry_price) * reduced * epoch.contract_size * (1 if epoch.side=="long" else -1)
            resolved["exit_price"] = order["average"]
        trade_log.record_reduce(cfg.user_dir,intent.symbol,epoch.side,epoch.raw_entry_price,reduced,
            resolved["gross_pnl"],reason=intent.reason_code,dry_run=False,fee=resolved["fee"],
            pnl_source=resolved["source"],close_price=resolved.get("exit_price"),strategy_group="candidate_c",
            execution_id=action.intent_id)
        epoch.realized_partial_pnl_usdt += resolved["gross_pnl"]
        epoch.realized_partial_fee_usdt += resolved["fee"]
        epoch.entry_fee_usdt = _scale_remaining_entry_fee(epoch.entry_fee_usdt, before, remaining)
        epoch.remaining_contracts = remaining
        _apply_reduce_lifecycle_flags(epoch, intent.reason_code)
        if epoch.remaining_reserved_risk_usdt is not None:
            epoch.remaining_reserved_risk_usdt *= remaining / before
        if epoch.remaining_gross_notional_usdt is not None:
            epoch.remaining_gross_notional_usdt *= remaining / before
        epoch_store.save(parent.intent_id, epoch)
        ledger.mark_residual_quantity(parent.intent_id, remaining)
        ledger.mark_protection_pending(parent.intent_id)
        # R1(2026-09-17) - 여기도 취소 후 재부착이 아니라, 소유권이 이미 확인된
        # 그 algoId를 남은 수량으로 amend한다(cxlOnFail=False). 실패해도 기존
        # (전체 수량 기준) 보호주문 자체는 그대로 살아있다 - OKX reduceOnly
        # 주문은 실제 포지션 수량을 넘어 청산하지 못하므로, sz가 아직 갱신 전
        # (더 큰) 값이어도 실제로 과다청산되지는 않는다 - 다만 우리 장부와
        # 실제 상태가 어긋난 것 자체는 critical/pending으로 남겨 확인받는다.
        algo_id = parent.protective_algo_ids[0] if len(parent.protective_algo_ids) == 1 else None
        if algo_id is None:
            return _pending(intent,"residual_protection_target_not_singular",critical=True,
                remaining_contracts=remaining,reduced_contracts=reduced)
        try:
            amend = client.amend_protective_stop(algo_id, new_sz=remaining)
            if not amend["ok"]:
                raise RuntimeError(f"residual protection resize rejected: {amend['reason']}")
            market = client.exchange.market(client.symbol)
            amount_step = (market.get("precision") or {}).get("amount")
            confirmed = None
            for attempt in range(PROTECTION_VERIFY_RETRIES):
                confirmed = client.fetch_protection_order_by_algo_id(algo_id)
                if (confirmed is not None
                        and order_safety._within_tolerance(confirmed["sz"], remaining, amount_step,
                                                            order_safety.SIZE_STEP_TOLERANCE_MULTIPLE)):
                    break
                if attempt < PROTECTION_VERIFY_RETRIES - 1:
                    time.sleep(PROTECTION_VERIFY_RETRY_DELAY_SECONDS)
            if confirmed is None or not order_safety._within_tolerance(
                    confirmed["sz"], remaining, amount_step, order_safety.SIZE_STEP_TOLERANCE_MULTIPLE):
                raise RuntimeError("residual protection not verified")
            epoch.protective_algo_ids=[algo_id]
            if intent.kind == dec.INTENT_REDUCE and intent.reason_code not in (
                    "partial_take_profit_2r", "PARTIAL_TP_2R_DUST_SAFE_FULL_EXIT"):
                epoch.derisk_done = True
            epoch_store.save(parent.intent_id, epoch)
            ledger.mark_protected(parent.intent_id,[algo_id])
            ledger.mark_terminal(action.intent_id)
        except Exception as exc:
            return _pending(intent,"residual_protection_failed",critical=True,remaining_contracts=remaining,
                reduced_contracts=reduced,error_reason=str(exc))
        result = {"intent_kind":intent.kind,"executed":intent.kind==dec.INTENT_REDUCE,
            "pending":intent.kind!=dec.INTENT_REDUCE,"remaining_contracts":remaining,
            "reduced_contracts":reduced,"fee_source":resolved["fee_source"]}
        net_pnl = resolved.get("net_pnl")
        if net_pnl is None:
            net_pnl = resolved["gross_pnl"] - resolved["fee"]
        result["notification_event"] = _persist_notification_event(cfg, {
            "event_type": "reduce", "symbol": intent.symbol, "side": epoch.side,
            "entry_price": epoch.raw_entry_price, "price": resolved.get("exit_price"),
            "contracts": reduced, "amount_coin": reduced * epoch.contract_size,
            "remaining_contracts": remaining,
            "gross_pnl_usdt": resolved["gross_pnl"],
            "fee_usdt": resolved["fee"], "net_pnl_usdt": net_pnl,
            "reason": intent.reason_code, "intent_id": action.intent_id,
        })
        return result
    resolved = _resolve_candidate_c_close_pnl(
        client,
        {**position, "pre_close_unrealized_pnl": position.get("unrealized_pnl"),
         "position_id": epoch.exchange_position_id,
         "entry_timestamp_ms": epoch.exchange_entry_timestamp_ms},
        close_order_id=order.get("id"),
        expected_close_contracts=before,
        expected_total_contracts=epoch.original_contracts,
    )
    if resolved.get("source") != "okx_realized":
        # Flat is already authoritative; delayed OKX PnL history is accounting-only.
        return _pending(intent, "external_close_pnl_reconciliation_pending",
                        accounting_reconciliation_pending=True, remaining_contracts=0.)
    # Position-history PnL is cumulative across the full epoch, including reductions.
    if resolved.get("source") == "okx_realized":
        resolved = dict(resolved)
        resolved["gross_pnl"] -= epoch.realized_partial_pnl_usdt
        resolved["fee"] = max(0., resolved["fee"] - epoch.realized_partial_fee_usdt)
        if resolved.get("net_pnl") is not None:
            resolved["net_pnl"] -= epoch.realized_partial_pnl_usdt - epoch.realized_partial_fee_usdt
    trade_log.record_close(cfg.user_dir,intent.symbol,epoch.side,epoch.raw_entry_price,before,
        resolved["gross_pnl"],reason=intent.reason_code,dry_run=False,fee=resolved["fee"],
        pnl_source=resolved["source"],close_price=resolved.get("exit_price"),strategy_group="candidate_c",
        execution_id=action.intent_id,okx_net_pnl=resolved.get("net_pnl"),funding_fee=resolved.get("funding_fee"))
    # Flat confirmation also requires no residual orders, including our own bracket.
    ids=client.fetch_pending_protection_algo_ids()
    if ids is None or set(ids) - set(parent.protective_algo_ids):
        return _pending(intent,"foreign_protection_after_exit",critical=True,remaining_contracts=0.)
    if ids: client.cancel_protection(ids)
    remaining_algos=client.fetch_pending_protection_algo_ids()
    remaining_orders=client.exchange.fetch_open_orders(intent.symbol)
    if client.fetch_position() is not None or remaining_algos != [] or remaining_orders != []:
        return _pending(intent,"flat_cleanup_unconfirmed",critical=True,remaining_contracts=0.)
    if any(r.kind == dec.INTENT_STOP_UPDATE for r in ledger.pending_intents()):
        # Emergency exposure is closed, but a delayed attachment can still appear.
        # Keep its parent reservation and epoch until that action is reconciled.
        ledger.mark_terminal(action.intent_id)
        return _pending(intent,"flat_but_stop_submission_unknown",critical=True,
                        flat_observed=True,remaining_contracts=0.)
    ledger.mark_terminal(action.intent_id); ledger.mark_terminal(parent.intent_id)
    epoch_store.discard(parent.intent_id)
    event = _persist_notification_event(cfg, {
        "event_type": "close", "symbol": intent.symbol, "side": epoch.side,
        "entry_price": epoch.raw_entry_price, "price": resolved.get("exit_price"),
        "contracts": before, "amount_coin": before * epoch.contract_size,
        "gross_pnl_usdt": resolved["gross_pnl"],
        "fee_usdt": resolved["fee"],
        "net_pnl_usdt": resolved.get("net_pnl"),
        "funding_fee_usdt": resolved.get("funding_fee"),
        "reason": intent.reason_code, "intent_id": action.intent_id,
    })
    return {"intent_kind":intent.kind,"executed":True,"flat_confirmed":True,
            "remaining_contracts":0.,"pnl_source":resolved["source"],
            "notification_event": event}


def reconcile_pending_management(cfg, client, ledger, epoch_store):
    """Finish only the original persisted action after authoritative exchange reads.

    An absent order or bracket remains pending; this function never resubmits the
    market order. A confirmed reduce may resize the still-owned old protection.
    """
    from types import SimpleNamespace
    pending = {"reconciled":False,"pending":True,"critical":True}
    if not getattr(cfg,"CANDIDATE_C_LIVE_EXECUTE",False):
        return {"reconciled":False,"pending":False,"shadow":True}
    with ownership.account_order_lock(cfg.user_dir):
        ledger.refresh()
        actions=[r for r in ledger.pending_intents() if r.kind != dec.INTENT_ENTRY]
        if not actions:
            return {"reconciled":False,"pending":False}
        if len(actions)!=1:
            return pending
        action=actions[0]
        parent_id=(action.position_epoch or "").split(":",1)[0]
        try:
            parent=ledger.get(parent_id)
            epoch=epoch_store.get(parent_id)
            position=client.fetch_position()
            ids=client.fetch_pending_protection_algo_ids()
            orders=client.exchange.fetch_open_orders(action.symbol)
            if (parent.kind != dec.INTENT_ENTRY or parent.strategy_id!="candidate_c"
                    or parent.account_id!=action.account_id or parent.symbol!=action.symbol
                    or getattr(client,"symbol",action.symbol)!=action.symbol
                    or epoch.entry_intent_id!=parent_id or epoch.account_id!=parent.account_id
                    or epoch.symbol!=parent.symbol or orders != [] or ids is None):
                return pending
            if position is None:
                # The cycle's exact-position-history close reconciler owns flat accounting.
                return {**pending,"flat_observed":True}
            if (position.get("side")!=epoch.side
                    or (epoch.exchange_position_id is None and epoch.exchange_entry_timestamp_ms is None)
                    or (epoch.exchange_position_id is not None and str(position.get("position_id"))!=str(epoch.exchange_position_id))
                    or (epoch.exchange_entry_timestamp_ms is not None and position.get("entry_timestamp_ms")!=epoch.exchange_entry_timestamp_ms)
                    or position.get("entry_price")!=epoch.raw_entry_price):
                return pending
            request=SimpleNamespace(kind=action.kind,side=epoch.side,symbol=action.symbol,
                raw_stop_price=action.requested_stop_price,reason_code="reconciled_"+action.kind)
            if action.kind==dec.INTENT_STOP_UPDATE:
                if position["contracts"]!=epoch.remaining_contracts:
                    return pending
                result=_finalize_managed(cfg,client,request,ledger,epoch_store,parent,epoch,
                    position,epoch.remaining_contracts,action.requested_quantity,action,{})
                return {**result,"reconciled":bool(result.get("executed")),"pending":not result.get("executed",False)}
            order=client.fetch_order_status_by_client_id(action.cl_ord_id)
            if not order or order.get("status") not in ("closed","canceled","cancelled","rejected","filled"):
                return pending
            filled=order.get("filled")
            if filled==0 and position["contracts"]==epoch.remaining_contracts:
                ledger.mark_terminal(action.intent_id)
                return {"reconciled":True,"pending":False,"remaining_contracts":epoch.remaining_contracts}
            if not _positive(filled) or filled>action.requested_quantity:
                return pending
            if position["contracts"]==epoch.remaining_contracts:
                # Accounting completed before an ambiguous residual bracket replacement.
                if parent.state!=il.IntentState.PROTECTION_PENDING.value:
                    return pending
                # R2(2026-09-17) - amend 기반 resize는 algoId가 안 바뀐다. "가격/
                # 방향/수량이 비슷한 게 하나 있다"가 아니라, reduce 이전부터 이미
                # 소유권이 확인돼 있던 그 algoId(parent.protective_algo_ids)가
                # 지금 실제로 남은 수량으로 조회되는지만 확인한다 - 조건만
                # 같은 남의 주문을 편입하지 않는다.
                algo_id = parent.protective_algo_ids[0] if len(parent.protective_algo_ids) == 1 else None
                if algo_id is None:
                    return pending
                confirmed = client.fetch_protection_order_by_algo_id(algo_id)
                if confirmed is None:
                    return pending
                market = client.exchange.market(client.symbol)
                amount_step = (market.get("precision") or {}).get("amount")
                if not order_safety._within_tolerance(confirmed["sz"], epoch.remaining_contracts,
                                                       amount_step, order_safety.SIZE_STEP_TOLERANCE_MULTIPLE):
                    return pending
                epoch.protective_algo_ids=[algo_id]
                epoch.derisk_done=action.kind==dec.INTENT_REDUCE or epoch.derisk_done
                epoch_store.save(parent_id,epoch)
                ledger.mark_protected(parent_id,[algo_id]);ledger.mark_terminal(action.intent_id)
                result = {"reconciled":True,"pending":False,
                          "remaining_contracts":epoch.remaining_contracts}
                try:
                    event = notification_delivery.trade_event(
                        cfg.user_dir, "reduce", action.intent_id, epoch,
                        remaining_contracts=epoch.remaining_contracts,
                    )
                except Exception:
                    logger_ = getattr(cfg, "logger", None)
                    if logger_ is not None:
                        logger_.warning(
                            "[%s] Candidate C 축소 알림 복원 실패",
                            client.symbol, exc_info=True,
                        )
                    event = None
                if event is not None:
                    result["notification_event"] = event
                return result
            if (abs(epoch.remaining_contracts-position["contracts"]-filled)>1e-8
                    or set(ids)-set(parent.protective_algo_ids)):
                return pending
            prior={**position,"contracts":epoch.remaining_contracts,"raw_entry_price":epoch.raw_entry_price}
            result=_finalize_managed(cfg,client,request,ledger,epoch_store,parent,epoch,
                prior,epoch.remaining_contracts,action.requested_quantity,action,{"order":order})
            return {**result,"reconciled":not result.get("critical",False),"pending":bool(result.get("critical",False))}
        except Exception as exc:
            return {**pending,"error_reason":str(exc)}
