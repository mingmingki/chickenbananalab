"""Phase 3.5 Task 4 - Candidate C 확정 10분봉 setup 추적 + 재시작 내구성.

Donchian 채널 비교 자체는 여기서 하지 않는다 - 호출부(Task 5/실행 루프)가
candidate_c_timeframe_contract.AsOfSnapshot의 bar_10m_current/bars_10m_prior_20으로
직접 "이번 10분봉에서 이 (symbol, side) 조건이 True인가"를 판정해 넘겨준다.
이 모듈은 조건이 True인 각 확정 10분봉의 setup_id와 그 setup의 소비(entry
attempt) 완결 여부를 append-only JSONL 로그로 추적한다.

두 종류의 레코드만 기록한다.
- {"type": "state", ...}: (symbol, side) 조건 상태와 qualifying 10분봉 기록.
  조건이 True인 새 확정 10분봉마다 setup_id를 하나씩 기록한다.
- {"type": "attempt_outcome", "setup_id":, "outcome":, ...}: 그 setup_id로 실제
  진행한 entry attempt의 최종 결과(accepted/rejected/...) 기록.

재시작 시 로그를 순서대로 재생하면: (1) 각 (symbol,side)의 마지막 상태,
(2) 지금까지 생성된 모든 setup_id, (3) 그중 아직 attempt_outcome이 기록되지
않은(=크래시로 중간에 끊긴) "pending" setup_id 목록을 전부 정확히 복구할 수
있다 - 호출부는 재시작 후 pending 목록을 우선 정리(reconciliation)해야 한다."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field


def make_setup_id(symbol: str, side: str, qualifying_10m_close_timestamp_ms: int) -> str:
    return f"{symbol}|{side}|{qualifying_10m_close_timestamp_ms}"


@dataclass
class SetupTracker:
    log_path: str | None
    _last_state: dict = field(default_factory=dict)       # (symbol, side) -> bool
    _all_setup_ids: dict = field(default_factory=dict)     # setup_id -> {"symbol","side","timestamp"}
    _attempted_setup_ids: dict = field(default_factory=dict)  # setup_id -> outcome
    # Start of the current uninterrupted True setup run.  Unlike individual
    # per-bar setup_ids this timestamp does not advance while the condition
    # remains True, so a restart cannot make an old setup look fresh again.
    _active_entry_started_ms: dict = field(default_factory=dict)
    _entry_eligible_state: dict = field(default_factory=dict)
    _active_true_started_ms: dict = field(default_factory=dict)  # (symbol, side) -> first True timestamp
    # [2026-09-16, 사용자 직접 지시 - timeout 1회 재검토 정책] 최초 GPT 결과가
    # timeout일 때만, 원 setup의 바로 다음 확정 10분봉에서 딱 한 번 재검토할
    # 기회를 durable하게 기록한다. wait/reject는 대상이 아니다(오케스트레이터가
    # 그 경우엔 애초에 arm_timeout_retry를 호출하지 않는다). "consumed"는
    # 재시작해도 절대 사라지지 않는다(재시도 횟수가 재시작으로 초기화되면
    # 안 된다는 사용자 지시) - 재검토 시도 자체를 실제로 시작하기 *직전에*
    # 기록해야 한다(크래시가 나도 "이미 한 번 썼다"로 fail-closed).
    _timeout_retry_armed: dict = field(default_factory=dict)     # setup_id -> {"symbol","side","retry_bar_open_time_ms"}
    _timeout_retry_consumed: set = field(default_factory=set)    # setup_id들 - 이미 재검토를 시도함(결과 무관)

    @classmethod
    def load(cls, log_path: str) -> "SetupTracker":
        """log_path가 이미 존재하면 처음부터 끝까지 재생해 상태를 복구한다
        (append-only이므로 파일이 중간에 잘려도 마지막 완전한 줄까지만 안전하게
        읽는다 - 크래시가 줄 쓰는 도중 났을 가능성을 대비)."""
        tracker = cls(log_path=log_path)
        if os.path.exists(log_path):
            with open(log_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        break  # 마지막 줄이 쓰다 만 상태 - 그 이후는 신뢰하지 않는다.
                    tracker._apply(rec)
        return tracker

    @classmethod
    def in_memory(cls) -> "SetupTracker":
        return cls(log_path=None)

    def _apply(self, rec: dict) -> None:
        if rec["type"] == "state":
            key = (rec["symbol"], rec["side"])
            previous = self._last_state.get(key, False)
            new_state = bool(rec["new_state"])
            if new_state and not previous:
                self._active_true_started_ms[key] = rec["timestamp"]
            elif not new_state:
                self._active_true_started_ms.pop(key, None)
            self._last_state[key] = new_state
            eligible = new_state and bool(rec.get("entry_eligible", False))
            if eligible and not self._entry_eligible_state.get(key, False):
                self._active_entry_started_ms[key] = rec["timestamp"]
            elif not eligible:
                self._active_entry_started_ms.pop(key, None)
            self._entry_eligible_state[key] = eligible
            if rec.get("setup_id"):
                self._all_setup_ids[rec["setup_id"]] = {
                    "symbol": rec["symbol"], "side": rec["side"], "timestamp": rec["timestamp"],
                }
        elif rec["type"] == "attempt_outcome":
            self._attempted_setup_ids[rec["setup_id"]] = rec["outcome"]
        elif rec["type"] == "timeout_retry_armed":
            self._timeout_retry_armed[rec["setup_id"]] = {
                "symbol": rec["symbol"], "side": rec["side"],
                "retry_bar_open_time_ms": rec["retry_bar_open_time_ms"],
            }
        elif rec["type"] == "timeout_retry_consumed":
            self._timeout_retry_consumed.add(rec["setup_id"])

    def _append(self, record: dict) -> None:
        if self.log_path is None:
            return
        directory = os.path.dirname(self.log_path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
            f.flush()
            os.fsync(f.fileno())

    def observe(self, symbol: str, side: str, timestamp_ms: int,
                condition_true: bool, *, entry_eligible: bool | None = None) -> str | None:
        """Track raw per-bar identities and the distinct direction-eligible window.

        Direction waiting never spends the 30-minute entry opportunity. Both
        clocks survive restart; an already observed bar still cannot resubmit.
        Omitted eligibility preserves the original standalone tracker contract.
        """
        key = (symbol, side)
        eligible = bool(condition_true and (True if entry_eligible is None else entry_eligible))
        setup_id = make_setup_id(symbol, side, timestamp_ms) if condition_true else None
        is_new = setup_id is not None and setup_id not in self._all_setup_ids
        changed = (self._last_state.get(key, False) != bool(condition_true)
                   or self._entry_eligible_state.get(key, False) != eligible)
        if not is_new and not changed:
            return None
        record = {"type": "state", "symbol": symbol, "side": side,
                  "timestamp": timestamp_ms, "new_state": bool(condition_true),
                  "entry_eligible": eligible, "setup_id": setup_id if is_new else None}
        self._append(record)
        self._apply(record)
        return setup_id if is_new else None

    def entry_eligible_state(self, symbol: str, side: str) -> bool:
        return self._entry_eligible_state.get((symbol, side), False)

    def continuous_entry_eligible_started_ms(self, symbol: str, side: str) -> int | None:
        return self._active_entry_started_ms.get((symbol, side))

    def continuous_true_started_ms(self, symbol: str, side: str) -> int | None:
        """First bar timestamp of the uninterrupted current True setup run."""
        return self._active_true_started_ms.get((symbol, side))

    def record_attempt_outcome(self, setup_id: str, outcome: str) -> None:
        """setup_id로 진행한 entry attempt의 최종 결과(예: 'accepted',
        'rejected:below_min_size', 'rejected:remaining_risk_room_zero',
        'rejected:stale_data' 등)를 영속화한다 - risk/최소수량/안전조건으로
        거부돼도 반드시 호출해야 그 setup이 '소비'된 것으로 완결된다."""
        if setup_id not in self._all_setup_ids:
            raise KeyError(f"관측된 적 없는 setup_id: {setup_id}")
        self._append({"type": "attempt_outcome", "setup_id": setup_id, "outcome": outcome})
        self._attempted_setup_ids[setup_id] = outcome

    def is_attempted(self, setup_id: str) -> bool:
        return setup_id in self._attempted_setup_ids

    def pending_setup_ids(self) -> list[str]:
        """생성됐지만(edge 발생) 아직 attempt_outcome이 기록되지 않은 setup_id -
        재시작 직후 이 목록을 먼저 reconciliation해야 한다(크래시로 중간에
        끊긴 attempt)."""
        return [sid for sid in self._all_setup_ids if sid not in self._attempted_setup_ids]

    def all_setup_ids(self) -> list[str]:
        return list(self._all_setup_ids)

    def setup_metadata(self, setup_id: str) -> dict | None:
        """Return a copy of durable setup metadata for reconciliation tooling."""
        meta = self._all_setup_ids.get(setup_id)
        return dict(meta) if meta is not None else None

    def current_state(self, symbol: str, side: str) -> bool:
        return self._last_state.get((symbol, side), False)

    # --- timeout 1회 재검토 정책(2026-09-16, 사용자 직접 지시, 기본 OFF) ---

    def arm_timeout_retry(self, symbol: str, side: str, setup_id: str, retry_bar_open_time_ms: int) -> None:
        """최초(재검토가 아닌) GPT 게이트 결과가 timeout이었을 때만 오케스트레이터가
        부른다(wait/reject는 절대 여기로 오지 않음 - 호출부 책임). 이미 이
        setup_id에 대해 armed/consumed 기록이 있으면 아무 것도 하지 않는다(중복
        호출로 창을 늘리거나 새로 열지 않음 - 최초 1회만 유효)."""
        if setup_id in self._timeout_retry_armed or setup_id in self._timeout_retry_consumed:
            return
        self._append({
            "type": "timeout_retry_armed", "setup_id": setup_id, "symbol": symbol, "side": side,
            "retry_bar_open_time_ms": retry_bar_open_time_ms,
        })
        self._timeout_retry_armed[setup_id] = {
            "symbol": symbol, "side": side, "retry_bar_open_time_ms": retry_bar_open_time_ms,
        }

    def pending_timeout_retry_for_bar(self, symbol: str, side: str, current_bar_open_time_ms: int) -> str | None:
        """이 (symbol, side)에 armed(아직 consumed 아님)된 재검토가 있고, 그
        대상 봉이 정확히 지금 이 봉이면 그 원 setup_id를 반환한다 - 그 외(아직
        대상 봉 전, 이미 지나침, consumed됨, 처음부터 armed 안 됨)는 모두 None -
        "그 다음 확정 10분봉에서 최대 1회"를 여기 한 곳에서 강제한다(다른 곳에서
        느슨하게 재해석할 여지를 주지 않음). 지나친 경우를 별도로 지우지 않는다 -
        같은 setup_id가 다시 armed되지 않는 한(그럴 일 없음, 위 가드) 그냥 계속
        None만 반환하므로 이월 위험이 없다."""
        for setup_id, meta in self._timeout_retry_armed.items():
            if meta["symbol"] != symbol or meta["side"] != side:
                continue
            if setup_id in self._timeout_retry_consumed:
                continue
            if meta["retry_bar_open_time_ms"] == current_bar_open_time_ms:
                return setup_id
        return None

    def mark_timeout_retry_consumed(self, setup_id: str) -> None:
        """실제 재검토 시도를 시작하기 *직전에* 호출해야 한다(GPT를 부르기 전) -
        재시작해도 이 기록은 그대로 남아 같은 setup_id에 다시 기회가 생기지
        않는다. 이미 consumed면 아무 것도 하지 않는다(멱등)."""
        if setup_id in self._timeout_retry_consumed:
            return
        self._append({"type": "timeout_retry_consumed", "setup_id": setup_id})
        self._timeout_retry_consumed.add(setup_id)
