"""Read exchange lifecycle evidence before every decision, with durable close replay."""
import json
import os

import candidate_c_hybrid_ownership as ownership
import candidate_c_intent_ledger as il
import candidate_c_notification_delivery as notification_delivery
import candidate_c_position_reconciliation as recon
import candidate_c_reversal_state_machine as rsm
import trade_log
import candidate_c_decision_engine as dec
import candidate_c_hybrid_live_adapter as adapter
import order_safety


def _queue_notification(cfg, state, event: dict) -> None:
    """Persist before returning from reconciliation; delivery remains fail-open."""
    store = getattr(state, "notification_store", None)
    if store is not None:
        try:
            store.enqueue(event)
        except Exception:
            logger_ = getattr(cfg, "logger", None)
            if logger_ is not None:
                logger_.warning(
                    "[%s] Candidate C 알림 상태 저장 실패 - 메모리 큐로 계속",
                    event.get("symbol"), exc_info=True,
                )
    if event not in state.notification_events:
        state.notification_events.append(event)


def _journal_rows(cfg):
    path = os.path.join(cfg.user_dir, "trades_log.jsonl")
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as source:
        return [json.loads(line) for line in source]


def _recover_terminal_close(cfg, client, symbol, state, tick_ms):
    machine = state.reversal_store.get(symbol)
    if machine.state == rsm.State.FLAT:
        return {"reason": "no_managed_epoch"}
    records = state.intent_ledger.records()
    entries = [r for r in records if r.kind == "EntryIntent" and r.state == il.IntentState.TERMINAL.value]
    if not entries:
        return {"reason": "no_managed_epoch"}
    entry = entries[-1]
    close_ids = {r.intent_id for r in records if r.kind in ("ExitIntent", "ReversalIntent")
                 and str(r.position_epoch).startswith(entry.intent_id + ":")}
    close_ids.add("external-close:" + entry.intent_id)
    if not any(row.get("type") == "close" and row.get("execution_id") in close_ids for row in _journal_rows(cfg)):
        return {"critical": True, "reason": "terminal_close_journal_missing"}
    try:
        if (client.fetch_position() is not None or client.exchange.fetch_open_orders(symbol) != []
                or client.fetch_pending_protection_algo_ids() != []):
            return {"critical": True, "reason": "terminal_flat_not_confirmed"}
    except Exception:
        return {"critical": True, "reason": "terminal_flat_UNKNOWN"}
    if getattr(cfg, "CANDIDATE_C_LIVE_EXECUTE", False):
        state.epoch_store.discard(entry.intent_id)
        machine.converge_authoritative_flat(tick_ms)
        state.reversal_store.persist(symbol, tick_ms)
    return {"reason": "terminal_close_replayed", "flat_confirmed": True}


def _recover_entry_fill(cfg, client, record, state, position, tick_ms):
    if not getattr(cfg, "CANDIDATE_C_LIVE_EXECUTE", False):
        return {"critical": True, "reason": "shadow_entry_reconciliation_observed"}
    try:
        order = client.fetch_order_status_by_client_id(record.cl_ord_id)
    except Exception:
        order = None
    if not order:
        return {"critical": True, "reason": "entry_order_UNKNOWN"}
    if (order.get("status") in ("canceled", "cancelled", "rejected")
            and order.get("filled") == 0 and position is None):
        state.intent_ledger.mark_terminal(record.intent_id)
        machine = state.reversal_store.get(record.symbol)
        if machine.state == rsm.State.ENTRY_PENDING:
            machine.observe_entry_outcome(accepted=False)
            state.reversal_store.persist(record.symbol, tick_ms)
        return {"reason": "entry_confirmed_no_fill"}
    if (not position or position.get("side") != record.requested_side
            or not order.get("average") or order.get("filled") != position.get("contracts")
            or float(order["average"]) != float(position.get("entry_price", 0))
            or order["filled"] > record.requested_quantity):
        return {"critical": True, "reason": "entry_fill_identity_UNKNOWN"}
    recovered_intent = dec.Intent(
        kind=dec.INTENT_ENTRY, account_id=record.account_id, symbol=record.symbol,
        strategy_id="candidate_c", setup_id=record.setup_id, position_epoch=None,
        config_version_id=record.config_version_id, config_hash=record.config_hash,
        decision_timestamp=tick_ms, source_candle_close_timestamp=tick_ms,
        side=record.requested_side, idempotency_key=record.cl_ord_id,
        reason_code="reconciled_entry", input_snapshot_hash=record.cl_ord_id,
        raw_stop_price=record.requested_stop_price, raw_target_price=record.requested_target_price,
    )
    # [2026-09-16, 사용자 직접 지시 - Candidate C 전용 GPT ON/OFF] 이 경로는 재시작
    # 전에 이미 제출된 주문을 복구하는 것이라, 그 순간 실제로 어느 게이트가
    # 허용했는지(GPT 승인 vs 규칙 기반)를 장부에서 재구성할 방법이 없다(장부
    # 스키마에 게이트 모드를 새로 추가하는 것은 사용자가 명시적으로 범위 밖으로
    # 정한 "회계 시스템 재작성"에 해당한다). "approved"는 이 함수가 원래부터
    # 항상 쓰던 값을 그대로 보존하는 것뿐이며, 순수 관찰용 필드라 매매 판단에는
    # 전혀 영향이 없다 - 재시작 순간에 마침 미해결이던 극히 드문 주문만 통계상
    # GPT 모드로 표시될 수 있다는 좁은 한계를 그대로 인정한다.
    result = adapter._verify_and_finalize_entry(
        cfg, client, recovered_intent, {}, record.requested_quantity * record.contract_size,
        order, state.intent_ledger, state.epoch_store, record, gate_result="approved",
    )
    if result.get("executed") and result.get("notification_event"):
        _queue_notification(cfg, state, result["notification_event"])
    return result if result.get("executed") else {"critical": True, "reason": "entry_protection_UNKNOWN"}


def reconcile_managed_position(cfg, client, symbol, state, tick_ms):
    """Caller holds account lock. Shadow returns observations without execution writes."""
    live_mode = getattr(cfg, "CANDIDATE_C_LIVE_EXECUTE", False)
    if live_mode:
        management = adapter.reconcile_pending_management(
            cfg, client, state.intent_ledger, state.epoch_store,
        )
        if management.get("notification_event"):
            _queue_notification(cfg, state, management["notification_event"])
        if management.get("pending") and not management.get("flat_observed"):
            return {"critical": True, "reason": "management_reconciliation_UNKNOWN"}
    record, ambiguous = state.intent_ledger.find_protected_entry()
    if record is None:
        candidates = [r for r in state.intent_ledger.pending_intents()
                      if r.kind == "EntryIntent"]
        if len(candidates) == 1:
            record = candidates[0]
        elif len(candidates) > 1:
            ambiguous = True
    if ambiguous:
        return {"critical": True, "reason": "ledger_ownership_UNKNOWN"}
    if record is None:
        return _recover_terminal_close(cfg, client, symbol, state, tick_ms)
    epoch = state.epoch_store.get(record.intent_id)
    try:
        position = client.fetch_position()
        orders = client.exchange.fetch_open_orders(symbol)
        algos = client.fetch_pending_protection_algo_ids()
        if orders is None or algos is None:
            raise ValueError("exchange order status UNKNOWN")
    except Exception:
        return {"critical": True, "reason": "exchange_state_UNKNOWN"}
    if not epoch.entry_intent_id:
        recovered = _recover_entry_fill(cfg, client, record, state, position, tick_ms)
        if recovered.get("critical") or not recovered.get("executed"):
            return recovered
        record = state.intent_ledger.get(record.intent_id)
        epoch = state.epoch_store.get(record.intent_id)
    if not record.remote_submission_finalized:
        try:
            entry_order = client.fetch_order_status_by_client_id(record.cl_ord_id)
        except Exception:
            entry_order = None
        if (not entry_order
                or entry_order.get("status") not in ("closed", "filled", "canceled", "cancelled", "rejected")
                or entry_order.get("filled") != epoch.original_contracts):
            return {"critical": True, "reason": "entry_remainder_UNKNOWN"}
        if live_mode:
            state.intent_ledger.mark_remote_submission_finalized(record.intent_id)
            record = state.intent_ledger.get(record.intent_id)
    if position is not None:
        # allow_missing_protection=True(2026-09-16, 사용자 직접 지시 - 상태6 경우A
        # 최소 수정) - 체결 확인 후 mark_protected() 전에 재시작하면 find_protected_
        # entry()가 이 항목을 못 찾는다(아직 PROTECTED 상태가 아니므로). 이 플래그가
        # 있어야 그 PROTECTION_PENDING 레코드까지 후보로 본다 - "보호 없음"과
        # "보호는 있는데 아직 기록 전"을 이 함수 하나로 같이 처리한다(둘 다 기존
        # ownership 함수 계약 안에서 이미 다루던 것과 같은 조합, 새 판정 로직 아님).
        owned = ownership.validate_candidate_c_position_owner(
            client, symbol, state.intent_ledger, state.epoch_store, allow_missing_protection=True,
        )
        if not owned["allowed"]:
            return {"critical": True, "reason": owned["reason"]}
        catch_up_ids = owned.get("needs_protected_catch_up")
        if catch_up_ids:
            # 실제 거래소 보호주문이 우리 것으로 확인됐지만 ledger 자신의 기록만
            # 아직 못 따라간 상태였다 - 지금 확인된 값으로만 뒤늦게 기록한다(새
            # 보호주문을 만들지 않는다 - 이미 있는 것을 인식만 늦게 하는 것).
            state.intent_ledger.mark_protected(record.intent_id, catch_up_ids)
        try:
            verification = order_safety.verify_protection(
                client, epoch.side, epoch.remaining_contracts,
                expected_sl_price=epoch.current_stop_price, expected_tp_price=epoch.target_price,
            )
        except Exception:
            return {"critical": True, "reason": "protection_detail_UNKNOWN"}
        if not verification["ok"]:
            return {"critical": True, "reason": "protection_" + verification.get("reason", "UNKNOWN")}
        store = getattr(state, "notification_store", None)
        should_recover_entry = bool(catch_up_ids)
        if store is not None:
            try:
                should_recover_entry = not store.contains("entry", record.intent_id)
            except Exception:
                logger_ = getattr(cfg, "logger", None)
                if logger_ is not None:
                    logger_.warning(
                        "[%s] Candidate C 진입 알림 상태 확인 실패 - 중복 방지로 전송 보류",
                        symbol, exc_info=True,
                    )
                should_recover_entry = False
        if should_recover_entry:
            try:
                event = notification_delivery.trade_event(
                    cfg.user_dir, "entry", record.intent_id, epoch,
                )
                if event is not None:
                    _queue_notification(cfg, state, event)
            except Exception:
                logger_ = getattr(cfg, "logger", None)
                if logger_ is not None:
                    logger_.warning(
                        "[%s] Candidate C 진입 알림 복원 실패",
                        symbol, exc_info=True,
                    )
        machine = state.reversal_store.get(symbol)
        if live_mode:
            if machine.state == rsm.State.FLAT:
                machine.request_entry(epoch.side)
                machine.confirm_entry_filled()
            else:
                machine.reconcile_authoritative_position(authoritative_side=epoch.side, tick_index=tick_ms)
            state.reversal_store.persist(symbol, tick_ms)
        return {"reason": "owned_position_reconciled", "current_position": recon.build_candidate_c_position_state(epoch)}
    if not live_mode:
        return {"reason": "shadow_external_flat_observed", "observed_flat": True}
    if any(r.kind == "StopUpdateIntent" for r in state.intent_ledger.pending_intents()):
        # A timed-out attachment may arrive after the position was safety-closed.
        # Flatness alone cannot retire that submission or its parent's reservation.
        return {"critical": True, "reason": "stop_submission_UNKNOWN"}
    if orders:
        return {"critical": True, "reason": "flat_with_pending_orders"}
    if algos:
        proof = ownership.validate_candidate_c_orphan_protection_owner(
            client, symbol, state.intent_ledger, state.epoch_store,
        )
        if not proof["allowed"]:
            return {"critical": True, "reason": "flat_with_foreign_protection"}
        client.cancel_protection(proof["algo_ids"])
        if client.fetch_pending_protection_algo_ids() != []:
            return {"critical": True, "reason": "flat_cleanup_UNKNOWN"}

    actions = [r for r in state.intent_ledger.pending_intents()
               if r.kind in ("ExitIntent", "ReversalIntent")
               and str(r.position_epoch).startswith(record.intent_id + ":")]
    action_execution_id = (
        actions[0].intent_id if len(actions) == 1
        else "external-close:" + record.intent_id
    )
    journal_rows = _journal_rows(cfg)
    if any(
        row.get("type") == "close"
        and row.get("execution_id") == action_execution_id
        for row in journal_rows
    ):
        execution_id = action_execution_id
    elif any(row.get("execution_id") == action_execution_id for row in journal_rows):
        # One ExitIntent can first produce a partial-reduce row, then become flat.
        # trade_log execution IDs are globally idempotent, so the final close needs
        # its own deterministic ID or record_close() would silently deduplicate it.
        execution_id = "external-close:" + record.intent_id
    else:
        execution_id = action_execution_id
    journaled = any(
        row.get("type") == "close" and row.get("execution_id") == execution_id
        for row in journal_rows
    )
    if journaled:
        store = getattr(state, "notification_store", None)
        should_recover = store is None
        if store is not None:
            try:
                should_recover = not store.contains("close", execution_id)
            except Exception:
                logger_ = getattr(cfg, "logger", None)
                if logger_ is not None:
                    logger_.warning(
                        "[%s] Candidate C 청산 알림 상태 확인 실패 - 중복 방지로 전송 보류",
                        symbol, exc_info=True,
                    )
        if should_recover:
            try:
                event = notification_delivery.trade_event(
                    cfg.user_dir, "close", execution_id, epoch,
                )
                if event is not None:
                    _queue_notification(cfg, state, event)
            except Exception:
                logger_ = getattr(cfg, "logger", None)
                if logger_ is not None:
                    logger_.warning(
                        "[%s] Candidate C 청산 알림 복원 실패",
                        symbol, exc_info=True,
                    )
    if not journaled:
        try:
            resolved = client.fetch_realized_close_for_position(
                epoch.side, epoch.raw_entry_price, epoch.exchange_position_id,
                epoch.exchange_entry_timestamp_ms,
            )
        except Exception:
            resolved = None
        if resolved is None or resolved.get("source") != "okx_realized":
            return {"critical": True, "reason": "external_close_pnl_UNKNOWN"}
        # History describes the entire position; previous reductions are separate rows.
        gross = resolved["gross_pnl"] - epoch.realized_partial_pnl_usdt
        fee = max(0., resolved["fee"] - epoch.realized_partial_fee_usdt)
        net = resolved.get("net_pnl")
        if net is not None:
            net -= epoch.realized_partial_pnl_usdt - epoch.realized_partial_fee_usdt
        classification = recon.classify_external_position_change(
            epoch=epoch, exchange_position=None, owned_algo_live=False,
            exit_price=resolved.get("exit_price"),
        )
        trade_log.record_close(
            cfg.user_dir, symbol, epoch.side, epoch.raw_entry_price, epoch.remaining_contracts,
            gross, classification, False, fee=fee, pnl_source="okx_realized",
            close_price=resolved.get("exit_price"), funding_fee=resolved.get("funding_fee"),
            okx_net_pnl=net,
            strategy_group="candidate_c", execution_id=execution_id,
        )
        try:
            event = notification_delivery.trade_event(
                cfg.user_dir, "close", execution_id, epoch,
            )
            if event is not None:
                _queue_notification(cfg, state, event)
        except Exception:
            logger_ = getattr(cfg, "logger", None)
            if logger_ is not None:
                logger_.warning(
                    "[%s] Candidate C 외부 청산 알림 생성 실패",
                    symbol, exc_info=True,
                )
    # A journal replay after crash completes the same transition, with no duplicate PnL.
    state.intent_ledger.mark_terminal(record.intent_id)
    for action in actions:
        state.intent_ledger.mark_terminal(action.intent_id)
    state.epoch_store.discard(record.intent_id)
    machine = state.reversal_store.get(symbol)
    machine.converge_authoritative_flat(tick_ms)
    state.reversal_store.persist(symbol, tick_ms)
    return {"reason": "external_flat_reconciled", "flat_confirmed": True}
