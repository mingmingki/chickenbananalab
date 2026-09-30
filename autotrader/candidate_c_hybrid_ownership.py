"""CORE와 Candidate C(신규 DOGE/SOL 엔진)가 같은 OKX 계좌에서 동시운영될 때 필요한
심볼 소유권 검증 + 전환 시 잔존 포지션 확인 + 계좌 공통 주문 락.

기존 Candidate C(candidate_c_exclusive_account_evidence 등)의 "이 계좌엔 나 혼자만
있어야 한다" 전제를 재사용하지 않는다 - 정반대로 "CORE와 서로 다른 심볼을 나눠 갖고
같은 계좌에서 공존한다"는 전제로 새로 설계했다."""
import candidate_c_reversal_state_machine as rsm
import process_lock
import os


class SymbolOwnershipConflictError(RuntimeError):
    """CORE와 Candidate C가 같은 심볼을 동시에 스캔하려고 함 - fail-closed.
    이 예외가 발생하면 두 엔진 다 시작하지 않아야 한다(어느 한쪽만 시작하는 것도
    허용하지 않는다 - 설정 자체가 잘못됐다는 신호이기 때문)."""


def compute_symbol_ownership(core_symbols: list, candidate_c_symbols: list) -> dict:
    """CORE와 Candidate C의 심볼 목록을 받아 교집합이 있으면 예외를 던진다.
    겹치지 않으면 각각의 소유 심볼 집합을 반환한다."""
    core_set = set(core_symbols)
    cc_set = set(candidate_c_symbols)
    overlap = core_set & cc_set
    if overlap:
        raise SymbolOwnershipConflictError(
            "CORE와 Candidate C가 같은 심볼을 동시에 소유하려 함(중복 스캔/중복 주문 "
            f"위험, fail-closed): {sorted(overlap)}"
        )
    return {"core": core_set, "candidate_c": cc_set}


def check_foreign_position_before_candidate_c_takes_symbol(
    client, symbol: str, *, core_active_symbols=None,
) -> dict:
    """Candidate C가 이 심볼을 새로 맡기 전에, 거래소에 이미 열려있는 포지션이 있는지
    확인한다(예: DOGE가 CORE 시절에 열어놓은 포지션이 남아있는 경우). 포지션이 있으면
    reconciliation_required=True를 반환한다 - 이 함수는 아무 것도 자동으로 정리하지
    않는다(fail-closed, 수동 확인 필요). client.fetch_position()은 GET-only 조회다."""
    if core_active_symbols is not None and symbol in core_active_symbols:
        return {"reconciliation_required": True, "position": None, "reason": "core_entry_owner_active"}
    try:
        position = client.fetch_position()
        # An explicit handoff must also prove that CORE left no pending orders.
        if core_active_symbols is not None:
            orders = client.exchange.fetch_open_orders(symbol)
            algos = client.fetch_pending_protection_algo_ids()
            if orders is None or algos is None:
                raise RuntimeError("order state UNKNOWN")
            if orders or algos:
                return {"reconciliation_required": True, "position": position, "reason": "outstanding_orders"}
    except Exception:
        return {"reconciliation_required": True, "position": None, "reason": "exchange_state_UNKNOWN"}
    contracts = (position or {}).get("contracts") or 0
    if position is not None and contracts:
        return {"reconciliation_required": True, "position": position}
    return {"reconciliation_required": False, "position": None}


ACCOUNT_ORDER_LOCK_KEY = "candidate_c_hybrid_account_order_lock"


def service_start_lock(user_dir: str):
    """Serialize account starts with operator quiescence of the shared service."""
    return process_lock.locked(os.path.dirname(os.path.realpath(user_dir)), 'service_loop_admission_lock')


def account_order_lock(user_dir: str):
    """주문 생성/취소/보호주문 갱신 전체가 공유하는 계좌 공통 락. CORE와 Candidate C
    양쪽의 주문 관련 코드가 이 컨텍스트 매니저를 거치면, 스레드 경계와 무관하게
    직렬화된다(process_lock.locked 재사용 - 이미 candidate_c_exit_management.py의
    epoch 저장에 쓰이는 것과 동일한, in-process RLock + 파일 fcntl 락 조합)."""
    return process_lock.locked(user_dir, ACCOUNT_ORDER_LOCK_KEY)


class AdmissionStateUnknown(RuntimeError):
    """An incomplete exchange snapshot cannot establish available capacity."""


def count_candidate_c_open_or_pending_positions(
    clients: dict, reversal_store, *, current_symbol: str | None = None,
    user_dir: str | None = None,
) -> int:
    """Count occupied Candidate C symbols; unknown state cannot grant capacity.

    Production supplies current_symbol and user_dir. Only the current request's
    unsubmitted machine reservation is excluded, after exchange flat/no-orders
    and fresh durable ledger checks. Every other pending reservation counts.
    Unknown durable outcomes block admission regardless of the configured limit.
    The caller holds account_order_lock from reservation through this check and
    exchange submission; all symbol clients share the same reversal store.
    """
    count = 0
    for symbol, client in clients.items():
        durable_occupied = False
        if user_dir is not None:
            import candidate_c_intent_ledger as il
            path = f"{user_dir}/candidate_c_intent_ledger_{symbol.replace('/', '_').replace(':', '_')}.jsonl"
            ledger = il.IntentLedger.load(path, user_dir, symbol)
            if ledger.has_ambiguous_pending_intent() or ledger.has_unfinished_entry_intent():
                raise AdmissionStateUnknown(f"{symbol}: durable reservation UNKNOWN")
            durable_occupied = ledger.find_protected_entry()[0] is not None
        try:
            position = client.fetch_position()
        except Exception as exc:
            raise AdmissionStateUnknown(f"{symbol}: position UNKNOWN") from exc
        if position is not None:
            count += 1
            continue
        if current_symbol is not None:
            try:
                orders = client.exchange.fetch_open_orders(symbol)
                algos = client.fetch_pending_protection_algo_ids()
                if orders is None or algos is None:
                    raise RuntimeError("order query UNKNOWN")
            except Exception as exc:
                raise AdmissionStateUnknown(f"{symbol}: orders UNKNOWN") from exc
            if orders or algos or durable_occupied:
                count += 1
                continue
        machine = reversal_store.get(symbol)
        if machine.state == rsm.State.SAFE_HALT:
            if current_symbol is not None:
                raise AdmissionStateUnknown(f"{symbol}: reservation UNKNOWN (SAFE_HALT)")
            count += 1
        elif (symbol != current_symbol
              and machine.state in (rsm.State.ENTRY_PENDING, rsm.State.REVERSAL_ENTRY_PENDING)):
            count += 1
    return count


def validate_candidate_c_position_owner(
    client, symbol: str, intent_ledger, epoch_store, *, expected_position_id=None,
    allow_missing_protection: bool = False,
) -> dict:
    """Read-only ownership proof. Caller holds the account lock through mutation.

    A strategy label alone grants no authority: durable entry identity, epoch,
    exchange side/size and owned order IDs must agree. SAFE_HALT may explicitly
    allow a missing owned bracket, but never an unknown or foreign bracket.
    """
    import candidate_c_position_reconciliation as recon

    def blocked(reason):
        return {"allowed": False, "reason": reason, "position": None, "entry_record": None}

    if intent_ledger is None or epoch_store is None:
        return blocked("ownership_evidence_missing")
    try:
        record, ambiguous = intent_ledger.find_protected_entry()
        if record is None and allow_missing_protection and not ambiguous:
            pending = [r for r in intent_ledger.pending_intents()
                       if r.kind == "EntryIntent" and r.state == "protection_pending"]
            if len(pending) == 1:
                record = pending[0]
        unknown_actions = [r for r in intent_ledger.pending_intents()
                           if r.state in ("submission_unknown", "reconcile_required")]
        owned_pending_stop_only = (allow_missing_protection and record is not None
            and unknown_actions and all(
                r.kind == "StopUpdateIntent" and r.account_id == record.account_id
                and r.symbol == symbol and r.strategy_id == "candidate_c"
                and str(r.position_epoch).startswith(record.intent_id + ":")
                for r in unknown_actions))
        if ambiguous or (unknown_actions and not owned_pending_stop_only):
            return blocked("ownership_UNKNOWN")
        if record is None or record.symbol != symbol or record.strategy_id != "candidate_c":
            return blocked("candidate_c_entry_identity_missing")
        if not record.remote_submission_finalized:
            return blocked("entry_submission_not_finalized")
        if expected_position_id is not None and record.intent_id != expected_position_id:
            return blocked("position_epoch_mismatch")
        epoch = epoch_store.get(record.intent_id)
        if not epoch.entry_intent_id or epoch.entry_intent_id != record.intent_id:
            return blocked("position_epoch_missing")
        if (epoch.account_id != record.account_id or epoch.symbol != symbol
                or epoch.side != record.requested_side):
            return blocked("position_epoch_owner_mismatch")
        position = client.fetch_position()
        if position is not None:
            info = position.get("info") or {}
            fingerprint = getattr(record, "position_fingerprint", {}) or {}
            expected_id = epoch.exchange_position_id or fingerprint.get("position_id")
            expected_time = epoch.exchange_entry_timestamp_ms or fingerprint.get("entry_timestamp_ms")
            if expected_id is None and expected_time is None:
                return blocked("ownership_evidence_missing")
            actual_id = position.get("position_id") or info.get("posId")
            actual_time = position.get("entry_timestamp_ms") or info.get("cTime")
            if expected_id is not None and str(actual_id) != str(expected_id):
                return blocked("exchange_position_identity_mismatch")
            if expected_time is not None and str(actual_time) != str(expected_time):
                return blocked("exchange_entry_timestamp_mismatch")
            expected_price = epoch.raw_entry_price or fingerprint.get("entry_price")
            actual_price = position.get("entry_price") or position.get("entryPrice") or info.get("avgPx")
            if expected_price is None or actual_price is None or float(expected_price) != float(actual_price):
                return blocked("exchange_entry_price_mismatch")
        orders = client.exchange.fetch_open_orders(symbol)
        algos = client.fetch_pending_protection_algo_ids()
        if orders is None or algos is None:
            return blocked("exchange_state_UNKNOWN")
        if orders:
            return blocked("unreconciled_open_orders")
        internal = recon.build_internal_trade_log_record(record, epoch)
        actual_algos = set(algos)
        recorded_algos = set(record.protective_algo_ids or [])
        # [2026-09-16, 사용자 직접 지시 - 상태6 경우A 최소 수정] record 자신의
        # protective_algo_ids는 오직 ledger.mark_protected()가 호출될 때만 채워진다
        # (candidate_c_intent_ledger.py) - "체결 확인 후 보호 확정 전" 구간에서는
        # 거래소 보호주문이 실제로 우리 것이어도 이 필드가 아직 비어있어 이전에는
        # 무조건 "foreign_protective_orders"로 오판했다(실제 재현 확인됨). epoch의
        # protective_algo_ids(epoch_store.save()가 mark_protected()보다 먼저,
        # protection 검증이 실제로 끝난 시점에 우리 코드 자신이 기록함, 외부에서
        # 위조 불가)를 오직 record 자신의 필드가 "아직 비어있을 때만" 대체 증거로
        # 받아들인다 - record에 이미 다른 값이 기록돼 있다면 그건 여전히 진짜
        # 불일치이므로 그대로 foreign으로 막는다(대체 조건 자체를 좁게 유지).
        used_epoch_fallback = False
        if not recorded_algos and epoch.protective_algo_ids:
            recorded_algos = set(epoch.protective_algo_ids)
            used_epoch_fallback = True
        if actual_algos - recorded_algos:
            return blocked("foreign_protective_orders")
        if allow_missing_protection and not actual_algos:
            import math
            risk = record.reserved_risk_usdt
            if (position is None or position.get("side") != record.requested_side
                    or position.get("contracts") != internal["contracts"]
                    or not record.config_version_id or not isinstance(risk, (int, float))
                    or not math.isfinite(risk) or risk <= 0):
                return blocked("unprotected_position_identity_mismatch")
            return {"allowed": True, "reason": None, "position": position, "entry_record": record}
        if used_epoch_fallback:
            # reconcile_symbol_at_startup()도 internal["protective_algo_ids"](=
            # record 자신의 필드, 여전히 비어있음)로 같은 불일치를 다시 낼 것이다 -
            # 아래 확인에서는 방금 신뢰한 epoch 값으로 일관되게 채운다.
            internal = dict(internal, protective_algo_ids=sorted(recorded_algos))
        snap = recon.SymbolSnapshot(
            symbol=symbol, exchange_position=position, exchange_open_orders=orders,
            exchange_protective_algo_ids=algos, internal_trade_log_record=internal,
            internal_risk_reservation=record.reserved_risk_usdt,
        )
        result = recon.reconcile_symbol_at_startup(snap)
        if result["status"] != recon.STATUS_ADOPTED:
            return blocked(result["reason"])
        return {
            "allowed": True, "reason": None, "position": position, "entry_record": record,
            # 호출부가 이 값을 보고 ledger.mark_protected()로 뒤늦게 따라잡아야
            # 한다(이 함수 자신은 read-only 계약이라 여기서 직접 쓰지 않는다) -
            # 그래야 다음 사이클부터는 이 대체 경로 없이도 정상적으로 발견된다.
            "needs_protected_catch_up": list(actual_algos) if used_epoch_fallback else None,
        }
    except Exception:
        return blocked("ownership_UNKNOWN")


def validate_candidate_c_orphan_protection_owner(client, symbol, intent_ledger, epoch_store) -> dict:
    """Authorize only explicitly recorded residual algo IDs after confirmed flat."""
    blocked = {"allowed": False, "reason": "orphan_ownership_UNKNOWN", "algo_ids": []}
    if intent_ledger is None or epoch_store is None:
        return blocked
    try:
        record, ambiguous = intent_ledger.find_protected_entry()
        if (record is None or ambiguous or intent_ledger.has_ambiguous_pending_intent()
                or record.symbol != symbol or record.strategy_id != "candidate_c"
                or not record.remote_submission_finalized):
            return blocked
        epoch = epoch_store.get(record.intent_id)
        if (epoch.entry_intent_id != record.intent_id or epoch.account_id != record.account_id
                or epoch.symbol != symbol or epoch.side != record.requested_side):
            return blocked
        if client.fetch_position() is not None:
            return blocked
        orders = client.exchange.fetch_open_orders(symbol)
        algos = client.fetch_pending_protection_algo_ids()
        if orders is None or orders or algos is None:
            return blocked
        if not set(algos).issubset(set(record.protective_algo_ids or [])):
            return blocked
        return {"allowed": True, "reason": None, "algo_ids": list(algos)}
    except Exception:
        return blocked
