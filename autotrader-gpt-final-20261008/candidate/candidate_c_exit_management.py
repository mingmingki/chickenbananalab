"""Phase 3.5 Task 5 - Candidate C 포지션 관리(초기 stop/trailing/profit-lock/
1H 구조적 de-risk/4H 무효화 full exit) 순수 판단 함수 모음.

candidate_c_preregistration_v2.json 6/7의 공식을 그대로 코드화한다. 이 모듈은
실제 주문 실행이나 엔진 이벤트 루프에 직접 붙지 않는다 - 상태(포지션 스냅샷,
epoch 플래그)를 입력받아 "이번 5분 action clock에 무엇을 해야 하는가"만
결정하는 순수 함수만 제공한다. ATR 배수(3.5/6.0)는 기존 Candidate B-B3 값을
그대로 재사용한다(재최적화 아님)."""
from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field

import execution_units as eu

ATR_PERIOD = 14
INITIAL_STOP_ATR_MULT = 3.5
TRAILING_ATR_MULT = 6.0


@dataclass
class PositionEpochState:
    """포지션 생애주기(진입~완전청산) 동안 유지되는 확장 상태.

    Phase 3.9 - current_position=None 하드코딩을 제거하기 위해, 이 epoch
    상태에 "실제로 지금 이 포지션이 무엇인가"를 재구성하는 데 필요한
    필드를 전부 추가한다(포지션 진입~완전청산 동안 유지되는 유일한
    영속 저장소이므로 - 새 모듈을 만들지 않고 기존 Task 5 저장소를
    확장한다). candidate_c_position_reconciliation.py가 이 상태 +
    durable intent ledger + 실제 거래소 스냅샷을 대사해 typed
    CandidateCPositionState를 만든다."""
    derisk_done: bool = False
    partial_take_profit_done: bool = False
    mfe_profit_reduce_done: bool = False
    profit_lock_active: bool = False
    profit_lock_stop_price: float | None = None
    weakening_prev: bool = False
    pilot_entry: bool = False
    pilot_4h_confirmed: bool = False
    last_action_tick_ms: int | None = None
    execution_mode_at_open: str | None = None
    # --- Phase 3.9 신규 필드 ---
    account_id: str | None = None
    symbol: str | None = None
    side: str | None = None
    entry_intent_id: str | None = None  # durable intent ledger의 진입 intent_id(=clOrdId) - 이 값 자체가 position_id다
    setup_id: str | None = None
    config_version_id: str | None = None
    config_hash: str | None = None
    entry_time_ms: int | None = None
    exchange_position_id: str | None = None
    exchange_entry_timestamp_ms: int | None = None
    raw_entry_price: float | None = None
    effective_entry_price: float | None = None
    entry_fee_usdt: float = 0.0
    contract_size: float | None = None
    lot_step: float | None = None
    min_contracts: float | None = None
    tick_size: float | None = None
    max_contracts: float | None = None
    fee_rate: float | None = None
    spread_bps: float | None = None
    slippage_bps: float | None = None
    original_contracts: float | None = None
    remaining_contracts: float | None = None
    initial_stop_price: float | None = None  # 진입 시점에 고정, 이후 절대 갱신 안 함(price_R 계산용, preregistration 계약)
    current_stop_price: float | None = None  # 실제 거래소에 설치된 현재 stop(trailing/profit-lock으로 갱신됨)
    target_price: float | None = None  # 진입 시점에 고정된 TP(있으면) - Phase 3.10 외부청산 SL/TP 판별에 사용
    high_water: float | None = None
    accumulated_funding_usdt: float = 0.0
    realized_partial_pnl_usdt: float = 0.0
    realized_partial_fee_usdt: float = 0.0
    remaining_reserved_risk_usdt: float | None = None
    remaining_gross_notional_usdt: float | None = None
    reservation_id: str | None = None
    protective_algo_ids: list = field(default_factory=list)
    attach_algo_cl_ord_id: str | None = None
    protective_order_type: str | None = None
    # Immutable snapshot from the config version that opened the position.
    # Management after restart/config change must continue with these values.
    strategy_policy: dict | None = None
    adaptive_exit_policy: dict | None = None
    adaptive_exit_policy_hash: str | None = None


def observe_pilot_4h_confirmation(epoch, *, side, direction_4h):
    if epoch.pilot_entry and direction_4h == ("LONG" if side == "long" else "SHORT"):
        epoch.pilot_4h_confirmed = True


def capture_adaptive_exit_policy_snapshot(epoch: PositionEpochState, policy: dict, policy_hash: str) -> PositionEpochState:
    """Capture once at entry; restart/config reload may never replace an existing snapshot."""
    if epoch.adaptive_exit_policy_hash is None:
        epoch.adaptive_exit_policy = dict(policy)
        epoch.adaptive_exit_policy_hash = str(policy_hash)
    return epoch


def compute_initial_stop_price(
    *, side: str, entry_price: float, atr_4h: float,
    atr_multiplier: float = INITIAL_STOP_ATR_MULT,
) -> float:
    if side == "long":
        return entry_price - atr_multiplier * atr_4h
    if side == "short":
        return entry_price + atr_multiplier * atr_4h
    raise ValueError(f"알 수 없는 side: {side}")


def compute_trailing_stop_price(
    *, side: str, high_or_low_water: float, atr_4h: float,
    atr_multiplier: float = TRAILING_ATR_MULT,
) -> float:
    if side == "long":
        return high_or_low_water - atr_multiplier * atr_4h
    if side == "short":
        return high_or_low_water + atr_multiplier * atr_4h
    raise ValueError(f"알 수 없는 side: {side}")


def price_R(raw_entry_price: float, initial_stop_price: float) -> float:
    return abs(raw_entry_price - initial_stop_price)


def profit_lock_activation_check(
    *, side: str, raw_entry_price: float, price_r: float, confirmed_5m_close: float,
    activation_r: float = 1.0,
) -> bool:
    """확정 5분봉 종가가 포지션에 유리한 방향으로 +1R 이상 도달했는가."""
    if price_r <= 0:
        return False
    if side == "long":
        return confirmed_5m_close >= raw_entry_price + activation_r * price_r
    return confirmed_5m_close <= raw_entry_price - activation_r * price_r


def compute_profit_lock_stop_price(
    *, side: str, effective_entry_price: float, entry_fee_usdt: float,
    contracts: float, contract_size: float, fee_rate: float, spread_bps: float, slippage_bps: float,
) -> float:
    """"예상 stop 청산 net_pnl >= 0"을 만족하는 raw stop price를 정확히
    역산한다(그 지점에서 net_pnl이 정확히 0, 그보다 유리한 방향이면 이익).
    entry fee/예상 exit fee/spread/slippage 전부 반영한다. funding은 사전에
    알 수 없으므로 제외한다(사이징 공식의 planned_risk와 동일한 원칙 -
    cost_accounting.py의 execution_adjusted_pnl/entry_fee/exit_fee 계약과
    정확히 같은 식을 대수적으로 풀어서 얻은 닫힌 형태 해다)."""
    qty = contracts * contract_size
    if qty <= 0:
        raise ValueError("contracts*contract_size는 0보다 커야 함")
    k = (spread_bps + slippage_bps) / 10000.0
    if side == "long":
        effective_exit = (qty * effective_entry_price + entry_fee_usdt) / (qty * (1 - fee_rate))
        raw_exit = effective_exit / (1 - k)
    elif side == "short":
        effective_exit = (qty * effective_entry_price - entry_fee_usdt) / (qty * (1 + fee_rate))
        raw_exit = effective_exit / (1 + k)
    else:
        raise ValueError(f"알 수 없는 side: {side}")
    return raw_exit


def floor_to_lot_step(quantity: float, lot_step: float) -> float:
    return eu.round_to_step(quantity, lot_step, mode="down")


@dataclass
class DeriskDecision:
    action: str  # "none" | "partial_reduce" | "dust_full_exit"
    target_residual: float | None = None
    reduce_quantity: float | None = None
    reason: str | None = None


def evaluate_1h_structural_derisk(
    *, current_quantity: float, lot_step: float, min_size: float,
    weakening_prev: bool, weakening_now: bool, epoch: PositionEpochState,
    derisk_pct: float = 50.0,
) -> DeriskDecision:
    """보유 후 weakening이 처음 False->True가 된 그 순간에만 발동(포지션
    epoch당 1회). 회복(True->False) 이후 다시 True가 돼도 이미 derisk_done이면
    재발동하지 않는다 - 호출부가 epoch.derisk_done을 이 함수 밖에서 True로
    갱신해야 한다(이 함수는 순수 함수라 상태를 직접 바꾸지 않는다)."""
    if epoch.derisk_done:
        return DeriskDecision(action="none", reason="already_done_this_epoch")
    if not (weakening_now and not weakening_prev):
        return DeriskDecision(action="none", reason="no_new_weakening_edge")

    if not math.isfinite(derisk_pct) or not 0 < derisk_pct < 100:
        raise ValueError("derisk_pct must be between 0 and 100 percent")
    target_residual = floor_to_lot_step(
        current_quantity * (1.0 - derisk_pct / 100.0), lot_step,
    )
    reduce_quantity = current_quantity - target_residual
    if target_residual < min_size or reduce_quantity <= 0:
        return DeriskDecision(
            action="dust_full_exit", reduce_quantity=current_quantity, reason="DUST_SAFE_FULL_EXIT",
        )
    return DeriskDecision(action="partial_reduce", target_residual=target_residual, reduce_quantity=reduce_quantity)


def evaluate_partial_take_profit(
    *, side: str, current_quantity: float, original_quantity: float | None,
    lot_step: float, min_size: float, raw_entry_price: float, price_r: float,
    confirmed_5m_close: float, epoch: PositionEpochState,
    activation_r: float = 2.0, take_profit_pct: float = 25.0,
) -> DeriskDecision:
    """One-time +2R partial realization using 25% of original entry quantity."""
    if epoch.partial_take_profit_done:
        return DeriskDecision(action="none", reason="partial_take_profit_already_done")
    if not original_quantity or original_quantity <= 0 or price_r <= 0:
        return DeriskDecision(action="none", reason="partial_take_profit_missing_baseline")
    reached = (confirmed_5m_close >= raw_entry_price + activation_r * price_r
               if side == "long"
               else confirmed_5m_close <= raw_entry_price - activation_r * price_r)
    if not reached:
        return DeriskDecision(action="none", reason="partial_take_profit_not_reached")
    if not math.isfinite(take_profit_pct) or not 0 < take_profit_pct < 100:
        raise ValueError("take_profit_pct must be between 0 and 100")
    desired_reduce = floor_to_lot_step(original_quantity * take_profit_pct / 100.0, lot_step)
    if desired_reduce <= 0:
        return DeriskDecision(action="none", reason="partial_take_profit_below_lot")
    desired_reduce = min(desired_reduce, current_quantity)
    target_residual = floor_to_lot_step(max(current_quantity - desired_reduce, 0.0), lot_step)
    reduce_quantity = current_quantity - target_residual
    if reduce_quantity <= 0:
        return DeriskDecision(action="none", reason="partial_take_profit_no_reduction")
    if target_residual < min_size:
        return DeriskDecision(action="dust_full_exit", reduce_quantity=current_quantity,
                              reason="PARTIAL_TP_2R_DUST_SAFE_FULL_EXIT")
    return DeriskDecision(action="partial_reduce", target_residual=target_residual,
                          reduce_quantity=reduce_quantity, reason="partial_take_profit_2r")


def is_4h_direction_invalidated(*, side: str, current_4h_direction: str) -> bool:
    """NONE도 무효화로 취급한다(그 외 값 전부 포함)."""
    if side == "long":
        return current_4h_direction != "LONG"
    if side == "short":
        return current_4h_direction != "SHORT"
    raise ValueError(f"알 수 없는 side: {side}")


PRIORITY_ORDER = [
    "reconciliation_unknown_state",
    "exchange_native_protective_stop_state",
    "already_occurred_stop_fill",
    "emergency_safety_action",
    "4h_full_exit",
    "structure_profit_break",
    "1h_structural_derisk",
    "mfe_profit_giveback",
    "partial_take_profit_2r",
    "profit_lock_tightening",
    "new_entry",
]


class PositionEpochStore:
    """position_id별 PositionEpochState를 append-only JSONL로 영속화한다
    (candidate_c_setup_tracker.SetupTracker와 동일한 재생 방식 - 재시작 시
    마지막 레코드가 그 position_id의 현재 상태다)."""

    def __init__(self, log_path: str | None):
        self.log_path = log_path
        self._states: dict[str, PositionEpochState] = {}
        self._discarded: set[str] = set()
        self.integrity_error: str | None = None

    @classmethod
    def in_memory(cls) -> "PositionEpochStore":
        return cls(log_path=None)

    @classmethod
    def load(cls, log_path: str) -> "PositionEpochStore":
        import dataclasses
        store = cls(log_path)
        field_names = {f.name for f in dataclasses.fields(PositionEpochState)}
        if os.path.exists(log_path):
            with open(log_path, "r", encoding="utf-8") as f:
                for line_number, line in enumerate(f, start=1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(
                            line,
                            parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)),
                        )
                    except (json.JSONDecodeError, ValueError, TypeError) as exc:
                        store.integrity_error = f"position epoch corrupt at line {line_number}: {exc}"
                        break
                    if rec.get("event") == "discarded":
                        position_id = rec.get("position_id")
                        if not isinstance(position_id, str) or not position_id:
                            store.integrity_error = f"position epoch invalid at line {line_number}: discarded position_id"
                            break
                        store._states.pop(position_id, None)
                        store._discarded.add(position_id)
                        continue
                    kwargs = {k: v for k, v in rec.items() if k in field_names}
                    try:
                        state = PositionEpochState(**kwargs)
                        _validate_epoch_state(state)
                        store._states[rec["position_id"]] = state
                        store._discarded.discard(rec["position_id"])
                    except (KeyError, TypeError, ValueError) as exc:
                        store.integrity_error = f"position epoch invalid at line {line_number}: {exc}"
                        break
        return store

    def get(self, position_id: str) -> PositionEpochState:
        return self._states.get(position_id, PositionEpochState())

    def save(self, position_id: str, state: PositionEpochState) -> None:
        import dataclasses
        if self.log_path is None:
            _validate_epoch_state(state)
            self._states[position_id] = state
            return
        directory = os.path.dirname(self.log_path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        record = {"position_id": position_id, **dataclasses.asdict(state)}
        _validate_epoch_state(state)
        import process_lock
        lock_dir = directory or "."
        lock_key = f"candidate_c_epoch_{os.path.basename(self.log_path)}"
        with process_lock.locked(lock_dir, lock_key):
            fresh = type(self).load(self.log_path)
            if fresh.integrity_error:
                raise ValueError(fresh.integrity_error)
            if position_id in fresh._discarded:
                self._states = fresh._states
                self._discarded = fresh._discarded
                return
            process_lock.append_jsonl(self.log_path, record)
            fresh._states[position_id] = state
            self._states = fresh._states
            self._discarded = fresh._discarded

    def discard(self, position_id: str) -> None:
        """포지션이 완전 청산되면 더 이상 필요 없다 - 다음 재시작 재생 시
        참조되지 않도록 append-only tombstone을 먼저 기록한다. 과거 state를
        삭제하지 않으므로 감사 이력은 보존하면서도 재시작 때 부활하지 않는다."""
        if self.log_path is not None:
            if not isinstance(position_id, str) or not position_id:
                raise ValueError("position_id must be a non-empty string")
            import process_lock
            directory = os.path.dirname(self.log_path) or "."
            lock_key = f"candidate_c_epoch_{os.path.basename(self.log_path)}"
            with process_lock.locked(directory, lock_key):
                fresh = type(self).load(self.log_path)
                if fresh.integrity_error:
                    raise ValueError(fresh.integrity_error)
                if position_id not in fresh._discarded:
                    process_lock.append_jsonl(
                        self.log_path,
                        {"position_id": position_id, "event": "discarded"},
                    )
                fresh._states.pop(position_id, None)
                fresh._discarded.add(position_id)
                self._states = fresh._states
                self._discarded = fresh._discarded
                return
        self._states.pop(position_id, None)
        self._discarded.add(position_id)


def _validate_epoch_state(state: PositionEpochState) -> None:
    positive = {
        "entry_time_ms", "raw_entry_price", "effective_entry_price", "contract_size",
        "lot_step", "min_contracts", "tick_size", "fee_rate", "original_contracts",
        "initial_stop_price", "current_stop_price", "high_water", "reservation_id",
    }
    if state.exchange_position_id is not None and (
        not isinstance(state.exchange_position_id, str)
        or not state.exchange_position_id.strip()
    ):
        raise ValueError("exchange_position_id must be a non-empty string")
    if state.exchange_entry_timestamp_ms is not None and (
        isinstance(state.exchange_entry_timestamp_ms, bool)
        or not isinstance(state.exchange_entry_timestamp_ms, int)
        or state.exchange_entry_timestamp_ms <= 0
    ):
        raise ValueError("exchange_entry_timestamp_ms must be a positive integer")
    nonnegative = {
        "entry_fee_usdt", "spread_bps", "slippage_bps", "remaining_contracts",
        "accumulated_funding_usdt", "realized_partial_pnl_usdt",
        "realized_partial_fee_usdt",
        "remaining_reserved_risk_usdt", "profit_lock_stop_price", "target_price",
        "remaining_gross_notional_usdt",
        "max_contracts", "last_action_tick_ms",
    }
    for name in positive | nonnegative:
        value = getattr(state, name)
        if value is None or name == "reservation_id":
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
            raise ValueError(f"{name} must be finite")
        if name in positive and float(value) <= 0:
            raise ValueError(f"{name} must be positive")
        if name in nonnegative and float(value) < 0 and name not in {
            "accumulated_funding_usdt", "realized_partial_pnl_usdt",
        }:
            raise ValueError(f"{name} must be nonnegative")


def decide_action_priority(candidates: dict) -> str:
    """candidates: {action_name: bool}. 우선순위상 가장 먼저 True인 항목 하나만
    고른다 - full exit가 True면 그보다 낮은 우선순위(de-risk/profit-lock/신규
    진입)는 아예 확인하지 않으므로, "같은 clock에서 별도 실행 금지"가 이
    함수의 반환값이 하나뿐이라는 사실 자체로 구조적으로 보장된다."""
    for action in PRIORITY_ORDER:
        if candidates.get(action):
            return action
    return "none"
