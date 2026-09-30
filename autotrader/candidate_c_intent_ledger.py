"""Phase 3.6 Part D / Phase 3.7 Part 1 - Candidate C 실거래 주문 의도(intent)를
위한 재시작 안전 durable ledger. setup tracker/epoch store와 별개다 - "이
setup에서 실제로 거래소에 주문을 낼지"의 생애주기를 추적한다.

이 모듈은 실제 OKX API를 호출하지 않는다 - 상태 전이만 관리하고, 실제
제출/조회는 호출부가 넘겨주는 fake/실제 adapter 콜백이 담당한다.

상태 전이:
intent_persisted -> submit_attempted -> exchange_ack | submission_unknown | rejected
                  -> partial_fill | filled -> protection_pending -> protected -> terminal

submission_unknown이나 reconcile_required는 오직 reconcile()로만 해소되고,
"거래소에 없음이 확정"되면 confirmed_not_submitted(터미널)로 가지 intent_persisted로
되돌아가지 않는다 - 같은 clOrdId 재제출을 구조적으로 영구히 금지한다(Phase
3.7 이전 버전의 위험한 동작이었다). 재시도가 필요하면 호출부가
attempt_epoch를 올려 완전히 새로운 clOrdId를 가진 새 intent를 만들어야 한다.

계약(Phase 3.7 갱신):
1. API 호출 "전"에 intent를 먼저 영속화한다.
2. 동일 (account, symbol, key_id, config_hash, attempt_epoch)는 항상 같은
   clOrdId로 수렴한다 - attempt_epoch가 다르면 완전히 다른 clOrdId다.
3. submission_unknown/reconcile_required면 blind retry를 하지 않는다 -
   reconcile() 결과가 나오기 전까지는 같은 intent로 새 제출 시도를 허용하지
   않는다(attempt_submit()이 즉시 예외를 던지고 submit_fn을 0회 호출한다).
4. process_lock.py를 재사용해 멀티스레드/멀티프로세스가 같은 intent_id에 대해
   동시에 submit_attempted로 전이하려 해도 최대 1회만 실제로 전이된다.
5. append-only JSONL + schema_version + 레코드별 체크섬 + seq/prev_hash
   해시체인 + fsync. 체크섬 불일치/JSON 파싱 실패/seq-hash 불일치로 감지된
   손상 레코드는 삭제가 아니라 quarantine 파일로 옮긴다(감사 증거 보존).
6. submit_attempted 이후(포함) 손상이 발견되면, 또는 재시작 시 submit_attempted에서
   멈춰 더 이상 진행되지 않은(정상 종료라도 모호한) intent를 발견하면,
   reconcile_required로 강제 전이한다 - 실제 거래소 조회 없이는 절대 자동으로
   해소되지 않는다.
7. 재시작 시 pending_intents()로 미완료 intent를 먼저 복구해야 한다."""
from __future__ import annotations

import hashlib
import json
import math
import os
import time
from dataclasses import dataclass, field
from enum import Enum

import process_lock

SCHEMA_VERSION = 2

TERMINAL_STATES = {"terminal"}
# submit_fn 호출 이후(=거래소에 실제로 도달했을 가능성이 생긴) 상태들 - 이
# 상태들에서 손상이 발견되면 절대 intent_persisted로 되돌리지 않는다.
POST_SUBMIT_ATTEMPT_STATES = {
    "submit_attempted", "exchange_ack", "submission_unknown", "reconcile_required",
    "partial_fill", "filled", "protection_pending", "protected",
}
STATE_ORDER = [
    "intent_persisted", "submit_attempted", "exchange_ack", "submission_unknown",
    "reconcile_required", "rejected", "confirmed_not_submitted",
    "partial_fill", "filled", "protection_pending", "protected", "terminal",
]
INTENT_KINDS = {
    "EntryIntent", "StopUpdateIntent", "ReduceIntent", "ExitIntent", "ReversalIntent",
}


class IntentState(str, Enum):
    INTENT_PERSISTED = "intent_persisted"
    SUBMIT_ATTEMPTED = "submit_attempted"
    EXCHANGE_ACK = "exchange_ack"
    SUBMISSION_UNKNOWN = "submission_unknown"
    RECONCILE_REQUIRED = "reconcile_required"
    REJECTED = "rejected"
    CONFIRMED_NOT_SUBMITTED = "confirmed_not_submitted"
    PARTIAL_FILL = "partial_fill"
    FILLED = "filled"
    PROTECTION_PENDING = "protection_pending"
    PROTECTED = "protected"
    TERMINAL = "terminal"


class LedgerError(Exception):
    pass


class LedgerIntegrityError(LedgerError):
    """The append-only ledger contains or was asked to append untrusted data."""


class IntentNotFoundError(LedgerError):
    pass


class BlindRetryBlockedError(LedgerError):
    """submission_unknown 상태에서 reconcile 없이 재제출을 시도했을 때."""


class ReconcileRequiredError(LedgerError):
    """reconcile_required 상태에서 제출을 시도했을 때 - submit_fn은 0회
    호출돼야 한다(이 예외는 process_lock 진입 전에 던져진다)."""


class ClOrdIdCollisionError(LedgerError):
    """서로 다른 (key_id, attempt_epoch)가 우연히 같은 clOrdId를 만들어냈을 때
    (SHA-256 충돌 - 사실상 불가능하지만 방어적으로 검사한다)."""


_ENVELOPE_FIELDS = {"schema_version", "seq", "prev_hash", "checksum"}
_CREATED_FIELDS = {
    "event", "intent_id", "account_id", "symbol", "strategy_id", "setup_id",
    "position_epoch", "config_version_id", "config_hash", "cl_ord_id",
    "attach_algo_cl_ord_id", "kind", "attempt_epoch", "requested_side",
    "requested_quantity", "requested_stop_price", "requested_target_price",
    "contract_size", "lot_step", "min_contracts", "tick_size", "max_contracts",
    "reservation_id", "reserved_risk_usdt", "gross_notional_usdt",
    "entry_price_estimate", "protective_order_type", "state",
    "remote_submission_finalized", "strategy_policy",
} | _ENVELOPE_FIELDS
_TRANSITION_FIELDS = {
    "event", "intent_id", "state", "exchange_order_id", "filled_quantity",
    "remaining_quantity", "protective_algo_ids", "reject_reason", "reconcile_reason",
    "protection_phase", "target_residual_quantity",
    "position_fingerprint",
    "remote_submission_finalized", "submission_unknown_reason",
} | _ENVELOPE_FIELDS
_POSITIVE_NUMERIC_FIELDS = {
    "requested_quantity", "requested_stop_price", "requested_target_price",
    "contract_size", "lot_step", "min_contracts", "tick_size", "max_contracts",
    "reserved_risk_usdt", "gross_notional_usdt", "entry_price_estimate",
}
_NONNEGATIVE_NUMERIC_FIELDS = {
    "filled_quantity", "remaining_quantity", "target_residual_quantity",
}


def _require_finite_number(record: dict, field: str, *, allow_zero: bool) -> None:
    value = record.get(field)
    if value is None:
        return
    if (not isinstance(value, (int, float)) or isinstance(value, bool)
            or not math.isfinite(float(value))
            or (float(value) < 0 if allow_zero else float(value) <= 0)):
        domain = "non-negative" if allow_zero else "positive"
        raise LedgerIntegrityError(f"{field} must be a finite {domain} number")


def _validate_record(record: dict) -> None:
    if not isinstance(record, dict):
        raise LedgerIntegrityError("ledger row must be an object")
    event = record.get("event")
    if event == "created":
        allowed = _CREATED_FIELDS
        required = {
            "event", "intent_id", "account_id", "symbol", "strategy_id",
            "config_version_id", "config_hash", "cl_ord_id", "kind", "state",
        }
        if record.get("state") != IntentState.INTENT_PERSISTED.value:
            raise LedgerIntegrityError("created row must be intent_persisted")
        if bool(record.get("setup_id")) == bool(record.get("position_epoch")):
            raise LedgerIntegrityError("created row requires exactly one key identity")
        attempt_epoch = record.get("attempt_epoch", 0)
        if (not isinstance(attempt_epoch, int) or isinstance(attempt_epoch, bool)
                or attempt_epoch < 0):
            raise LedgerIntegrityError("attempt_epoch must be a non-negative integer")
        if record.get("requested_side") not in (None, "long", "short"):
            raise LedgerIntegrityError("requested_side invalid")
        if record.get("kind") not in INTENT_KINDS:
            raise LedgerIntegrityError(f"intent kind invalid: {record.get('kind')!r}")
        if record.get("protective_order_type") not in (None, "oco", "conditional"):
            raise LedgerIntegrityError("protective_order_type invalid")
        if record.get("strategy_policy") is not None:
            try:
                import candidate_c_strategy_policy
                candidate_c_strategy_policy.validate_strategy_policy(
                    record["strategy_policy"],
                )
            except Exception as exc:
                raise LedgerIntegrityError("strategy_policy invalid") from exc
    elif event == "transition":
        allowed = _TRANSITION_FIELDS
        required = {"event", "intent_id", "state"}
        state = record.get("state")
        if state not in {item.value for item in IntentState}:
            raise LedgerIntegrityError("transition state invalid")
        if state in (IntentState.PARTIAL_FILL.value, IntentState.FILLED.value) \
                and "filled_quantity" not in record:
            raise LedgerIntegrityError("fill transition requires filled_quantity")
        if (state == IntentState.PROTECTED.value
                and "protective_algo_ids" not in record
                and "remaining_quantity" not in record
                and "target_residual_quantity" not in record):
            raise LedgerIntegrityError("protected transition requires protective_algo_ids")
        if "protection_phase" in record and record["protection_phase"] not in (
            "complete", "reduce_resize_pending",
        ):
            raise LedgerIntegrityError("protection_phase invalid")
    else:
        raise LedgerIntegrityError(f"unknown ledger event: {event!r}")

    missing = required - set(record)
    unknown = set(record) - allowed
    if missing:
        raise LedgerIntegrityError(f"ledger row missing fields: {sorted(missing)}")
    if unknown:
        raise LedgerIntegrityError(f"ledger row has unknown fields: {sorted(unknown)}")
    for field in required - {"event", "state"}:
        value = record.get(field)
        if value is None or isinstance(value, bool) or not isinstance(value, (str, int)) or str(value) == "":
            raise LedgerIntegrityError(f"{field} must be a non-empty scalar identifier")
    for field in _POSITIVE_NUMERIC_FIELDS:
        _require_finite_number(record, field, allow_zero=False)
    for field in _NONNEGATIVE_NUMERIC_FIELDS:
        _require_finite_number(record, field, allow_zero=True)
    if "protective_algo_ids" in record:
        ids = record["protective_algo_ids"]
        if (not isinstance(ids, list) or any(
                not isinstance(item, str) or not item for item in ids
        )):
            raise LedgerIntegrityError("protective_algo_ids must be a list of non-empty strings")
    if "position_fingerprint" in record:
        fingerprint = record["position_fingerprint"]
        allowed_fingerprint = {
            "position_id", "entry_timestamp_ms", "entry_order_id",
            "entry_client_order_id", "entry_fill_id", "entry_fill_ids",
            "first_fill_timestamp_ms", "last_fill_timestamp_ms",
            "position_trade_id_at_binding",
            "position_updated_timestamp_ms_at_binding",
            "entry_price", "side", "contracts",
        }
        if not isinstance(fingerprint, dict) or not fingerprint:
            raise LedgerIntegrityError("position_fingerprint must be a non-empty object")
        if set(fingerprint) - allowed_fingerprint:
            raise LedgerIntegrityError("position_fingerprint has unknown fields")
        identity_count = 0
        for name in (
            "position_id", "entry_order_id", "entry_client_order_id",
            "entry_fill_id", "position_trade_id_at_binding",
        ):
            value = fingerprint.get(name)
            if value is not None:
                if not isinstance(value, str) or not value.strip():
                    raise LedgerIntegrityError(f"position_fingerprint {name} invalid")
                identity_count += 1
        timestamp = fingerprint.get("entry_timestamp_ms")
        if timestamp is not None:
            if (isinstance(timestamp, bool) or not isinstance(timestamp, int)
                    or timestamp <= 0):
                raise LedgerIntegrityError("position_fingerprint entry_timestamp_ms invalid")
            identity_count += 1
        for name in (
            "first_fill_timestamp_ms", "last_fill_timestamp_ms",
            "position_updated_timestamp_ms_at_binding",
        ):
            value = fingerprint.get(name)
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int) or value <= 0
            ):
                raise LedgerIntegrityError(f"position_fingerprint {name} invalid")
        if "entry_fill_ids" in fingerprint:
            fill_ids = fingerprint["entry_fill_ids"]
            if (not isinstance(fill_ids, list) or not fill_ids
                    or any(not isinstance(item, str) or not item.strip() for item in fill_ids)
                    or len(fill_ids) != len(set(fill_ids))):
                raise LedgerIntegrityError("position_fingerprint entry_fill_ids invalid")
        if identity_count == 0:
            raise LedgerIntegrityError("position_fingerprint immutable identity missing")
        if fingerprint.get("side") not in (None, "long", "short"):
            raise LedgerIntegrityError("position_fingerprint side invalid")
        for name in ("entry_price", "contracts"):
            if name in fingerprint:
                _require_finite_number(fingerprint, name, allow_zero=False)
    if ("remote_submission_finalized" in record
            and type(record["remote_submission_finalized"]) is not bool):
        raise LedgerIntegrityError("remote_submission_finalized must be bool")
    if "schema_version" in record and record["schema_version"] != SCHEMA_VERSION:
        raise LedgerIntegrityError("ledger schema_version invalid")
    if "seq" in record and (
        not isinstance(record["seq"], int) or isinstance(record["seq"], bool) or record["seq"] < 0
    ):
        raise LedgerIntegrityError("ledger seq invalid")


def make_deterministic_cl_ord_id(
    *, account_id: str, symbol: str, key_id: str, config_hash: str, attempt_epoch: int = 0,
) -> str:
    """key_id는 setup_id(신규진입) 또는 position_epoch(기존 포지션 관리 intent -
    reduce/exit/reversal)다. attempt_epoch가 같으면 항상 같은 clOrdId, 다르면
    완전히 다른 clOrdId - 같은 key_id라도 재시도 epoch가 다르면 절대 이전
    clOrdId를 재사용하지 않는다. OKX 공식 규격상 clOrdId는 대소문자 구분
    영숫자 최대 32자다 - SHA-256 hexdigest의 앞 32자(0-9a-f)는 이 규격을
    문자셋/길이 양면에서 만족한다(tests/test_candidate_c_okx_client_id_contract.py
    로 고정)."""
    digest = hashlib.sha256(
        f"{account_id}|{symbol}|{key_id}|{config_hash}|{attempt_epoch}".encode("utf-8")
    ).hexdigest()
    return digest[:32]


def make_attach_algo_cl_ord_id(main_cl_ord_id: str) -> str:
    """메인 주문 clOrdId로부터 결합 SL/TP bracket의 attachAlgoClOrdId를
    결정론적으로 유도한다 - 메인 clOrdId가 정해지면 항상 같은 bracket ID가
    나온다(별도의 입력을 새로 받지 않음 - 두 ID가 서로 다른 소스에서 각자
    계산돼 우연히 어긋나는 일을 구조적으로 방지)."""
    return hashlib.sha256(f"attach_algo|{main_cl_ord_id}".encode("utf-8")).hexdigest()[:32]


def _checksum(record: dict) -> str:
    payload = {k: v for k, v in record.items() if k != "checksum"}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()


@dataclass
class IntentRecord:
    intent_id: str
    account_id: str
    symbol: str
    strategy_id: str
    setup_id: str | None
    position_epoch: str | None
    config_version_id: str
    config_hash: str
    cl_ord_id: str
    kind: str  # EntryIntent/ReduceIntent/ExitIntent/ReversalIntent 등
    attach_algo_cl_ord_id: str = ""
    attempt_epoch: int = 0
    state: str = IntentState.INTENT_PERSISTED.value
    requested_side: str | None = None
    requested_quantity: float | None = None
    requested_stop_price: float | None = None
    requested_target_price: float | None = None
    contract_size: float | None = None
    lot_step: float | None = None
    min_contracts: float | None = None
    tick_size: float | None = None
    max_contracts: float | None = None
    reservation_id: str | None = None
    reserved_risk_usdt: float | None = None
    gross_notional_usdt: float | None = None
    entry_price_estimate: float | None = None
    filled_quantity: float = 0.0
    # Latest exchange-confirmed position quantity.  Unlike filled_quantity
    # (the immutable parent-entry fill), this shrinks after reductions and is
    # the authoritative protection/recovery size.
    remaining_quantity: float | None = None
    exchange_order_id: str | None = None
    protective_algo_ids: list = field(default_factory=list)
    protective_order_type: str | None = None
    reject_reason: str | None = None
    reconcile_reason: str | None = None
    # [2026-09-16, 사용자 직접 지시 - 모호한 제출 결과 원인 보존] submit_fn()이
    # 던진 예외(또는 id 없는 응답)를 그대로 삼키지 않고 남긴다. 기존
    # reject_reason과 같은 패턴 - 매매 판단에는 전혀 쓰이지 않는 순수 진단용
    # 필드다(출력값 어디에도 outcome 판정 로직이 이 문자열을 읽지 않음).
    submission_unknown_reason: str | None = None
    protection_phase: str | None = None
    target_residual_quantity: float | None = None
    position_fingerprint: dict = field(default_factory=dict)
    remote_submission_finalized: bool = False
    strategy_policy: dict | None = None
    history: list = field(default_factory=list)  # [(state, extra_dict), ...] 감사용

    @property
    def key_id(self) -> str:
        return self.setup_id or self.position_epoch


class IntentLedger:
    """account(user_dir) + symbol 단위 하나의 durable ledger. 파일 하나 =
    (account, symbol) 하나 - 심볼 간 완전히 격리된다(한 심볼의 손상이 다른
    심볼에 전혀 영향을 주지 않는다)."""

    def __init__(self, log_path: str, user_dir: str, symbol: str):
        self.log_path = log_path
        self.quarantine_path = log_path + ".quarantine"
        self.user_dir = user_dir
        self.symbol = symbol
        self._intents: dict[str, IntentRecord] = {}
        self._next_seq = 0
        self._prev_hash = "GENESIS"
        self.load_report: dict = {"corruption_detected": False, "forced_reconcile_intent_ids": []}

    @property
    def _mutation_lock_key(self) -> str:
        # seq/prev_hash is shared by every intent in this symbol ledger, so all
        # mutations must use one ledger-wide lock rather than per-intent locks.
        return f"candidate_c_intent_{self.symbol}"

    def _reload_from_disk_unlocked(self) -> None:
        fresh = type(self)._load_unlocked(
            self.log_path, user_dir=self.user_dir, symbol=self.symbol,
        )
        self._intents = fresh._intents
        self._next_seq = fresh._next_seq
        self._prev_hash = fresh._prev_hash
        self.load_report = fresh.load_report

    @classmethod
    def load(cls, log_path: str, user_dir: str, symbol: str) -> "IntentLedger":
        probe = cls(log_path, user_dir, symbol)
        with process_lock.locked(user_dir, probe._mutation_lock_key):
            return cls._load_unlocked(log_path, user_dir=user_dir, symbol=symbol)

    @classmethod
    def _load_unlocked(cls, log_path: str, user_dir: str, symbol: str) -> "IntentLedger":
        ledger = cls(log_path, user_dir, symbol)
        if not os.path.exists(log_path):
            return ledger

        with open(log_path, "r", encoding="utf-8") as f:
            raw_lines = f.readlines()

        bad_idx = None
        for idx, raw_line in enumerate(raw_lines):
            line = raw_line.strip()
            if not line:
                continue
            try:
                rec = json.loads(
                    line,
                    parse_constant=lambda token: (_ for _ in ()).throw(
                        LedgerIntegrityError(f"non-standard JSON number: {token}")
                    ),
                )
            except json.JSONDecodeError:
                bad_idx = idx
                break
            # A checksum-valid row can still carry NaN/Infinity, an unknown
            # event, or a forged-but-well-hashed schema.  These are semantic
            # integrity failures: preserve the source byte-for-byte and stop.
            _validate_record(rec)
            expected_checksum = rec.get("checksum")
            if expected_checksum is None or _checksum(rec) != expected_checksum:
                bad_idx = idx
                break
            if rec.get("seq") != ledger._next_seq or rec.get("prev_hash") != ledger._prev_hash:
                bad_idx = idx  # 순서 변경/중간 누락 - 체인이 끊김
                break
            ledger._apply(rec)
            ledger._prev_hash = expected_checksum
            ledger._next_seq += 1

        if bad_idx is not None:
            ledger._quarantine_and_truncate(raw_lines, bad_idx)
            ledger.load_report["corruption_detected"] = True

        # 손상 여부와 무관하게: 재시작 시점에 submit_attempted에 멈춰 있는
        # intent는 그 자체로 모호하다(제출 호출이 실제로 실행됐는지, 응답
        # 기록 전에 죽었는지 알 수 없음) - reconcile_required로 강제한다.
        for record_obj in list(ledger._intents.values()):
            if record_obj.state in POST_SUBMIT_ATTEMPT_STATES and record_obj.state != IntentState.RECONCILE_REQUIRED.value \
                    and record_obj.state == IntentState.SUBMIT_ATTEMPTED.value:
                ledger._force_reconcile_required(record_obj.intent_id, "restart_found_unresolved_submit_attempted")

        if bad_idx is not None:
            for record_obj in list(ledger._intents.values()):
                if record_obj.state in POST_SUBMIT_ATTEMPT_STATES and record_obj.state not in (
                    IntentState.RECONCILE_REQUIRED.value,
                ) and record_obj.state not in TERMINAL_STATES:
                    ledger._force_reconcile_required(record_obj.intent_id, "ledger_corruption_detected_on_load")

        return ledger

    def refresh(self) -> None:
        """Refresh the in-memory snapshot while holding the ledger-wide lock.

        Loading may quarantine a corrupt tail or persist a forced reconcile
        transition, so even a nominal read is a mutation-capable operation.
        """
        with process_lock.locked(self.user_dir, self._mutation_lock_key):
            self._reload_from_disk_unlocked()

    def _quarantine_and_truncate(self, raw_lines: list[str], bad_idx: int) -> None:
        """bad_idx부터 EOF까지(손상되었거나 신뢰할 수 없는 tail 전체)를
        quarantine 파일로 옮기고, 원본 로그는 마지막으로 검증된 레코드까지만
        남긴다(원자적 교체 - 절대 원본을 그 자리에서 잘라내지 않는다)."""
        quarantined_tail = "".join(raw_lines[bad_idx:])
        with open(self.quarantine_path, "a", encoding="utf-8") as qf:
            qf.write(f"--- quarantined_at_ms={int(time.time() * 1000)} recovered_seq_start={self._next_seq} ---\n")
            qf.write(quarantined_tail)
            qf.flush()
            os.fsync(qf.fileno())

        good_content = "".join(raw_lines[:bad_idx])
        tmp_path = self.log_path + ".tmp_truncate"
        with open(tmp_path, "w", encoding="utf-8") as tf:
            tf.write(good_content)
            tf.flush()
            os.fsync(tf.fileno())
        os.replace(tmp_path, self.log_path)

    def _force_reconcile_required(self, intent_id: str, reason: str) -> None:
        self._append({
            "event": "transition", "intent_id": intent_id, "state": IntentState.RECONCILE_REQUIRED.value,
            "reconcile_reason": reason,
        })
        self._apply({
            "event": "transition", "intent_id": intent_id, "state": IntentState.RECONCILE_REQUIRED.value,
            "reconcile_reason": reason,
        })
        self.load_report["forced_reconcile_intent_ids"].append(intent_id)

    def _append(self, record: dict) -> None:
        record = dict(
            record, schema_version=SCHEMA_VERSION, seq=self._next_seq, prev_hash=self._prev_hash,
        )
        _validate_record(record)
        record["checksum"] = _checksum(record)
        _validate_record(record)
        directory = os.path.dirname(self.log_path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, allow_nan=False) + "\n")
            f.flush()
            os.fsync(f.fileno())
        self._prev_hash = record["checksum"]
        self._next_seq += 1

    def _apply(self, rec: dict) -> None:
        intent_id = rec["intent_id"]
        if rec["event"] == "created":
            self._intents[intent_id] = IntentRecord(
                intent_id=intent_id, account_id=rec["account_id"], symbol=rec["symbol"],
                strategy_id=rec["strategy_id"], setup_id=rec.get("setup_id"), position_epoch=rec.get("position_epoch"),
                config_version_id=rec["config_version_id"], config_hash=rec["config_hash"],
                cl_ord_id=rec["cl_ord_id"], kind=rec["kind"], attempt_epoch=rec.get("attempt_epoch", 0),
                attach_algo_cl_ord_id=rec.get("attach_algo_cl_ord_id") or make_attach_algo_cl_ord_id(rec["cl_ord_id"]),
                requested_side=rec.get("requested_side"), requested_quantity=rec.get("requested_quantity"),
                requested_stop_price=rec.get("requested_stop_price"),
                requested_target_price=rec.get("requested_target_price"),
                contract_size=rec.get("contract_size"),
                lot_step=rec.get("lot_step"), min_contracts=rec.get("min_contracts"),
                tick_size=rec.get("tick_size"), max_contracts=rec.get("max_contracts"),
                reservation_id=rec.get("reservation_id"),
                reserved_risk_usdt=rec.get("reserved_risk_usdt"),
                gross_notional_usdt=rec.get("gross_notional_usdt"),
                entry_price_estimate=rec.get("entry_price_estimate"),
                protective_order_type=rec.get("protective_order_type"),
                remaining_quantity=rec.get("remaining_quantity"),
                remote_submission_finalized=rec.get("remote_submission_finalized", False),
                strategy_policy=rec.get("strategy_policy"),
            )
            self._intents[intent_id].history.append(("intent_persisted", {}))
        else:
            record_obj = self._intents.get(intent_id)
            if record_obj is None:
                return
            if (record_obj.state == IntentState.TERMINAL.value
                    and rec["state"] != IntentState.TERMINAL.value):
                raise LedgerIntegrityError(
                    f"terminal intent {intent_id} cannot transition to {rec['state']}"
                )
            record_obj.state = rec["state"]
            if "exchange_order_id" in rec and rec["exchange_order_id"]:
                record_obj.exchange_order_id = rec["exchange_order_id"]
            if "filled_quantity" in rec:
                record_obj.filled_quantity = rec["filled_quantity"]
                if record_obj.remaining_quantity is None:
                    record_obj.remaining_quantity = rec["filled_quantity"]
            if "remaining_quantity" in rec:
                record_obj.remaining_quantity = rec["remaining_quantity"]
            if "protective_algo_ids" in rec:
                record_obj.protective_algo_ids = rec["protective_algo_ids"]
            if "reject_reason" in rec:
                record_obj.reject_reason = rec["reject_reason"]
            if "submission_unknown_reason" in rec:
                record_obj.submission_unknown_reason = rec["submission_unknown_reason"]
            if "reconcile_reason" in rec:
                record_obj.reconcile_reason = rec["reconcile_reason"]
            if "protection_phase" in rec:
                record_obj.protection_phase = rec["protection_phase"]
            if "target_residual_quantity" in rec:
                record_obj.target_residual_quantity = rec["target_residual_quantity"]
            if "position_fingerprint" in rec:
                record_obj.position_fingerprint = dict(rec["position_fingerprint"])
            if "remote_submission_finalized" in rec:
                record_obj.remote_submission_finalized = rec["remote_submission_finalized"]
            record_obj.history.append((rec["state"], {
                k: v for k, v in rec.items()
                if k not in ("event", "intent_id", "state", "schema_version", "checksum", "seq", "prev_hash")
            }))

    def get(self, intent_id: str) -> IntentRecord:
        if intent_id not in self._intents:
            raise IntentNotFoundError(intent_id)
        return self._intents[intent_id]

    def find_by_key(self, *, account_id: str, symbol: str, key_id: str, config_hash: str, attempt_epoch: int = 0) -> IntentRecord | None:
        cl_ord_id = make_deterministic_cl_ord_id(
            account_id=account_id, symbol=symbol, key_id=key_id, config_hash=config_hash, attempt_epoch=attempt_epoch,
        )
        for rec in self._intents.values():
            if rec.cl_ord_id == cl_ord_id:
                return rec
        return None

    def latest_attempt_epoch(self, key_id: str) -> int:
        """이 key_id(setup_id 또는 position_epoch)로 지금까지 만들어진 intent
        중 가장 높은 attempt_epoch. 아직 하나도 없으면 -1(호출부가 +1해서 첫
        epoch=0을 만들 수 있게)."""
        epochs = [r.attempt_epoch for r in self._intents.values() if r.key_id == key_id]
        return max(epochs) if epochs else -1

    def pending_intents(self) -> list[IntentRecord]:
        """terminal이 아닌 모든 intent - 재시작 시 반드시 이 목록부터 reconcile.
        submission_unknown/reconcile_required/partial_fill도 여기 포함된다."""
        return [r for r in self._intents.values() if r.state not in TERMINAL_STATES]

    def records(self) -> list[IntentRecord]:
        """Fresh audit/recovery view, including terminal entries and management."""
        with process_lock.locked(self.user_dir, self._mutation_lock_key):
            self._reload_from_disk_unlocked()
            return list(self._intents.values())

    def entry_records(self) -> list[IntentRecord]:
        return [record for record in self.records() if record.kind == "EntryIntent"]

    def reservation_ids(self) -> set[str]:
        """Return reservation IDs from a lock-protected fresh disk replay."""
        with process_lock.locked(self.user_dir, self._mutation_lock_key):
            self._reload_from_disk_unlocked()
            return {
                record.reservation_id for record in self._intents.values()
                if record.reservation_id
            }

    def find_protected_entry(self) -> tuple[IntentRecord | None, bool]:
        """Phase 3.9 - 이 (account, symbol)의 열린(터미널 아닌) PROTECTED
        EntryIntent를 정확히 하나 찾는다. 0개면 (None, False), 1개면
        (record, False), 2개 이상이면 (None, True)로 모호함을 알린다(같은
        심볼에 동시에 두 개의 보호된 진입이 있을 수 없다 - 구조적 이상,
        절대 추측으로 하나를 고르지 않는다)."""
        protected = [
            r for r in self._intents.values()
            if r.kind == "EntryIntent" and r.state == IntentState.PROTECTED.value
        ]
        if len(protected) == 0:
            return None, False
        if len(protected) > 1:
            return None, True
        return protected[0], False

    def has_ambiguous_pending_intent(self) -> bool:
        """RECONCILE_REQUIRED/SUBMISSION_UNKNOWN 상태의 intent가 남아있으면
        이 심볼의 상태 자체가 확정 불가하다 - 신규진입을 막아야 한다."""
        return any(
            r.state in (IntentState.RECONCILE_REQUIRED.value, IntentState.SUBMISSION_UNKNOWN.value)
            for r in self._intents.values()
        )

    def has_unfinished_entry_intent(self) -> bool:
        """Any entry that has not reached terminal or fully protected blocks
        admission of another setup for this symbol."""
        return any(
            r.kind == "EntryIntent"
            and r.state not in (
                IntentState.TERMINAL.value, IntentState.PROTECTED.value,
            )
            for r in self._intents.values()
        )

    def read_quarantine(self) -> str:
        if not os.path.exists(self.quarantine_path):
            return ""
        with open(self.quarantine_path, encoding="utf-8") as f:
            return f.read()

    # --- 상태 전이 ---

    def persist_intent(
        self, *, account_id: str, symbol: str, strategy_id: str, setup_id: str | None,
        position_epoch: str | None, config_version_id: str, config_hash: str, kind: str,
        requested_side: str | None = None, requested_quantity: float | None = None,
        requested_stop_price: float | None = None, requested_target_price: float | None = None,
        contract_size: float | None = None, lot_step: float | None = None,
        min_contracts: float | None = None, tick_size: float | None = None,
        max_contracts: float | None = None,
        reservation_id: str | None = None, reserved_risk_usdt: float | None = None,
        gross_notional_usdt: float | None = None,
        entry_price_estimate: float | None = None,
        strategy_policy: dict | None = None,
        attempt_epoch: int = 0,
    ) -> IntentRecord:
        with process_lock.locked(self.user_dir, self._mutation_lock_key):
            self._reload_from_disk_unlocked()
            return self._persist_intent_unlocked(
                account_id=account_id, symbol=symbol, strategy_id=strategy_id,
                setup_id=setup_id, position_epoch=position_epoch,
                config_version_id=config_version_id, config_hash=config_hash, kind=kind,
                requested_side=requested_side, requested_quantity=requested_quantity,
                requested_stop_price=requested_stop_price,
                requested_target_price=requested_target_price,
                contract_size=contract_size, lot_step=lot_step,
                min_contracts=min_contracts, tick_size=tick_size,
                max_contracts=max_contracts, reservation_id=reservation_id,
                reserved_risk_usdt=reserved_risk_usdt,
                gross_notional_usdt=gross_notional_usdt,
                entry_price_estimate=entry_price_estimate, attempt_epoch=attempt_epoch,
                strategy_policy=strategy_policy,
            )

    def _persist_intent_unlocked(
        self, *, account_id: str, symbol: str, strategy_id: str, setup_id: str | None,
        position_epoch: str | None, config_version_id: str, config_hash: str, kind: str,
        requested_side: str | None = None, requested_quantity: float | None = None,
        requested_stop_price: float | None = None, requested_target_price: float | None = None,
        contract_size: float | None = None, lot_step: float | None = None,
        min_contracts: float | None = None, tick_size: float | None = None,
        max_contracts: float | None = None,
        reservation_id: str | None = None, reserved_risk_usdt: float | None = None,
        gross_notional_usdt: float | None = None,
        entry_price_estimate: float | None = None,
        strategy_policy: dict | None = None,
        attempt_epoch: int = 0,
    ) -> IntentRecord:
        """API 호출 "전"에 반드시 이 메서드부터 호출해야 한다. 같은
        (key, config_hash, attempt_epoch)로 이미 intent가 존재하면 새로
        만들지 않고 기존 것을 반환한다(중복 생성 방지). attempt_epoch를 올려
        호출하면 완전히 새로운 clOrdId를 가진, 이전 intent와 독립적인 새
        intent가 생성된다 - reconcile()이 "거래소에 없음"을 확정한 뒤에만
        호출부가 attempt_epoch+1로 재시도해야 한다(같은 epoch 재사용 금지)."""
        key_id = setup_id or position_epoch
        if key_id is None:
            raise LedgerError("setup_id 또는 position_epoch 중 하나는 반드시 있어야 함")
        cl_ord_id = make_deterministic_cl_ord_id(
            account_id=account_id, symbol=symbol, key_id=key_id, config_hash=config_hash, attempt_epoch=attempt_epoch,
        )
        colliding = self._intents.get(cl_ord_id)
        if colliding is not None:
            if colliding.key_id == key_id and colliding.attempt_epoch == attempt_epoch:
                return colliding  # 같은 intent에 대한 멱등 재호출
            raise ClOrdIdCollisionError(
                f"clOrdId {cl_ord_id}가 다른 key_id/attempt_epoch(key_id={colliding.key_id}, "
                f"attempt_epoch={colliding.attempt_epoch})로 이미 존재함 - 재사용 금지"
            )
        intent_id = cl_ord_id
        payload = {
            "event": "created", "intent_id": intent_id, "account_id": account_id, "symbol": symbol,
            "strategy_id": strategy_id, "setup_id": setup_id, "position_epoch": position_epoch,
            "config_version_id": config_version_id, "config_hash": config_hash, "cl_ord_id": cl_ord_id,
            "attach_algo_cl_ord_id": make_attach_algo_cl_ord_id(cl_ord_id),
            "kind": kind, "attempt_epoch": attempt_epoch, "requested_side": requested_side,
            "requested_quantity": requested_quantity, "requested_stop_price": requested_stop_price,
            "requested_target_price": requested_target_price, "contract_size": contract_size,
            "lot_step": lot_step, "min_contracts": min_contracts,
            "tick_size": tick_size, "max_contracts": max_contracts,
            "reservation_id": reservation_id, "reserved_risk_usdt": reserved_risk_usdt,
            "gross_notional_usdt": gross_notional_usdt,
            "entry_price_estimate": entry_price_estimate,
            "strategy_policy": strategy_policy,
            "protective_order_type": (
                "oco" if requested_target_price is not None else "conditional"
            ) if kind == "EntryIntent" else None,
            "state": IntentState.INTENT_PERSISTED.value,
            "remote_submission_finalized": False,
        }
        self._append(payload)
        self._apply(payload)
        return self._intents[intent_id]

    def attempt_submit(self, intent_id: str, submit_fn) -> IntentRecord:
        with process_lock.locked(self.user_dir, self._mutation_lock_key):
            self._reload_from_disk_unlocked()
            return self._attempt_submit_unlocked(intent_id, submit_fn)

    def _attempt_submit_unlocked(self, intent_id: str, submit_fn) -> IntentRecord:
        """process_lock으로 보호된 임계구역 안에서만 submit_fn()을 호출한다 -
        동시에 여러 스레드/프로세스가 같은 intent_id로 이 메서드를 불러도
        submit_fn()은 최대 1회만 실행된다. submit_fn은 실제(또는 fake) 거래소
        제출을 수행하고 {"outcome": "ack"|"unknown"|"rejected", ...}를
        반환해야 한다 - 이 함수는 절대 실제 OKX API를 직접 호출하지 않는다
        (submit_fn 주입은 호출부 책임).

        state가 submission_unknown 또는 reconcile_required면 submit_fn을
        절대 호출하지 않고(0회) 즉시 예외를 던진다 - process_lock 진입조차
        하지 않는다."""
        record = self.get(intent_id)
        if record.state == IntentState.RECONCILE_REQUIRED.value:
            raise ReconcileRequiredError(
                f"intent {intent_id}는 reconcile_required 상태 - 실제 거래소 조회로 해소하기 전까지 제출 금지"
            )
        if record.state == IntentState.SUBMISSION_UNKNOWN.value:
            raise BlindRetryBlockedError(
                f"intent {intent_id}는 submission_unknown 상태 - reconcile 없이 재제출 금지"
            )
        if record.state != IntentState.INTENT_PERSISTED.value:
            return record  # 이미 제출 시도가 있었음 - 재실행 안 함(최대 1회 보장)

        with process_lock.locked(self.user_dir, f"candidate_c_intent_{self.symbol}_{intent_id}"):
            record = self.get(intent_id)
            if record.state != IntentState.INTENT_PERSISTED.value:
                return record  # 락 획득 사이에 다른 스레드가 이미 처리함
            self._append({"event": "transition", "intent_id": intent_id, "state": IntentState.SUBMIT_ATTEMPTED.value})
            self._apply({"event": "transition", "intent_id": intent_id, "state": IntentState.SUBMIT_ATTEMPTED.value})

            try:
                result = submit_fn()
            except Exception as exc:
                # A transport or local parsing failure is never proof of rejection -
                # the outcome stays "unknown" either way. But the exception itself
                # must not be thrown away silently (2026-09-16, 사용자 직접 지시 -
                # 첫 실거래 제출에서 이 예외가 삼켜져 원인을 알 수 없었던 실제
                # 사고 이후) - purely diagnostic, never read by any outcome/control
                # branch below.
                result = {"outcome": "unknown", "unknown_reason": f"{type(exc).__name__}: {exc}"[:500]}
            if not isinstance(result, dict):
                result = {"outcome": "unknown"}
            outcome = result.get("outcome")
            if outcome == "ack":
                payload = {
                    "event": "transition", "intent_id": intent_id, "state": IntentState.EXCHANGE_ACK.value,
                    "exchange_order_id": result.get("exchange_order_id"),
                }
                self._append(payload)
                self._apply(payload)
            elif outcome == "rejected":
                payload = {"event": "transition", "intent_id": intent_id, "state": IntentState.REJECTED.value, "reject_reason": result.get("reason")}
                self._append(payload)
                self._apply(payload)
                self.mark_terminal(intent_id)
            else:
                # timeout/연결단절/애매한 응답 - 실패로 단정하지 않고 unknown으로 기록.
                payload = {"event": "transition", "intent_id": intent_id, "state": IntentState.SUBMISSION_UNKNOWN.value}
                if result.get("unknown_reason"):
                    payload["submission_unknown_reason"] = result["unknown_reason"]
                self._append(payload)
                self._apply(payload)
            return self.get(intent_id)

    def mark_submission_unknown(self, intent_id: str) -> None:
        self._mark_transition(intent_id, IntentState.SUBMISSION_UNKNOWN.value)

    def reconcile(self, intent_id: str, reconcile_fn) -> IntentRecord:
        with process_lock.locked(self.user_dir, self._mutation_lock_key):
            self._reload_from_disk_unlocked()
            return self._reconcile_unlocked(intent_id, reconcile_fn)

    def _reconcile_unlocked(self, intent_id: str, reconcile_fn) -> IntentRecord:
        """submission_unknown 또는 reconcile_required 상태를 실제 거래소
        clOrdId/주문/체결/포지션 조회로 해소한다. reconcile_fn()은
        {"found": True/False, "exchange_order_id":, "filled_quantity":, ...}를
        반환해야 한다(실제 조회 방법은 호출부 책임 - 여기서는 실제 API를
        호출하지 않는다).

        found=False(거래소에 정말로 도달한 흔적이 없음이 확정)면
        confirmed_not_submitted(터미널)로 보내고 절대 intent_persisted로
        되돌리지 않는다 - 같은 clOrdId 재제출은 영구히 금지된다. 재시도가
        필요하면 호출부가 attempt_epoch를 올려 새 intent를 만들어야 한다."""
        record = self.get(intent_id)
        if record.state not in (IntentState.SUBMISSION_UNKNOWN.value, IntentState.RECONCILE_REQUIRED.value):
            return record
        result = reconcile_fn()
        # F5(2026-09-17, 외부 검토 지적 + 직접 재현 확인) - {}/None/"found" 키
        # 누락/bool이 아닌 값은 "거래소에 없음이 확인됨"이 아니라 "확인 자체가
        # 안 됨"이다. 예전에는 falsy이기만 하면(빈 딕셔너리 포함) 곧바로
        # confirmed_not_submitted(터미널 - 같은 clOrdId 영구 재제출 금지)로
        # 보냈다 - 애매한 응답과 확정된 부재를 구분하지 못하는 결함이었다.
        # 판단을 미루고 현재 상태 그대로 둔다(재시도 자체는 호출부가 나중에
        # 더 나은 근거로 다시 reconcile()을 부르면 됨).
        if not isinstance(result, dict) or not isinstance(result.get("found"), bool):
            return record
        if not result["found"]:
            payload = {"event": "transition", "intent_id": intent_id, "state": IntentState.CONFIRMED_NOT_SUBMITTED.value}
            self._append(payload)
            self._apply(payload)
            self.mark_terminal(intent_id)
            return self.get(intent_id)
        filled_qty = result.get("filled_quantity", 0.0)
        if filled_qty <= 0:
            state = IntentState.EXCHANGE_ACK.value
        elif filled_qty >= (record.requested_quantity or 0):
            state = IntentState.FILLED.value
        else:
            state = IntentState.PARTIAL_FILL.value
        payload = {
            "event": "transition", "intent_id": intent_id, "state": state,
            "exchange_order_id": result.get("exchange_order_id"), "filled_quantity": filled_qty,
        }
        self._append(payload)
        self._apply(payload)
        return self.get(intent_id)

    def mark_partial_fill(self, intent_id: str, filled_quantity: float) -> None:
        self._mark_transition(
            intent_id, IntentState.PARTIAL_FILL.value,
            filled_quantity=filled_quantity, remaining_quantity=filled_quantity,
        )

    def mark_filled(self, intent_id: str, filled_quantity: float) -> None:
        self._mark_transition(
            intent_id, IntentState.FILLED.value,
            filled_quantity=filled_quantity, remaining_quantity=filled_quantity,
        )

    def mark_residual_quantity(self, intent_id: str, remaining_quantity: float) -> None:
        with process_lock.locked(self.user_dir, self._mutation_lock_key):
            self._reload_from_disk_unlocked()
            record = self._intents.get(intent_id)
            if record is None or record.state == IntentState.TERMINAL.value:
                return
            payload = {
                "event": "transition", "intent_id": intent_id,
                "state": record.state, "remaining_quantity": remaining_quantity,
            }
            self._append(payload)
            self._apply(payload)

    def mark_position_fingerprint(self, intent_id: str, fingerprint: dict) -> None:
        with process_lock.locked(self.user_dir, self._mutation_lock_key):
            self._reload_from_disk_unlocked()
            record = self._intents.get(intent_id)
            if record is None or record.state == IntentState.TERMINAL.value:
                return
            proposed = dict(fingerprint)
            if record.position_fingerprint:
                if record.position_fingerprint != proposed:
                    raise LedgerIntegrityError(
                        "immutable position fingerprint cannot be replaced"
                    )
                return
            payload = {
                "event": "transition", "intent_id": intent_id,
                "state": record.state,
                "position_fingerprint": proposed,
            }
            if record.state in (
                IntentState.PARTIAL_FILL.value, IntentState.FILLED.value,
            ):
                payload["filled_quantity"] = record.filled_quantity
            if record.state == IntentState.PROTECTED.value:
                payload["protective_algo_ids"] = list(record.protective_algo_ids)
            self._append(payload)
            self._apply(payload)

    def mark_protection_pending(self, intent_id: str) -> None:
        self._mark_transition(intent_id, IntentState.PROTECTION_PENDING.value)

    def mark_protected(self, intent_id: str, protective_algo_ids: list) -> None:
        self._mark_transition(
            intent_id, IntentState.PROTECTED.value,
            protective_algo_ids=protective_algo_ids, protection_phase="complete",
        )

    def mark_remote_submission_finalized(self, intent_id: str) -> None:
        with process_lock.locked(self.user_dir, self._mutation_lock_key):
            self._reload_from_disk_unlocked()
            record = self.get(intent_id)
            if record.remote_submission_finalized:
                return
            payload = {
                "event": "transition", "intent_id": intent_id,
                "state": record.state,
                "remote_submission_finalized": True,
            }
            if record.state == IntentState.PROTECTED.value:
                payload["protective_algo_ids"] = list(record.protective_algo_ids)
            self._append(payload)
            self._apply(payload)

    def mark_reduce_protection_pending(
        self, intent_id: str, target_residual_quantity: float,
    ) -> None:
        """Keep position ownership protected while its post-reduce OCO resize is pending."""
        self._mark_transition(
            intent_id, IntentState.PROTECTED.value,
            protection_phase="reduce_resize_pending",
            target_residual_quantity=target_residual_quantity,
        )

    def mark_terminal(self, intent_id: str) -> None:
        self._mark_transition(intent_id, IntentState.TERMINAL.value)

    def _mark_transition(self, intent_id: str, state: str, **extra) -> None:
        with process_lock.locked(self.user_dir, self._mutation_lock_key):
            self._reload_from_disk_unlocked()
            if intent_id not in self._intents:
                return
            if (self._intents[intent_id].state == IntentState.TERMINAL.value
                    and state != IntentState.TERMINAL.value):
                return
            payload = {"event": "transition", "intent_id": intent_id, "state": state, **extra}
            self._append(payload)
            self._apply(payload)
