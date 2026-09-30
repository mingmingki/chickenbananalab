"""Phase 3.5 Task 6 - flat-confirmed reversal 상태기계.

이 모듈은 실거래소 API를 호출하지 않는다 - fake exchange/reconciliation
adapter(ExchangeReconciliationSnapshot, 호출부/테스트가 주입)로만 상태를
관측한다. portfolio engine과 backtest engine이 나중에 이 반전 로직을 실제로
연결할 때, 둘 다 이 모듈 하나만 그대로 재사용해야 한다 - 로직을 엔진마다
따로 구현하지 않는 것 자체가 "같은 상태전이 결과"를 구조적으로 보장하는
방법이다(같은 코드를 두 번 실행하면 항상 같은 결과가 나온다는 자명한 사실에
기반).

상태 영속화는 candidate_c_setup_tracker.py와 동일한 append-only JSONL 재생
방식을 쓴다 - 재시작 시 마지막 레코드가 그 심볼의 현재 상태다."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from enum import Enum


class State(str, Enum):
    FLAT = "FLAT"
    ENTRY_PENDING = "ENTRY_PENDING"
    LONG = "LONG"
    SHORT = "SHORT"
    EXIT_PENDING = "EXIT_PENDING"
    REVERSAL_EXIT_PENDING = "REVERSAL_EXIT_PENDING"
    REVERSAL_WAIT_FLAT = "REVERSAL_WAIT_FLAT"
    REVERSAL_ENTRY_PENDING = "REVERSAL_ENTRY_PENDING"
    SAFE_HALT = "SAFE_HALT"


class InvalidReversalTransitionError(Exception):
    pass


@dataclass(frozen=True)
class ExchangeReconciliationSnapshot:
    """fake adapter가 매 확정 5분봉마다 제공해야 하는 최소 정보 - 실거래소
    응답을 그대로 흉내낸 값이며, 이 모듈은 이 값을 만드는 방법(실제 API 호출)에
    전혀 관여하지 않는다."""
    net_position_qty: float
    has_pending_entry_order: bool
    has_pending_exit_order: bool
    has_unknown_order_state: bool
    oco_cleanup_clean: bool
    local_reservation_released: bool

    def is_flat_confirmed(self) -> bool:
        return (
            self.net_position_qty == 0
            and not self.has_pending_entry_order
            and not self.has_pending_exit_order
            and not self.has_unknown_order_state
            and self.oco_cleanup_clean
            and self.local_reservation_released
        )


@dataclass
class _SymbolState:
    state: State = State.FLAT
    pending_side: str | None = None
    flat_confirmed_at_tick: int | None = None
    original_side: str | None = None  # EXIT_PENDING/REVERSAL_EXIT_PENDING 중 거부 시 되돌아갈 방향


class SymbolReversalMachine:
    """심볼 하나의 반전 상태기계. 여러 심볼은 서로 완전히 독립된 인스턴스를
    쓴다(공유 상태 없음 - 심볼 isolation은 이 사실 자체로 보장된다)."""

    def __init__(self, symbol: str, initial: _SymbolState | None = None):
        self.symbol = symbol
        self._s = initial or _SymbolState()

    @property
    def state(self) -> State:
        return self._s.state

    @property
    def pending_side(self) -> str | None:
        return self._s.pending_side

    @property
    def flat_confirmed_at_tick(self) -> int | None:
        return self._s.flat_confirmed_at_tick

    def enter_safe_halt(self) -> None:
        """Retain exposure/reversal identity while blocking fresh mutations."""
        self._s.state = State.SAFE_HALT

    def to_record(self, tick_index: int) -> dict:
        return {
            "symbol": self.symbol, "tick": tick_index, "state": self._s.state.value,
            "pending_side": self._s.pending_side, "flat_confirmed_at_tick": self._s.flat_confirmed_at_tick,
            "original_side": self._s.original_side,
        }

    @classmethod
    def from_record(cls, symbol: str, rec: dict) -> "SymbolReversalMachine":
        return cls(symbol, _SymbolState(
            state=State(rec["state"]), pending_side=rec["pending_side"],
            flat_confirmed_at_tick=rec["flat_confirmed_at_tick"], original_side=rec.get("original_side"),
        ))

    # --- 정상 진입/청산(반전 아님) ---

    def request_entry(self, side: str) -> None:
        if self._s.state != State.FLAT:
            raise InvalidReversalTransitionError(f"{self.symbol}: {self._s.state}에서 신규진입 요청 불가")
        self._s.state = State.ENTRY_PENDING
        self._s.pending_side = side

    def confirm_entry_filled(self) -> None:
        if self._s.state != State.ENTRY_PENDING:
            raise InvalidReversalTransitionError(f"{self.symbol}: {self._s.state}에서 진입 체결 확인 불가")
        self._s.state = State.LONG if self._s.pending_side == "long" else State.SHORT
        self._s.pending_side = None

    def observe_entry_outcome(self, *, accepted: bool, unknown: bool = False) -> None:
        """신규진입(반전 아님) 주문의 실제 결과를 반영한다 -
        observe_reversal_entry_outcome와 동일한 계약: unknown이면 SAFE_HALT(주문이
        실제로 나갔는지 확신할 수 없으므로 재시도하지 않고 사람/reconciliation
        대기 - 절대 FLAT으로 되돌리지 않는다), 확정적으로 거부됐으면(accepted=False,
        unknown=False) FLAT으로 돌아가 다음 setup을 다시 시도할 수 있게 한다.
        체결 확인(accepted=True)은 이 메서드가 아니라 confirm_entry_filled()가
        전담한다(반전 진입에서 confirm_reversal_entry_filled()를 따로 두는 것과
        동일한 이유 - 성공 경로와 실패 분류 경로를 섞지 않는다)."""
        if self._s.state != State.ENTRY_PENDING:
            raise InvalidReversalTransitionError(f"{self.symbol}: {self._s.state}에서 진입결과 처리 불가")
        if unknown:
            self._s.state = State.SAFE_HALT
            return
        if not accepted:
            self._s.state = State.FLAT
            self._s.pending_side = None

    def request_exit(self) -> None:
        if self._s.state not in (State.LONG, State.SHORT):
            raise InvalidReversalTransitionError(f"{self.symbol}: {self._s.state}에서 청산 요청 불가")
        self._s.original_side = "long" if self._s.state == State.LONG else "short"
        self._s.state = State.EXIT_PENDING

    def confirm_exit_filled(self) -> None:
        if self._s.state != State.EXIT_PENDING:
            raise InvalidReversalTransitionError(f"{self.symbol}: {self._s.state}에서 청산 체결 확인 불가")
        self._s.state = State.FLAT

    # --- 반전 ---

    def request_reversal(self, tick_index: int, target_side: str) -> None:
        if self._s.state not in (State.LONG, State.SHORT):
            raise InvalidReversalTransitionError(f"{self.symbol}: {self._s.state}에서 반전 요청 불가")
        current_side = "long" if self._s.state == State.LONG else "short"
        if target_side == current_side:
            raise InvalidReversalTransitionError("같은 방향으로 반전 요청 불가")
        self._s.original_side = current_side
        self._s.state = State.REVERSAL_EXIT_PENDING
        self._s.pending_side = target_side

    def observe_exit_outcome(self, *, filled: bool, unknown: bool = False) -> None:
        """REVERSAL_EXIT_PENDING/EXIT_PENDING 상태에서 청산 주문의 실제 결과를
        반영한다. unknown이면 SAFE_HALT(임의 판단 대신 human/reconciliation
        대기). 거부(filled=False)면 원래 보유 방향(LONG/SHORT)으로 즉시
        복귀한다 - EXIT_PENDING에 영구히 머물지 않는다(반전 요청 자체도 취소됨 -
        재시도는 다음 tick에 별도로 request_exit/request_reversal을 다시
        호출해야 한다). 체결(filled=True)이면 pending 상태를 유지하고,
        실제 flat 여부는 observe_reconciliation()에서 별도로 확인한다."""
        if self._s.state not in (State.EXIT_PENDING, State.REVERSAL_EXIT_PENDING):
            raise InvalidReversalTransitionError(f"{self.symbol}: {self._s.state}에서 청산결과 처리 불가")
        if unknown:
            self._s.state = State.SAFE_HALT
            return
        if not filled:
            self._s.state = State.LONG if self._s.original_side == "long" else State.SHORT
            self._s.pending_side = None
            return

    def observe_reconciliation(self, tick_index: int, snap: ExchangeReconciliationSnapshot) -> None:
        """매 확정 5분봉마다 호출한다. unknown order state는 어떤 상태에서든
        즉시 SAFE_HALT로 우선 처리한다."""
        if snap.has_unknown_order_state:
            self._s.state = State.SAFE_HALT
            return
        if self._s.state in (State.EXIT_PENDING, State.REVERSAL_EXIT_PENDING) and snap.is_flat_confirmed():
            if self._s.state == State.EXIT_PENDING:
                self._s.state = State.FLAT
            else:
                self._s.state = State.REVERSAL_WAIT_FLAT
                self._s.flat_confirmed_at_tick = tick_index

    def converge_authoritative_flat(self, tick_index: int) -> bool:
        """Converge a durable JOURNALED exit after a crash before state persist."""
        if self._s.state == State.REVERSAL_EXIT_PENDING:
            self._s.state = State.REVERSAL_WAIT_FLAT
            self._s.flat_confirmed_at_tick = tick_index
            return True
        if self._s.state in (State.LONG, State.SHORT, State.EXIT_PENDING):
            self._s.state = State.FLAT
            self._s.pending_side = None
            self._s.flat_confirmed_at_tick = None
            self._s.original_side = None
            return True
        return False

    def halt_on_position_side_mismatch(self, authoritative_side: str) -> bool:
        expected = State.LONG if authoritative_side == "long" else State.SHORT
        if self._s.state in (State.LONG, State.SHORT) and self._s.state != expected:
            self._s.state = State.SAFE_HALT
            return True
        return False

    def reconcile_authoritative_position(
        self, *, authoritative_side: str | None, tick_index: int,
        exit_journaled: bool = False,
    ) -> bool:
        """Repair a crash after a pending state was persisted.

        ``authoritative_side`` comes only from the exchange/epoch
        reconciliation boundary.  A matching fill completes the pending
        transition; an opposite position is never adopted into the old
        lifecycle.  Exit states become flat only after the matching durable
        exit coordinator is JOURNALED.
        """
        before = self.to_record(tick_index)
        state = self._s.state
        if state == State.ENTRY_PENDING:
            if authoritative_side is None:
                return False
            if authoritative_side == self._s.pending_side:
                self._s.state = State.LONG if authoritative_side == "long" else State.SHORT
                self._s.pending_side = None
            else:
                self._s.state = State.SAFE_HALT
        elif state == State.EXIT_PENDING:
            if authoritative_side is None and exit_journaled:
                self._s = _SymbolState(state=State.FLAT)
            elif authoritative_side not in (None, self._s.original_side):
                self._s.state = State.SAFE_HALT
        elif state == State.REVERSAL_EXIT_PENDING:
            if authoritative_side is None and exit_journaled:
                self._s.state = State.REVERSAL_WAIT_FLAT
                self._s.flat_confirmed_at_tick = tick_index
            elif authoritative_side not in (None, self._s.original_side):
                self._s.state = State.SAFE_HALT
        elif state == State.REVERSAL_WAIT_FLAT:
            if authoritative_side is not None:
                # A target-side position can only be accepted from the
                # REVERSAL_ENTRY_PENDING phase.  Seeing any position while the
                # durable state still says WAIT_FLAT is contradictory.
                self._s.state = State.SAFE_HALT
        elif state == State.REVERSAL_ENTRY_PENDING:
            if authoritative_side is None:
                return False
            if authoritative_side == self._s.pending_side:
                self._s.state = State.LONG if authoritative_side == "long" else State.SHORT
                self._s.pending_side = None
                self._s.flat_confirmed_at_tick = None
            else:
                self._s.state = State.SAFE_HALT
        return before != self.to_record(tick_index)

    def revalidate_and_request_reversal_entry(
        self, tick_index: int, *, current_4h_direction: str, entry_conditions_still_met: bool,
    ) -> bool:
        """REVERSAL_WAIT_FLAT 상태에서만 유효. flat_confirmed된 바로 그 tick
        에서는 절대 진입하지 않는다(최소 다음 확정 5분봉까지 대기). 방향
        불일치거나 entry_conditions_still_met=False(반대 setup invalidated,
        risk cap 초과, stale data 등 - 이유는 호출부 Task 3/4/5 모듈들이 이미
        판정해서 하나의 bool로 넘긴다)면 반전을 취소하고 FLAT으로 복귀한다
        (반대 진입을 강행하지 않음 - 반전 실패는 곧 "포지션 없음"으로 끝나지
        영구 대기가 아니다)."""
        if self._s.state != State.REVERSAL_WAIT_FLAT:
            return False
        if tick_index <= self._s.flat_confirmed_at_tick:
            return False
        target_side = self._s.pending_side
        direction_ok = (
            (current_4h_direction == "LONG" and target_side == "long")
            or (current_4h_direction == "SHORT" and target_side == "short")
        )
        if not direction_ok or not entry_conditions_still_met:
            self._s.state = State.FLAT
            self._s.pending_side = None
            return False
        self._s.state = State.REVERSAL_ENTRY_PENDING
        return True

    def confirm_reversal_entry_filled(self) -> None:
        if self._s.state != State.REVERSAL_ENTRY_PENDING:
            raise InvalidReversalTransitionError(f"{self.symbol}: {self._s.state}에서 반전진입 체결 확인 불가")
        self._s.state = State.LONG if self._s.pending_side == "long" else State.SHORT
        self._s.pending_side = None
        self._s.flat_confirmed_at_tick = None

    def observe_reversal_entry_outcome(self, *, accepted: bool, unknown: bool = False) -> None:
        if self._s.state != State.REVERSAL_ENTRY_PENDING:
            raise InvalidReversalTransitionError(
                f"{self.symbol}: {self._s.state}에서 반전진입 결과 처리 불가"
            )
        if unknown:
            self._s.state = State.SAFE_HALT
            return
        if not accepted:
            self._s.state = State.FLAT
            self._s.pending_side = None
            self._s.flat_confirmed_at_tick = None


class ReversalStateStore:
    """심볼별 SymbolReversalMachine을 append-only JSONL로 영속화한다."""

    def __init__(self, log_path: str):
        self.log_path = log_path
        self._machines: dict[str, SymbolReversalMachine] = {}

    @classmethod
    def load(cls, log_path: str) -> "ReversalStateStore":
        store = cls(log_path)
        if os.path.exists(log_path):
            with open(log_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        break
                    store._machines[rec["symbol"]] = SymbolReversalMachine.from_record(rec["symbol"], rec)
        return store

    def get(self, symbol: str) -> SymbolReversalMachine:
        if symbol not in self._machines:
            self._machines[symbol] = SymbolReversalMachine(symbol)
        return self._machines[symbol]

    def persist(self, symbol: str, tick_index: int) -> None:
        directory = os.path.dirname(self.log_path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        record = self.get(symbol).to_record(tick_index)
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
            f.flush()
            os.fsync(f.fileno())
