"""Phase 3.6 Part F / Phase 3.9 - 시작 시·매 cycle 기존 포지션/주문 대사
(read-only). 실제 프로덕션 계정을 조회하지 않는다 - 호출부가 이미
조회해서 넘겨준 스냅샷(fixture 또는 fake exchange 결과)만 다룬다. 이
모듈은 아무 것도 취소/청산하지 않는다 - "이 심볼에 Candidate C 신규진입을
허용할지"와 "지금 이 심볼의 Candidate C 포지션이 정확히 무엇인가"만
판정한다.

Phase 3.9 - current_position=None 하드코딩을 없애기 위해, internal_trade_log_record
는 이제 candidate_c_intent_ledger.py(누가 이 포지션을 열었는지 - clOrdId/
setup_id/config)와 candidate_c_exit_management.PositionEpochStore(이 포지션의
현재 stop/high_water/누적 funding/실현 부분손익)를 조합해 실제로 구성한다
(build_internal_trade_log_record/build_candidate_c_position_state 참고).
새 안전 모듈이 아니라 Task 5/Part D/Part F를 조합하는 통합 계층이다."""
from __future__ import annotations

from dataclasses import dataclass
import math

STATUS_ADOPTED = "adopted"
STATUS_NO_POSITION = "no_position"
STATUS_LEGACY_BLOCKED = "LEGACY_POSITION_BLOCKED"
STATUS_RECONCILE_REQUIRED = "POSITION_RECONCILE_REQUIRED"

# Phase 3.10 / Phase 3.10.1 갱신 - Candidate C 자신이 이미 ADOPTED로 인식하고
# 있던 포지션이 이번 cycle에 더 이상 거래소 스냅샷과 일치하지 않을 때의
# typed outcome. 절대 추측하지 않는다 - 근거가 있을 때만 SL/TP/manual로
# 확정하고, 없으면 UNCLASSIFIED로 남겨 사람이 확인할 때까지 신규진입만
# 차단한다(기존 포지션 보호/청산에는 영향 없음).
#
# Phase 3.10.1 - "OKX가 어느 bracket leg인지 안 알려준다"고 단정하지 않는다.
# 실제 설치된 ccxt(4.5.74)의 okx.py가 GET /api/v5/trade/orders-algo-history
# 응답 스키마를 문서화하고 있고, 이 응답은 실제 체결가(actualPx)와 원래
# 설정된 slTriggerPx/tpTriggerPx를 같은 레코드에 함께 담고 있다(okx_client.
# fetch_algo_order_history() 참고, 실제 ccxt request-capture로 검증됨) -
# 이 "우리가 소유한 algoId의 실제 이력"과 직접 비교해서 판정하면 EXACT,
# 그 이력 조회가 실패/모호하면 우리 내부에 기록된 sl/tp 값과 (별도로
# 조회한) 실현 청산가를 비교하는 기존 방식으로 폴백하면 INFERRED다 - 둘을
# 절대 같은 신뢰도로 기록하지 않는다.
EXTERNAL_SL_FILLED_EXACT = "EXTERNAL_SL_FILLED_EXACT"
EXTERNAL_TP_FILLED_EXACT = "EXTERNAL_TP_FILLED_EXACT"
EXTERNAL_SL_FILLED_INFERRED = "EXTERNAL_SL_FILLED_INFERRED"
EXTERNAL_TP_FILLED_INFERRED = "EXTERNAL_TP_FILLED_INFERRED"
MANUAL_OR_EXTERNAL_CLOSE = "MANUAL_OR_EXTERNAL_CLOSE"
EXTERNAL_PARTIAL_REDUCE = "EXTERNAL_PARTIAL_REDUCE"
POSITION_DRIFT = "POSITION_DRIFT"
UNCLASSIFIED_EXTERNAL_CHANGE = "UNCLASSIFIED_EXTERNAL_CHANGE"

# 이 값들에 finalize/청산 로직이 의존하면 안 된다 - "SL이었는지 TP였는지"는
# 사후 분류일 뿐, flat 확정·회계 종료(risk 해제/journal/ledger terminal)는
# 이 값과 무관하게 항상 똑같이 수행된다(candidate_c_trader_adapter.
# _finalize_candidate_c_external_close 참고).
EXTERNAL_CLOSE_OUTCOMES = (
    EXTERNAL_SL_FILLED_EXACT, EXTERNAL_TP_FILLED_EXACT,
    EXTERNAL_SL_FILLED_INFERRED, EXTERNAL_TP_FILLED_INFERRED,
    MANUAL_OR_EXTERNAL_CLOSE,
)

# trader.py의 CORE 자신의 외부청산 판정(_classify_close_reason)이 이미 쓰는
# 값과 정확히 같다 - INFERRED 경로(우리 내부 기록 vs 재구성된 청산가 비교)에만
# 적용한다. EXACT 경로(OKX 자신의 algo-history actualPx vs slTriggerPx/
# tpTriggerPx)는 별도의, 더 엄격한 상대 마진(_EXACT_AMBIGUITY_MARGIN_PCT)을
# 쓴다 - 두 트리거가 서로 가까운 전략에서 근소한 차이로 억지 판정하지
# 않기 위함이다.
CLOSE_PRICE_TOLERANCE = 0.003
_EXACT_AMBIGUITY_MARGIN_PCT = 0.001

# Phase 3.9 - 정확히 일치해야 하는 필드(사용자 지시 그대로): account/symbol/side/
# size는 SymbolSnapshot/AccountSnapshot 레벨에서 이미 확인되고, 아래는 내부
# 기록(internal_trade_log_record)에 반드시 있어야 하는 추가 식별자다.
REQUIRED_INTERNAL_RECORD_KEYS = (
    "side", "contracts", "protective_algo_ids", "entry_intent_id",
    "position_epoch", "config_version_id",
)


@dataclass(frozen=True)
class SymbolSnapshot:
    symbol: str
    exchange_position: dict | None  # {"side":, "contracts":, "entry_price":} or None
    exchange_open_orders: list  # entry/exit 미체결 주문
    exchange_protective_algo_ids: list  # 실제 거래소 OCO algoId들
    internal_trade_log_record: dict | None  # build_internal_trade_log_record()가 만든 dict, 또는 None(내부 기록 없음)
    internal_risk_reservation: float | None  # 내부 리스크 원장에 기록된 예약액(None=예약 없음)
    internal_state_ambiguous: bool = False  # ledger에 RECONCILE_REQUIRED/SUBMISSION_UNKNOWN intent가 있는 등 확정 불가


@dataclass(frozen=True)
class AccountSnapshot:
    account_identity_matches: bool
    wallet_query_succeeded: bool
    position_mode_matches: bool  # 예상 position mode(예: net/hedge)와 실제가 일치하는지
    account_risk_ledger_consistent: bool


def reconcile_symbol_at_startup(snap: SymbolSnapshot) -> dict:
    """내부 기록과 거래소 상태를 대사한다. 정확히 일치해야만 adopt - 조금이라도
    불명확하면 LEGACY_POSITION_BLOCKED(확정적으로 다름) 또는
    POSITION_RECONCILE_REQUIRED(확정 불가 - 예: ledger가 아직 결론 안 남)로
    분류한다. 두 경우 모두 신규진입만 차단하고 기존 포지션/주문은 절대
    건드리지 않는다."""
    if snap.internal_state_ambiguous:
        return {"status": STATUS_RECONCILE_REQUIRED, "reason": "internal_ledger_state_ambiguous"}

    if snap.exchange_position is None:
        if snap.exchange_open_orders or snap.internal_trade_log_record is not None:
            return {"status": STATUS_LEGACY_BLOCKED, "reason": "position_flat_but_orphan_orders_or_internal_record"}
        return {"status": STATUS_NO_POSITION, "reason": None}

    if snap.internal_trade_log_record is None:
        return {"status": STATUS_LEGACY_BLOCKED, "reason": "exchange_position_exists_without_internal_record"}

    pos = snap.exchange_position
    rec = snap.internal_trade_log_record
    missing_keys = [k for k in REQUIRED_INTERNAL_RECORD_KEYS if k not in rec]
    if missing_keys:
        return {"status": STATUS_RECONCILE_REQUIRED, "reason": f"internal_record_missing_keys:{missing_keys}"}
    if pos["side"] != rec.get("side"):
        return {"status": STATUS_LEGACY_BLOCKED, "reason": "side_mismatch"}
    if pos["contracts"] != rec.get("contracts"):
        return {"status": STATUS_LEGACY_BLOCKED, "reason": "size_mismatch"}
    if not rec.get("entry_intent_id"):
        return {"status": STATUS_LEGACY_BLOCKED, "reason": "no_candidate_c_intent_identity"}
    if not rec.get("config_version_id"):
        return {"status": STATUS_LEGACY_BLOCKED, "reason": "no_config_version_identity"}

    recorded_algo_ids = set(rec.get("protective_algo_ids") or [])
    actual_algo_ids = set(snap.exchange_protective_algo_ids)
    if not recorded_algo_ids or recorded_algo_ids != actual_algo_ids:
        return {"status": STATUS_LEGACY_BLOCKED, "reason": "protective_algo_id_mismatch"}

    if snap.internal_risk_reservation is None:
        return {"status": STATUS_LEGACY_BLOCKED, "reason": "risk_reservation_missing"}
    if (not isinstance(snap.internal_risk_reservation, (int, float))
            or not math.isfinite(float(snap.internal_risk_reservation))):
        return {"status": STATUS_RECONCILE_REQUIRED, "reason": "risk_reservation_nonfinite"}
    if snap.internal_risk_reservation <= 0:
        return {"status": STATUS_LEGACY_BLOCKED, "reason": "risk_reservation_missing"}

    return {"status": STATUS_ADOPTED, "reason": None}


def build_internal_trade_log_record(entry_record, epoch) -> dict:
    """Phase 3.9 - durable intent ledger의 PROTECTED EntryIntent(entry_record,
    candidate_c_intent_ledger.IntentRecord) + PositionEpochStore의 확장
    상태(epoch, candidate_c_exit_management.PositionEpochState)를 조합해
    reconcile_symbol_at_startup()이 기대하는 internal_trade_log_record를
    만든다. 실제 값을 지어내지 않는다 - 둘 다 없으면 이 함수 자체를
    호출하면 안 된다(호출부가 find_protected_entry()로 먼저 확인)."""
    return {
        "side": entry_record.requested_side,
        "contracts": epoch.remaining_contracts if epoch.remaining_contracts is not None else entry_record.filled_quantity,
        "protective_algo_ids": entry_record.protective_algo_ids,
        "entry_intent_id": entry_record.intent_id,
        "position_epoch": entry_record.intent_id,
        "config_version_id": entry_record.config_version_id,
        "config_hash": entry_record.config_hash,
        "attach_algo_cl_ord_id": entry_record.attach_algo_cl_ord_id,
    }


def build_candidate_c_position_state(epoch) -> dict:
    """candidate_c_decision_engine.decide()의 current_position 스키마
    그대로 반환한다(candidate_c_decision_engine.py/candidate_c_backtest_
    signal_adapter.py와 동일한 필드 이름·의미) - reconcile 결과가
    STATUS_ADOPTED일 때만 호출해야 한다."""
    return {
        "side": epoch.side, "position_id": epoch.entry_intent_id, "contracts": epoch.remaining_contracts,
        "raw_entry_price": epoch.raw_entry_price, "initial_stop_price": epoch.initial_stop_price,
        "high_water": epoch.high_water, "effective_entry_price": epoch.effective_entry_price,
        "entry_fee_usdt": epoch.entry_fee_usdt, "contract_size": epoch.contract_size,
        "fee_rate": epoch.fee_rate, "spread_bps": epoch.spread_bps, "slippage_bps": epoch.slippage_bps,
    }


def _classify_from_algo_history(algo_history: dict | None) -> str | None:
    """Phase 3.10.1 - OKX 자신의 algo-order-history 레코드(우리가 소유한
    algoId로 조회됨, okx_client.fetch_algo_order_history() 참고)에서
    직접 판정을 시도한다. 이 레코드 자체가 실제로 발동(effective)했다는
    증거이고, 그 안의 actualPx(실제 체결가)를 같은 레코드의 slTriggerPx/
    tpTriggerPx와 비교한다 - 우리 내부 기록이 아니라 OKX가 실제로 기록한
    값끼리 비교하므로 EXACT다. 증거가 없거나(취소된 레코드 등) actualPx가
    없거나, 두 트리거 가격에서 거리가 실질적으로 구분 안 되면(예: 두
    트리거가 서로 아주 가까운 전략) None을 반환해 호출부가 INFERRED
    경로로 폴백하게 한다 - 절대 억지로 EXACT를 만들어내지 않는다."""
    if algo_history is None:
        return None
    if algo_history.get("state") not in ("effective", "partially_effective"):
        return None  # 취소 등 - 실제로 발동했다는 증거가 아님

    def _to_float(value) -> float | None:
        try:
            f = float(value)
        except (TypeError, ValueError):
            return None
        return f if f > 0 else None

    actual_px = _to_float(algo_history.get("actualPx"))
    if actual_px is None:
        return None
    sl_trigger = _to_float(algo_history.get("slTriggerPx"))
    tp_trigger = _to_float(algo_history.get("tpTriggerPx"))
    if sl_trigger is None and tp_trigger is None:
        return None

    dist_sl = abs(actual_px - sl_trigger) if sl_trigger is not None else None
    dist_tp = abs(actual_px - tp_trigger) if tp_trigger is not None else None
    if dist_sl is not None and dist_tp is not None:
        reference = max(sl_trigger, tp_trigger, 1e-9)
        if abs(dist_sl - dist_tp) < reference * _EXACT_AMBIGUITY_MARGIN_PCT:
            # 실제 체결가가 두 트리거 가격 모두와 실질적으로 비슷하게
            # 가깝다 - 어느 쪽이 발동했는지 이 증거만으로는 구분할 수
            # 없으므로 추측하지 않는다.
            return None
        return EXTERNAL_SL_FILLED_EXACT if dist_sl < dist_tp else EXTERNAL_TP_FILLED_EXACT
    if dist_sl is not None:
        return EXTERNAL_SL_FILLED_EXACT
    return EXTERNAL_TP_FILLED_EXACT


def classify_external_position_change(
    *, epoch, exchange_position: dict | None, owned_algo_live: bool | None, exit_price: float | None,
    algo_history: dict | None = None,
) -> str:
    """Phase 3.10 / Phase 3.10.1 - reconcile_symbol_at_startup()이 이미
    ADOPTED가 아니라고 판정했고, 그 사유가 "거래소 상태가 구조적으로
    달라짐"(side/size mismatch, 또는 flat인데 내부 기록이 남아있음)일
    때만 호출부가 이 함수를 부른다(호출부, candidate_c_trader_adapter.
    _reconcile_candidate_c_position 참고). 이 함수 자체는 client를 전혀
    모른다 - 이미 조회된 값만 받는다(이 모듈의 기존 원칙 그대로).

    우선순위: (1) algo_history가 있고 실제로 판정 가능하면 EXACT_*,
    (2) 없거나 모호하면 exit_price와 우리 내부 sl/tp를 비교하는 INFERRED_*,
    (3) 그마저 안 되면 MANUAL_OR_EXTERNAL_CLOSE(체결 사실 자체는 확실하나
    이유가 불명확), (4) 위 모든 근거가 모순되거나 부족하면
    UNCLASSIFIED_EXTERNAL_CHANGE. 어느 경우든 "포지션이 flat이 됐다"는
    사실 자체(회계 종료 대상 여부)는 이 분류와 완전히 독립적이다 - 호출부가
    이미 exchange_position is None으로 flat을 확정한 뒤에만 이 분류 결과를
    reason으로만 참고한다(청산 여부 자체를 바꾸지 않음).

    owned_algo_live: 우리가 소유한 단일 결합 브라켓(protective_algo_ids)이
    아직 "live" 상태인 algo 중 하나라도 있는지. None이면 조회 자체가
    안 됐다는 뜻(불확실 - 추측하지 않음).
    exit_price: position이 flat일 때만 의미가 있다 - client.fetch_last_
    realized_close()가 실제로 찾아낸 청산가(없으면 None, 절대 추정치를
    실현가인 것처럼 넘기지 않는다 - 호출부 책임)."""
    if exchange_position is None:
        # 완전히 flat이 됨 - SL/TP/수동청산 중 하나여야 한다.
        if owned_algo_live is None:
            # 우리 브라켓 상태 자체를 조회하지 못함 - 근거 없이 추측하지 않는다.
            return UNCLASSIFIED_EXTERNAL_CHANGE
        if owned_algo_live:
            # 포지션은 flat인데 우리 소유 브라켓은 아직 "live" - OKX의 autoCxl
            # 기본값(false) 때문에 브라켓이 스스로 발동한 게 아니라 다른
            # 경로(수동 청산 등)로 포지션이 닫혔을 때 브라켓만 그대로 남는
            # 정상적인 상태다(모순이 아니다). 우리 브라켓이 발동하지 않았다는
            # 확실한 근거이므로 SL/TP로 볼 수 없다 - 브라켓 정리(cleanup)는
            # 호출부(_reconcile_candidate_c_position -> OCO cleanup_fn)가
            # 별도로 처리한다.
            return MANUAL_OR_EXTERNAL_CLOSE

        exact = _classify_from_algo_history(algo_history)
        if exact is not None:
            return exact

        if exit_price is not None:
            if (epoch.current_stop_price is not None and epoch.current_stop_price > 0
                    and abs(exit_price - epoch.current_stop_price) / epoch.current_stop_price <= CLOSE_PRICE_TOLERANCE):
                return EXTERNAL_SL_FILLED_INFERRED
            if (epoch.target_price is not None and epoch.target_price > 0
                    and abs(exit_price - epoch.target_price) / epoch.target_price <= CLOSE_PRICE_TOLERANCE):
                return EXTERNAL_TP_FILLED_INFERRED
        return MANUAL_OR_EXTERNAL_CLOSE

    # 포지션이 아직 남아있음 - side/수량을 비교한다.
    if exchange_position.get("side") != epoch.side:
        return POSITION_DRIFT
    exch_contracts = exchange_position.get("contracts")
    if exch_contracts is None or epoch.remaining_contracts is None:
        return UNCLASSIFIED_EXTERNAL_CHANGE
    if 0 < exch_contracts < epoch.remaining_contracts:
        # side가 그대로고 수량만 일관되게 줄었다는 관측 자체를 "부분 축소가
        # 실제로 있었다"는 근거로 삼는다 - OKX가 어떤 fill이 이걸 만들었는지
        # 별도로 확인해 주지 않으므로(위 SL/TP 판별과 같은 한계), 내부
        # 수량을 조용히 덮어쓰는 대신 이 관측 자체를 근거로 명시한다.
        return EXTERNAL_PARTIAL_REDUCE
    return POSITION_DRIFT


def should_auto_release_block(snap: SymbolSnapshot) -> bool:
    """block된 심볼이 다음 tick부터 자동 해제되는 조건 - flat이고 관련 주문이
    전부 정리됐을 때만."""
    return snap.exchange_position is None and not snap.exchange_open_orders


def check_account_wide_gate(acct: AccountSnapshot) -> dict:
    """네 종목 신규진입 전체를 막아야 하는지 판정한다(계좌 단위 불일치)."""
    if not acct.account_identity_matches:
        return {"blocked": True, "reason": "account_identity_mismatch"}
    if not acct.wallet_query_succeeded:
        return {"blocked": True, "reason": "wallet_query_failed"}
    if not acct.position_mode_matches:
        return {"blocked": True, "reason": "position_mode_mismatch"}
    if not acct.account_risk_ledger_consistent:
        return {"blocked": True, "reason": "account_risk_ledger_inconsistent"}
    return {"blocked": False, "reason": None}
